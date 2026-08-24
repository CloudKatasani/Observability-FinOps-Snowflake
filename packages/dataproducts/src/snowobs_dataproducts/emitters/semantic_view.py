"""``CREATE SEMANTIC VIEW`` for a data product (BUILD_PROMPT §13.4).

Cortex Analyst reads a semantic view to turn a question into SQL. Without one
it guesses joins; with a bad one it guesses arithmetic. Two rules govern what
this emitter produces:

* **Only additive figures become METRICS.** ``SUM`` and ``COUNT`` survive being
  re-aggregated over a coarser grain; a ratio, a percentile, or a distinct count
  does not. Non-additive columns are published as row-level ``FACTS``, so no tool
  can silently average a percentage of percentages into a wrong number (R12).
* **Verified queries are generated, never invented.** Each one selects from the
  product's own published view, so a verified query cannot drift from the
  relation it claims to demonstrate.

Clause order follows the current ``CREATE SEMANTIC VIEW`` grammar: ``TABLES``,
``RELATIONSHIPS``, ``FACTS``, ``DIMENSIONS``, ``METRICS``, ``COMMENT``, then
``AI_VERIFIED_QUERIES`` — the clause is spelled ``AI_VERIFIED_QUERIES``, not
``VERIFIED_QUERIES`` (ASSUMPTIONS §6, A-8).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from snowobs_dataproducts.contracts import ContractColumn, ContractDataset, DataContract
from snowobs_dataproducts.emitters import (
    EmitterError,
    SnowflakeTarget,
    header,
    ident,
    literal,
    one_line,
)
from snowobs_dataproducts.model import DataProduct
from snowobs_dataproducts.resolve import is_additive
from snowobs_semantics.compiler import TIME_COLUMN_ALIAS
from snowobs_semantics.model import SemanticModel, default_model

#: How far back a generated verified query looks.
VERIFIED_QUERY_WINDOW_DAYS = 30
#: How many rows a generated verified query returns.
VERIFIED_QUERY_LIMIT = 20


@dataclass(frozen=True)
class VerifiedQuery:
    """One gold-standard question and the SQL that answers it."""

    name: str
    question: str
    sql: str
    onboarding: bool


def emit_semantic_view(
    product: DataProduct,
    contract: DataContract,
    model: SemanticModel | None = None,
    *,
    target: SnowflakeTarget | None = None,
) -> str:
    """Render the product's semantic view, verified queries included."""
    resolved_model = model or default_model()
    resolved = target or SnowflakeTarget()
    queries = verified_queries(product, contract, resolved_model, target=resolved)
    if not queries:
        raise EmitterError(
            f"{product.id}: a semantic view without verified queries is a guessing "
            f"machine; every metric must contribute at least one question"
        )

    name = resolved.qualified(resolved.semantic_schema, product.slug_upper)
    body = [
        header(
            f"{product.name} {product.version} — semantic view",
            [
                "Cortex Analyst reads this view to answer questions about the product.",
                f"{len(queries)} verified queries teach it the team's vocabulary.",
                "",
                "Additive measures are METRICS; ratios, percentiles, and distinct counts",
                "are FACTS, because re-aggregating them would produce a wrong number.",
            ],
        ),
        f"USE ROLE {ident(resolved.publisher_role)};",
        "",
        f"CREATE OR REPLACE SEMANTIC VIEW {name}",
        _tables_clause(contract, resolved),
    ]
    relationships = _relationships_clause(product, contract)
    if relationships:
        body.append(relationships)
    facts = _facts_clause(contract, resolved_model)
    if facts:
        body.append(facts)
    body.append(_dimensions_clause(contract))
    metrics = _metrics_clause(contract, resolved_model)
    if metrics:
        body.append(metrics)
    body.append(f"  COMMENT = {literal(one_line(product.description))}")
    body.append(_verified_queries_clause(queries, product))
    body.append(";")
    body.append("")
    return "\n".join(body)


def _alias(dataset: ContractDataset) -> str:
    return dataset.name.removeprefix("V_").lower()


def _tables_clause(contract: DataContract, target: SnowflakeTarget) -> str:
    entries = []
    for dataset in contract.datasets:
        keys = ", ".join(ident(column) for column in dataset.grain)
        entries.append(
            f"    {ident(_alias(dataset))} AS {target.view(dataset.name)}\n"
            f"      PRIMARY KEY ({keys})\n"
            f"      COMMENT = {literal(one_line(dataset.description))}"
        )
    return "  TABLES (\n" + ",\n".join(entries) + "\n  )"


