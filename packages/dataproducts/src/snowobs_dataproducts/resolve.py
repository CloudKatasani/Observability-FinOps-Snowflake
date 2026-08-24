"""Resolving a product declaration against the semantic layer.

A product names metrics; the semantic layer knows which entity each metric
belongs to. Grouping the product's metrics by entity gives its **datasets** —
one published relation per entity, aggregated at the product's grain. This is
the only sound grouping: metrics from two different facts cannot share a
relation without either a fan-out join or an invented key (§8.3).

Everything in this module is a pure function of ``(product, semantic model)``.
No SQL is executed here and no artifact is written; the callers are the
contract builder and the emitters.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import cast

import sqlglot
from sqlglot import exp

from snowobs_common.errors import AppError
from snowobs_dataproducts.model import DataProduct
from snowobs_semantics.compiler import (
    MAX_LIMIT,
    TIME_COLUMN_ALIAS,
    CompiledQuery,
    MetricRequest,
    SemanticCompiler,
)
from snowobs_semantics.dialect_shims import Dialect
from snowobs_semantics.model import Entity, FormatType, Metric, SemanticModel, default_model


class ProductResolutionError(AppError):
    """The product cannot be resolved against the current semantic layer."""

    status_code = 400
    title = "Data product could not be resolved"
    problem_type = "https://snowobs.dev/problems/data-product-resolution"


class ColumnType(StrEnum):
    """Physical column types in a published product relation.

    Credits and currency are fixed-point everywhere (§27.7); there is no
    floating-point member in this enum and there must never be one.
    """

    STRING = "STRING"
    BOOLEAN = "BOOLEAN"
    DATE = "DATE"
    TIMESTAMP_LTZ = "TIMESTAMP_LTZ"
    NUMBER_COUNT = "NUMBER(38,0)"
    NUMBER_MILLIS = "NUMBER(38,3)"
    NUMBER_MONEY = "NUMBER(38,9)"
    NUMBER_RATIO = "NUMBER(38,15)"

    @property
    def is_text(self) -> bool:
        return self is ColumnType.STRING

    @property
    def is_numeric(self) -> bool:
        return self.value.startswith("NUMBER")


#: A metric's declared presentation format determines the type of its column.
#: The correspondence is exact: ``MONEY()`` and ``SAFE_RATIO()`` in the compiled
#: SQL cast to ``DECIMAL(38,9)`` and ``DECIMAL(38,15)`` respectively, counting
#: expressions are integral, and duration metrics carry sub-millisecond scale.
_FORMAT_TYPES: dict[FormatType, ColumnType] = {
    FormatType.NUMBER: ColumnType.NUMBER_MONEY,
    FormatType.CURRENCY: ColumnType.NUMBER_MONEY,
    FormatType.PERCENT: ColumnType.NUMBER_RATIO,
    FormatType.INTEGER: ColumnType.NUMBER_COUNT,
    FormatType.BYTES: ColumnType.NUMBER_COUNT,
    FormatType.DURATION_MS: ColumnType.NUMBER_MILLIS,
}

#: Shim functions whose result type is known without evaluating the expression.
_SHIM_TYPES: dict[str, ColumnType] = {
    "MONEY": ColumnType.NUMBER_MONEY,
    "SAFE_RATIO": ColumnType.NUMBER_RATIO,
    "TS_PARSE": ColumnType.TIMESTAMP_LTZ,
    "TS_TRUNC": ColumnType.TIMESTAMP_LTZ,
    "EPOCH_SECONDS": ColumnType.NUMBER_COUNT,
    "DATE_DIFF_DAYS": ColumnType.NUMBER_COUNT,
    "JSON_GET": ColumnType.STRING,
    "REGEX_CONTAINS": ColumnType.BOOLEAN,
}


def metric_column(metric_id: str) -> str:
    """The column a metric occupies in a published relation."""
    return metric_id.replace(".", "_").upper()


def is_additive(metric: Metric) -> bool:
    """Whether the metric survives being summed again over the product's grain.

    ``SUM`` and plain ``COUNT`` roll up; ratios, percentiles, averages, and
    ``COUNT(DISTINCT …)`` do not. The semantic view emitter relies on this: a
    non-additive figure is published as a row-level fact, never as a metric a
    consumer's tool will silently re-aggregate into a wrong number.
    """
    expression = " ".join(metric.expression.split()).upper()
    if expression.startswith("COUNT(DISTINCT") or expression.startswith("COUNT( DISTINCT"):
        return False
    return expression.startswith(("SUM(", "COUNT("))


@dataclass(frozen=True)
class DatasetSpec:
    """One published relation: the product's metrics for a single entity."""

    product_id: str
    entity_id: str
    metric_ids: tuple[str, ...]
    #: Product dimensions this entity can resolve, in the product's order.
    dimensions: tuple[str, ...]
    #: True when the entity has a time column, so the relation carries a bucket.
    bucketed: bool

    @property
    def suffix(self) -> str:
        for prefix in ("fact_", "dim_"):
            if self.entity_id.startswith(prefix):
                return self.entity_id.removeprefix(prefix)
        return self.entity_id

    @property
    def view_name(self) -> str:
        """The secure view name in the ``PUBLISHED`` schema."""
        return f"V_{self.product_id}_{self.suffix}".upper()

    @property
    def dbt_model(self) -> str:
        return f"{self.product_id}_{self.suffix}".lower()

    @property
    def semantic_alias(self) -> str:
        """The table alias used inside the semantic view."""
        return self.suffix.lower()

    @property
    def grain(self) -> tuple[str, ...]:
        keys = (TIME_COLUMN_ALIAS,) if self.bucketed else ()
        return keys + tuple(d.upper() for d in self.dimensions)


