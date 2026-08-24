"""The semantic compiler: MetricRequest → validated IR → SQLGlot → dialect SQL.

Compilation is pure and deterministic: the same request produces byte-identical
SQL, which is what makes the golden SQL snapshots and the dual-engine parity
suite meaningful (§8.3).

Fan-out safety is the property this module exists to guarantee: when a request
mixes metrics from facts at different grains, each fact is aggregated in its own
CTE and the results are joined on shared dimensions. A naive multi-fact join
would silently multiply cost figures.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from snowobs_common.errors import AppError
from snowobs_semantics.dialect_shims import Dialect, apply_shims
from snowobs_semantics.model import Entity, Metric, SemanticModel, TimeGrain, default_model

DEFAULT_LIMIT = 10_000
MAX_LIMIT = 50_000
TIME_COLUMN_ALIAS = "TIME_BUCKET"


class CompilationError(AppError):
    status_code = 400
    title = "Metric request could not be compiled"
    problem_type = "https://snowobs.dev/problems/compilation"


class FilterOperator(StrEnum):
    EQ = "eq"
    NEQ = "neq"
    IN = "in"
    NOT_IN = "not_in"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    CONTAINS = "contains"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


class Filter(BaseModel):
    model_config = ConfigDict(frozen=True)

    dimension: str
    operator: FilterOperator = FilterOperator.EQ
    value: str | int | float | bool | list[str] | None = None

    @model_validator(mode="after")
    def _value_required(self) -> Filter:
        if self.operator in (FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL):
            return self
        if self.value is None:
            raise ValueError(f"operator {self.operator} requires a value")
        if self.operator in (FilterOperator.IN, FilterOperator.NOT_IN) and not isinstance(
            self.value, list
        ):
            raise ValueError(f"operator {self.operator} requires a list value")
        return self


class TimeRange(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> TimeRange:
        if self.end < self.start:
            raise ValueError("time range end precedes start")
        return self


class Order(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: str
    descending: bool = True


class MetricRequest(BaseModel):
    """What a dashboard tile or an agent tool asks for."""

    model_config = ConfigDict(frozen=True)

    metrics: list[str]
    dimensions: list[str] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)
    time_range: TimeRange | None = None
    grain: TimeGrain | None = None
    limit: int = DEFAULT_LIMIT
    order: list[Order] = Field(default_factory=list)
    #: Group by the time grain. False for a single-figure tile, which wants one
    #: total for the period rather than the largest day within it. The time
    #: *filter* still applies either way.
    bucket_time: bool = True
    #: Row-level security predicates injected server-side (§17) — never from
    #: the browser, and never overridable by a caller-supplied filter.
    rls_filters: list[Filter] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sane(self) -> MetricRequest:
        if not self.metrics:
            raise ValueError("at least one metric is required")
        if self.limit < 1 or self.limit > MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
        return self


@dataclass(frozen=True)
class CompiledQuery:
    """Compiler output: the SQL plus everything R5 needs to explain it."""

    sql: str
    dialect: Dialect
    sources_used: list[str]
    metrics: list[str]
    dimensions: list[str]
    columns: list[str]
    latency_floor_minutes: int
    provisional: bool
    limit: int
    fingerprint: str
    entities_used: list[str] = field(default_factory=list)
    #: The sources that actually gate this figure's completeness, which is not
    #: the same set as ``sources_used``: an entity view may join a slow source
    #: for a column this query never selects. Without this, a caller looking up
    #: the latency of everything in ``sources_used`` would report a query as
    #: eight hours stale when it is final in forty-five minutes.
    gating_sources: list[str] = field(default_factory=list)

    @property
    def cache_key(self) -> str:
        return self.fingerprint


def _quote(identifier: str) -> str:
    if not identifier.replace("_", "").isalnum():
        raise CompilationError(f"Unsafe identifier: {identifier!r}")
    return f'"{identifier.upper()}"'


def _literal(value: str | int | float | bool) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return str(value)
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def _time_expression(entity: Entity, grain: TimeGrain) -> str:
    if entity.time_column is None:
        raise CompilationError(f"Entity {entity.id} has no time column to group by")
    return f"TS_TRUNC('{grain.value}', {_quote(entity.time_column)})"


def _render_filter(entity: Entity, model: SemanticModel, filter_: Filter) -> str:
    expression = _dimension_expression(entity, model, filter_.dimension)
    match filter_.operator:
        case FilterOperator.EQ:
            return f"{expression} = {_literal(filter_.value)}"  # type: ignore[arg-type]
        case FilterOperator.NEQ:
            return f"{expression} <> {_literal(filter_.value)}"  # type: ignore[arg-type]
        case FilterOperator.IN | FilterOperator.NOT_IN:
            values = filter_.value if isinstance(filter_.value, list) else [filter_.value]
            if not values:
                # An empty allowlist selects nothing — never everything. This is
                # the RLS failure mode that leaks another team's costs.
                return "FALSE" if filter_.operator is FilterOperator.IN else "TRUE"
            rendered = ", ".join(_literal(v) for v in values)  # type: ignore[arg-type]
            keyword = "IN" if filter_.operator is FilterOperator.IN else "NOT IN"
            return f"{expression} {keyword} ({rendered})"
        case FilterOperator.GT:
            return f"{expression} > {_literal(filter_.value)}"  # type: ignore[arg-type]
        case FilterOperator.GTE:
            return f"{expression} >= {_literal(filter_.value)}"  # type: ignore[arg-type]
        case FilterOperator.LT:
            return f"{expression} < {_literal(filter_.value)}"  # type: ignore[arg-type]
        case FilterOperator.LTE:
            return f"{expression} <= {_literal(filter_.value)}"  # type: ignore[arg-type]
        case FilterOperator.CONTAINS:
            pattern = str(filter_.value).replace("'", "''")
            return f"{expression} LIKE '%{pattern}%'"
        case FilterOperator.IS_NULL:
            return f"{expression} IS NULL"
        case _:
            return f"{expression} IS NOT NULL"


def _dimension_expression(entity: Entity, model: SemanticModel, name: str) -> str:
    dimension = entity.dimension(name)
    if dimension is not None:
        return dimension.expression
    for join in entity.joins:
        joined = model.entities.get(join.entity)
        if joined is None:
            continue
        joined_dimension = joined.dimension(name)
        if joined_dimension is not None:
            return joined_dimension.expression
    raise CompilationError(f"Dimension '{name}' is not available on entity '{entity.id}'")


class SemanticCompiler:
    """Compiles :class:`MetricRequest` into dialect SQL."""

    def __init__(self, model: SemanticModel | None = None) -> None:
        self.model = model or default_model()

    # ------------------------------------------------------------- public API
    def compile(self, request: MetricRequest, dialect: Dialect) -> CompiledQuery:
        metrics = [self.model.metric(metric_id) for metric_id in request.metrics]
        by_entity = self._group_by_entity(metrics)
        grain = request.grain or self._common_grain(metrics)

        if len(by_entity) == 1:
            entity_id, entity_metrics = next(iter(by_entity.items()))
            sql = self._single_entity_sql(
                self.model.entity(entity_id), entity_metrics, request, grain
            )
        else:
            sql = self._multi_entity_sql(by_entity, request, grain)

        sql = apply_shims(sql, dialect)
        return self._describe(sql, dialect, metrics, request, grain, list(by_entity))

    def compile_both(self, request: MetricRequest) -> dict[Dialect, CompiledQuery]:
        """Compile for both engines — the parity harness's entry point."""
        return {dialect: self.compile(request, dialect) for dialect in Dialect}

    # ------------------------------------------------------------- internals
    def _group_by_entity(self, metrics: Sequence[Metric]) -> dict[str, list[Metric]]:
        grouped: dict[str, list[Metric]] = {}
        for metric in metrics:
            grouped.setdefault(metric.entity, []).append(metric)
        return grouped

    @staticmethod
    def _common_grain(metrics: Sequence[Metric]) -> TimeGrain:
        # The coarsest declared grain wins: aggregating a daily metric to hourly
        # would invent precision the source does not have.
        order = [TimeGrain.HOUR, TimeGrain.DAY, TimeGrain.WEEK, TimeGrain.MONTH]
        return max((m.grain for m in metrics), key=order.index)

    def _select_list(
        self, entity: Entity, metrics: Sequence[Metric], dimensions: Sequence[str]
    ) -> tuple[list[str], list[str]]:
        """Return (select expressions, group-by expressions)."""
        selects: list[str] = []
        groups: list[str] = []
        for name in dimensions:
            expression = _dimension_expression(entity, self.model, name)
            selects.append(f"{expression} AS {_quote(name)}")
            groups.append(expression)
        for metric in metrics:
            selects.append(f"{metric.expression} AS {_quote(metric.id.replace('.', '_'))}")
        return selects, groups

    def _where(self, entity: Entity, request: MetricRequest) -> list[str]:
        """Predicates for one entity.

        The time *filter* is independent of time *bucketing*: a single-figure
        tile does not group by day, but it must still be restricted to the
        requested period — otherwise it silently totals all of history.
        """
        predicates: list[str] = []
        if request.time_range and entity.time_column:
            column = _quote(entity.time_column)
            predicates.append(
                f"{column} >= {_literal(request.time_range.start.isoformat())} "
                f"AND {column} <= {_literal(request.time_range.end.isoformat())}"
            )
        # RLS predicates are applied first and cannot be removed by a caller.
        for filter_ in [*request.rls_filters, *request.filters]:
            predicates.append(_render_filter(entity, self.model, filter_))
        return predicates

    def _entity_cte(
        self,
        entity: Entity,
        metrics: Sequence[Metric],
        request: MetricRequest,
        grain: TimeGrain,
        dimensions: Sequence[str],
        *,
        bucket_time: bool,
    ) -> str:
        selects, groups = self._select_list(entity, metrics, dimensions)
        if bucket_time and entity.time_column:
            time_expression = _time_expression(entity, grain)
            selects.insert(0, f"{time_expression} AS {_quote(TIME_COLUMN_ALIAS)}")
            groups.insert(0, time_expression)

        sql = "SELECT\n  " + ",\n  ".join(selects) + f"\nFROM (\n{_indent(entity.sql)}\n) AS base"
        predicates = self._where(entity, request)
        if predicates:
            sql += "\nWHERE " + "\n  AND ".join(f"({p})" for p in predicates)
        if groups:
            sql += "\nGROUP BY " + ", ".join(groups)
        return sql

    def _single_entity_sql(
        self,
        entity: Entity,
        metrics: Sequence[Metric],
        request: MetricRequest,
        grain: TimeGrain,
    ) -> str:
        bucket_time = request.bucket_time and entity.time_column is not None
        sql = self._entity_cte(
            entity, metrics, request, grain, request.dimensions, bucket_time=bucket_time
        )
        sql += self._order_and_limit(request, metrics)
        return sql

    def _multi_entity_sql(
        self,
        by_entity: dict[str, list[Metric]],
        request: MetricRequest,
        grain: TimeGrain,
    ) -> str:
        """Fan-out-safe composition: one aggregate CTE per fact, joined on keys.

        Joining two facts directly would repeat each row of the coarser fact for
        every row of the finer one and double the cost figures. Aggregating each
        to the shared grain first makes that impossible.
        """
        keys: list[str] = []
        if request.bucket_time and any(self.model.entity(e).time_column for e in by_entity):
            keys.append(TIME_COLUMN_ALIAS)
        keys.extend(request.dimensions)
        if not keys:
            raise CompilationError(
                "Metrics from different entities need at least one shared dimension "
                "or a time grain to join on"
            )

        ctes: list[str] = []
        cte_names: list[str] = []
        for index, (entity_id, metrics) in enumerate(by_entity.items()):
            entity = self.model.entity(entity_id)
            missing = [d for d in request.dimensions if not _has_dimension(self.model, entity, d)]
            if missing:
                raise CompilationError(
                    f"Entity '{entity_id}' cannot be sliced by {missing}; drop the dimension "
                    f"or the metrics that depend on it"
                )
            name = f"agg_{index}"
            cte_names.append(name)
            body = self._entity_cte(
                entity,
                metrics,
                request,
                grain,
                request.dimensions,
                bucket_time=request.bucket_time and entity.time_column is not None,
            )
            ctes.append(f"{name} AS (\n{_indent(body)}\n)")

        selects = [f"{cte_names[0]}.{_quote(key)} AS {_quote(key)}" for key in keys]
        for index, metrics in enumerate(by_entity.values()):
            for metric in metrics:
                column = _quote(metric.id.replace(".", "_"))
                selects.append(f"{cte_names[index]}.{column} AS {column}")

        sql = "WITH " + ",\n".join(ctes) + "\nSELECT\n  " + ",\n  ".join(selects)
        sql += f"\nFROM {cte_names[0]}"
        for name in cte_names[1:]:
            conditions = " AND ".join(
                f"{cte_names[0]}.{_quote(key)} = {name}.{_quote(key)}" for key in keys
            )
            sql += f"\nFULL OUTER JOIN {name} ON {conditions}"
        all_metrics = [m for metrics in by_entity.values() for m in metrics]
        sql += self._order_and_limit(request, all_metrics, qualify=cte_names[0])
        return sql

    def _order_and_limit(
        self, request: MetricRequest, metrics: Sequence[Metric], *, qualify: str | None = None
    ) -> str:
        clauses: list[str] = []
        for order in request.order:
            column = _quote(order.field.replace(".", "_"))
            clauses.append(f"{column} {'DESC' if order.descending else 'ASC'}")
        if not clauses:
            first_metric = _quote(metrics[0].id.replace(".", "_"))
            clauses.append(f"{first_metric} DESC NULLS LAST")
        # Break ties deterministically on the grouping columns. Without this a
        # tile's rows can reorder between refreshes (and between engines) purely
        # because two rows share a value.
        tiebreakers = (
            [_quote(TIME_COLUMN_ALIAS)]
            if request.bucket_time and (qualify or self._has_time(metrics))
            else []
        )
        tiebreakers += [_quote(dimension) for dimension in request.dimensions]
        ordered_columns = {clause.split()[0] for clause in clauses}
        clauses += [column for column in tiebreakers if column not in ordered_columns]

        sql = ""
        if clauses:
            sql += "\nORDER BY " + ", ".join(clauses)
        sql += f"\nLIMIT {request.limit}"  # forced limit (R9)
        return sql

    def _has_time(self, metrics: Sequence[Metric]) -> bool:
        return any(self.model.entity(m.entity).time_column is not None for m in metrics)

    def _describe(
        self,
        sql: str,
        dialect: Dialect,
        metrics: Sequence[Metric],
        request: MetricRequest,
        grain: TimeGrain,
        entity_ids: list[str],
    ) -> CompiledQuery:
        sources = self.model.sources_used([m.id for m in metrics])
        latency_floor = max(m.latency_floor_minutes for m in metrics)
        provisional = self._is_provisional(metrics, request)
        columns = (
            [TIME_COLUMN_ALIAS]
            if request.bucket_time and any(self.model.entity(e).time_column for e in entity_ids)
            else []
        )
        columns += list(request.dimensions)
        columns += [m.id.replace(".", "_").upper() for m in metrics]
        fingerprint = hashlib.sha256(f"{dialect.value}|{sql}".encode()).hexdigest()[:32]
        return CompiledQuery(
            sql=sql,
            dialect=dialect,
            sources_used=sources,
            metrics=[m.id for m in metrics],
            dimensions=list(request.dimensions),
            columns=columns,
            latency_floor_minutes=latency_floor,
            provisional=provisional,
            limit=request.limit,
            fingerprint=fingerprint,
            entities_used=entity_ids,
            gating_sources=sorted({s for m in metrics for s in m.requires_sources}),
        )

    @staticmethod
    def _is_provisional(
        metrics: Sequence[Metric], request: MetricRequest, today: date | None = None
    ) -> bool:
        """True when the request reaches into a source's restatement window (§9.3).

        The window is measured from *now*, not from the request: figures for
        last January are final however recently they were asked for, while
        figures for this week restate until month close.
        """
        if not any(m.provisional_window_days > 0 for m in metrics):
            return False
        if request.time_range is None:
            # An unbounded request necessarily includes the most recent data.
            return True

        from datetime import timedelta

        reference = today or date.today()  # noqa: DTZ011 — account-date granularity
        for metric in metrics:
            if metric.provisional_window_days <= 0:
                continue
            settled_before = reference - timedelta(days=metric.provisional_window_days)
            if request.time_range.end >= settled_before:
                return True
        return False


def _has_dimension(model: SemanticModel, entity: Entity, name: str) -> bool:
    try:
        _dimension_expression(entity, model, name)
    except CompilationError:
        return False
    return True


def _indent(sql: str, spaces: int = 2) -> str:
    pad = " " * spaces
    return "\n".join(f"{pad}{line}" if line.strip() else line for line in sql.splitlines())