def _relationships_clause(product: DataProduct, contract: DataContract) -> str:
    if not product.relationships:
        return ""
    by_entity = {dataset.entity: _alias(dataset) for dataset in contract.datasets}
    entries = []
    for relationship in product.relationships:
        left = by_entity[relationship.from_entity]
        right = by_entity[relationship.to_entity]
        entries.append(
            f"    {ident(relationship.name)} AS "
            f"{ident(left)} ({', '.join(ident(c) for c in relationship.from_columns)}) "
            f"REFERENCES {ident(right)} "
            f"({', '.join(ident(c) for c in relationship.to_columns)})"
        )
    return "  RELATIONSHIPS (\n" + ",\n".join(entries) + "\n  )"


def _dimensions_clause(contract: DataContract) -> str:
    entries = []
    for dataset in contract.datasets:
        alias = _alias(dataset)
        for column in dataset.columns:
            if column.metric_id is not None:
                continue
            synonyms = _dimension_synonyms(column.name)
            entry = f"    {ident(alias)}.{ident(column.name)} AS {ident(column.name)}"
            if synonyms:
                entry += "\n      WITH SYNONYMS = (" + ", ".join(literal(s) for s in synonyms) + ")"
            entry += f"\n      COMMENT = {literal(one_line(column.description))}"
            entries.append(entry)
    if not entries:
        raise EmitterError("a semantic view needs at least one dimension to slice by")
    return "  DIMENSIONS (\n" + ",\n".join(entries) + "\n  )"


def _dimension_synonyms(name: str) -> list[str]:
    """Vocabulary a person would actually use for a grain column."""
    lowered = name.lower()
    synonyms = {lowered.replace("_", " ")}
    if name == TIME_COLUMN_ALIAS:
        synonyms.update({"date", "day", "period", "time period"})
    return sorted(s for s in synonyms if s)


def _facts_clause(contract: DataContract, model: SemanticModel) -> str:
    entries = []
    for dataset in contract.datasets:
        alias = _alias(dataset)
        for column in dataset.columns:
            if column.metric_id is None or is_additive(model.metric(column.metric_id)):
                continue
            entries.append(_measure_entry(alias, column, model, aggregate=None))
    return "  FACTS (\n" + ",\n".join(entries) + "\n  )" if entries else ""


def _metrics_clause(contract: DataContract, model: SemanticModel) -> str:
    entries = []
    for dataset in contract.datasets:
        alias = _alias(dataset)
        for column in dataset.columns:
            if column.metric_id is None or not is_additive(model.metric(column.metric_id)):
                continue
            entries.append(_measure_entry(alias, column, model, aggregate="SUM"))
    return "  METRICS (\n" + ",\n".join(entries) + "\n  )" if entries else ""


def _measure_entry(
    alias: str, column: ContractColumn, model: SemanticModel, *, aggregate: str | None
) -> str:
    metric = model.metric(column.metric_id or "")
    expression = f"{aggregate}({ident(column.name)})" if aggregate else ident(column.name)
    entry = f"    {ident(alias)}.{ident(column.name)} AS {expression}"
    synonyms = sorted({s.strip() for s in [*metric.synonyms, metric.name.lower()] if s.strip()})
    if synonyms:
        entry += "\n      WITH SYNONYMS = (" + ", ".join(literal(s) for s in synonyms) + ")"
    note = one_line(column.description)
    if aggregate is None:
        note += (
            " Published as a row-level fact: this figure does not survive being "
            "re-aggregated over a coarser grain."
        )
    entry += f"\n      COMMENT = {literal(note)}"
    return entry


def verified_queries(
    product: DataProduct,
    contract: DataContract,
    model: SemanticModel | None = None,
    *,
    target: SnowflakeTarget | None = None,
) -> list[VerifiedQuery]:
    """Generate one verified query per declared question, plus one per metric.

    Metrics that declare their own natural-language questions contribute those
    verbatim. A metric that declares none still gets a question built from its
    name and grain, because a metric nobody can ask about is a metric Analyst
    will get wrong.
    """
    resolved_model = model or default_model()
    resolved = target or SnowflakeTarget()
    queries: list[VerifiedQuery] = []
    for dataset in contract.datasets:
        for column in dataset.columns:
            if column.metric_id is None:
                continue
            metric = resolved_model.metric(column.metric_id)
            questions = [q.strip() for q in metric.verified_queries if q.strip()]
            if not questions:
                questions = [_synthetic_question(metric.name, dataset)]
            for index, question in enumerate(questions):
                queries.append(
                    VerifiedQuery(
                        name=f"vq_{column.name.lower()}_{index + 1}",
                        question=question,
                        sql=_verified_sql(dataset, column, resolved_model, resolved),
                        onboarding=index == 0,
                    )
                )
    return queries