def resolve_datasets(product: DataProduct, model: SemanticModel | None = None) -> list[DatasetSpec]:
    """Group a product's metrics into one dataset per entity.

    Order is deterministic: entities appear in the order their first metric is
    declared, which is what makes the emitted artifacts byte-stable.
    """
    resolved = model or default_model()
    grouped: dict[str, list[str]] = {}
    for metric_id in product.metrics:
        try:
            metric = resolved.metric(metric_id)
        except Exception as exc:  # ConfigurationError — re-raised with product context
            raise ProductResolutionError(
                f"Product {product.id} references unknown metric '{metric_id}'"
            ) from exc
        grouped.setdefault(metric.entity, []).append(metric_id)

    specs: list[DatasetSpec] = []
    for entity_id, metric_ids in grouped.items():
        entity = resolved.entity(entity_id)
        dimensions = tuple(
            name
            for name in product.dimensions
            if _resolves(resolved, entity, name) and not _is_time_column(entity, resolved, name)
        )
        specs.append(
            DatasetSpec(
                product_id=product.id,
                entity_id=entity_id,
                metric_ids=tuple(metric_ids),
                dimensions=dimensions,
                bucketed=entity.time_column is not None,
            )
        )
    return specs


def _resolves(model: SemanticModel, entity: Entity, dimension: str) -> bool:
    if dimension in entity.dimension_names:
        return True
    return any(
        dimension in model.entities[join.entity].dimension_names
        for join in entity.joins
        if join.entity in model.entities
    )


def _is_time_column(entity: Entity, model: SemanticModel, dimension: str) -> bool:
    """True when the dimension is just the entity's time column under another name.

    Publishing it beside the time bucket would put the same value in two columns
    and double the apparent grain.
    """
    if entity.time_column is None:
        return False
    reference = entity.dimension(dimension)
    if reference is None:
        return False
    return reference.expression.strip().strip('"').upper() == entity.time_column.upper()


def compile_dataset(
    spec: DatasetSpec,
    dialect: Dialect = Dialect.SNOWFLAKE,
    model: SemanticModel | None = None,
    *,
    account: str | None = None,
) -> CompiledQuery:
    """Compile the dataset's metrics through the semantic compiler (R1).

    The product layer never writes SQL: it asks the compiler for the same SQL a
    dashboard tile would get, which is what makes "show the SQL" on a published
    artifact and on a dashboard the same answer (R5).
    """
    compiler = SemanticCompiler(model or default_model())
    request = MetricRequest(
        metrics=list(spec.metric_ids),
        dimensions=list(spec.dimensions),
        bucket_time=spec.bucketed,
        limit=MAX_LIMIT,
        account_context=account,
    )
    return compiler.compile(request, dialect)


def unbounded_sql(compiled: CompiledQuery) -> str:
    """The compiled SQL with the guard's row cap and ordering removed.

    The forced ``LIMIT`` exists to bound *interactive execution* (R9). Baking it
    into a published view would silently truncate the product at fifty thousand
    rows, so the emitted DDL drops it — and drops the presentation ordering with
    it, which a relation has no business carrying.
    """
    statement = sqlglot.parse_one(compiled.sql, read=compiled.dialect.value)
    statement.set("limit", None)
    statement.set("order", None)
    return statement.sql(dialect=compiled.dialect.value, pretty=True)


def dimension_type(
    entity: Entity, dimension: str, model: SemanticModel | None = None
) -> ColumnType:
    """Infer a dimension column's physical type from the entity's own SQL.

    The entity's SELECT is the definition of the column, so it is what we read —
    a ``CAST(x AS DATE)`` projection is a ``DATE``, a ``MONEY()`` projection is
    ``NUMBER(38,9)``. Anything we cannot type from the projection is a string,
    which is what every un-cast usage-view column actually is.
    """
    resolved = model or default_model()
    reference = entity.dimension(dimension)
    owning_entity = entity
    if reference is None:
        for join in entity.joins:
            joined = resolved.entities.get(join.entity)
            if joined is not None and joined.dimension(dimension) is not None:
                reference, owning_entity = joined.dimension(dimension), joined
                break
    if reference is None:
        raise ProductResolutionError(
            f"Dimension '{dimension}' is not available on entity '{entity.id}'"
        )
    return _classify(reference.expression, owning_entity)


def time_bucket_type(entity: Entity) -> ColumnType:
    """The type of ``TIME_BUCKET`` for an entity: truncation preserves the type."""
    if entity.time_column is None:
        raise ProductResolutionError(f"Entity {entity.id} has no time column")
    inferred = _classify(entity.time_column, entity)
    return (
        inferred
        if inferred in (ColumnType.DATE, ColumnType.TIMESTAMP_LTZ)
        else (ColumnType.TIMESTAMP_LTZ)
    )


def metric_type(metric: Metric) -> ColumnType:
    return _FORMAT_TYPES[metric.format.type]


@lru_cache(maxsize=64)
def _projections(entity_sql: str) -> dict[str, str]:
    """alias (upper) → the SQL of the expression it aliases, for one entity."""
    statement = sqlglot.parse_one(entity_sql, read="snowflake")
    select = statement.find(exp.Select)
    if select is None:
        return {}
    projections: dict[str, str] = {}
    for projection in select.expressions:
        alias = projection.alias_or_name
        if not alias:
            continue
        target = projection.this if isinstance(projection, exp.Alias) else projection
        projections[alias.upper()] = target.sql(dialect="snowflake")
    return projections


def _classify(expression: str, entity: Entity) -> ColumnType:
    """Type an expression written against an entity's projection list."""
    text = expression.strip()
    bare = text.strip('"').upper()
    projections = _projections(entity.sql)
    if bare in projections and projections[bare].strip('"').upper() != bare:
        text = projections[bare]
    try:
        # ``parse_one`` is annotated with a bound type variable that mypy resolves
        # to the narrower ``Expr``; the repo widens it at the parse site.
        node = cast(exp.Expression, sqlglot.parse_one(text, read="snowflake"))
    except Exception:
        return ColumnType.STRING
    return _classify_node(node)


def _classify_node(node: exp.Expression) -> ColumnType:
    if isinstance(node, exp.Cast):
        return _classify_cast(node)
    if isinstance(node, exp.Anonymous):
        return _SHIM_TYPES.get(str(node.this).upper(), ColumnType.STRING)
    if isinstance(node, exp.Case):
        branches = [_classify_node(branch.args["true"]) for branch in node.args.get("ifs", [])]
        distinct = {branch for branch in branches if branch is not ColumnType.STRING}
        return next(iter(distinct)) if len(distinct) == 1 else ColumnType.STRING
    if isinstance(node, exp.Coalesce | exp.Paren):
        inner = node.this
        return _classify_node(inner) if isinstance(inner, exp.Expression) else ColumnType.STRING
    if isinstance(node, exp.Boolean | exp.EQ | exp.NEQ | exp.GT | exp.LT | exp.GTE | exp.LTE):
        return ColumnType.BOOLEAN
    if isinstance(node, exp.Literal):
        return ColumnType.NUMBER_COUNT if node.is_int else ColumnType.STRING
    return ColumnType.STRING


def _classify_cast(node: exp.Cast) -> ColumnType:
    target = node.to
    kind = target.this
    if kind in (exp.DataType.Type.DATE,):
        return ColumnType.DATE
    if kind in (
        exp.DataType.Type.TIMESTAMP,
        exp.DataType.Type.TIMESTAMPLTZ,
        exp.DataType.Type.TIMESTAMPNTZ,
        exp.DataType.Type.TIMESTAMPTZ,
        exp.DataType.Type.DATETIME,
    ):
        return ColumnType.TIMESTAMP_LTZ
    if kind in (exp.DataType.Type.BOOLEAN,):
        return ColumnType.BOOLEAN
    if kind in (exp.DataType.Type.DECIMAL, exp.DataType.Type.DOUBLE, exp.DataType.Type.FLOAT):
        scale = _decimal_scale(target)
        if scale is None or scale == 0:
            return ColumnType.NUMBER_COUNT
        return ColumnType.NUMBER_RATIO if scale > 9 else ColumnType.NUMBER_MONEY
    if kind in (exp.DataType.Type.INT, exp.DataType.Type.BIGINT, exp.DataType.Type.SMALLINT):
        return ColumnType.NUMBER_COUNT
    return ColumnType.STRING


def _decimal_scale(target: exp.DataType) -> int | None:
    parameters = target.expressions
    if len(parameters) < 2:
        return None
    try:
        return int(parameters[1].name)
    except (TypeError, ValueError):
        return None