def _synthetic_question(metric_name: str, dataset: ContractDataset) -> str:
    slice_by = next((c for c in dataset.grain if c != TIME_COLUMN_ALIAS), None)
    lowered = metric_name[0].lower() + metric_name[1:]
    if slice_by is None:
        return f"what is {lowered}"
    by = slice_by.lower().replace("_", " ")
    if dataset.time_grain is None:
        return f"{lowered} by {by}"
    return f"{lowered} by {by} over the last {VERIFIED_QUERY_WINDOW_DAYS} days"


def _verified_sql(
    dataset: ContractDataset,
    column: ContractColumn,
    model: SemanticModel,
    target: SnowflakeTarget,
) -> str:
    """SQL over the product's own published view.

    Additive measures are summed to the slicing columns; a non-additive measure
    is read at the relation's own grain, never averaged into nonsense.
    """
    metric = model.metric(column.metric_id or "")
    slicing = [c for c in dataset.grain if c != TIME_COLUMN_ALIAS] or list(dataset.grain)
    bucketed = TIME_COLUMN_ALIAS in dataset.grain
    where = (
        f"\nWHERE {ident(TIME_COLUMN_ALIAS)} >= "
        f"DATEADD(day, -{VERIFIED_QUERY_WINDOW_DAYS}, CURRENT_DATE())"
        if bucketed
        else ""
    )
    if is_additive(metric):
        selected = ", ".join(ident(c) for c in slicing)
        return (
            f"SELECT {selected}, SUM({ident(column.name)}) AS {ident(column.name)}\n"
            f"FROM {target.view(dataset.name)}{where}\n"
            f"GROUP BY {selected}\n"
            f"ORDER BY {ident(column.name)} DESC NULLS LAST\n"
            f"LIMIT {VERIFIED_QUERY_LIMIT}"
        )
    selected = ", ".join(ident(c) for c in dataset.grain)
    return (
        f"SELECT {selected}, {ident(column.name)}\n"
        f"FROM {target.view(dataset.name)}{where}\n"
        f"ORDER BY {ident(column.name)} DESC NULLS LAST\n"
        f"LIMIT {VERIFIED_QUERY_LIMIT}"
    )


def _verified_queries_clause(queries: list[VerifiedQuery], product: DataProduct) -> str:
    verified_at = _verified_at(product)
    verified_by = literal(f"(support = {product.sla.support_channel})")
    entries = []
    for query in queries:
        entries.append(
            f"    {ident(query.name)} AS (\n"
            f"      QUESTION {literal(query.question)}\n"
            f"      VERIFIED_AT {verified_at}\n"
            f"      ONBOARDING_QUESTION {'TRUE' if query.onboarding else 'FALSE'}\n"
            f"      VERIFIED_BY {verified_by}\n"
            f"      SQL {literal(query.sql)}\n"
            f"    )"
        )
    return "  AI_VERIFIED_QUERIES (\n" + ",\n".join(entries) + "\n  )"


def _verified_at(product: DataProduct) -> int:
    """Seconds since the Unix epoch for the release this product last shipped.

    The release date, not the clock: a redeploy of an unchanged product must
    produce an unchanged script.
    """
    released = next(
        (entry.released_on for entry in product.change_log if entry.version == product.version),
        None,
    )
    if released is None:
        raise EmitterError(
            f"{product.id} {product.version} has no change_log entry, so its verified "
            f"queries have no verification date"
        )
    try:
        released_date = date.fromisoformat(released)
    except ValueError as exc:
        raise EmitterError(f"{product.id}: released_on {released!r} is not an ISO date") from exc
    return int(datetime.combine(released_date, datetime.min.time(), tzinfo=UTC).timestamp())


__all__ = [
    "VERIFIED_QUERY_LIMIT",
    "VERIFIED_QUERY_WINDOW_DAYS",
    "VerifiedQuery",
    "emit_semantic_view",
    "verified_queries",
]
