"""dbt project emission for a data product (BUILD_PROMPT §8.5, §13.4).

The model SQL is the semantic compiler's own output with two changes: the
interactive row cap is dropped (a mart is not truncated), and every registered
source relation becomes a ``{{ source(...) }}`` reference so the project is a
real dbt project rather than a SQL dump.

Tests are generated, not hand-written, from the contract: ``not_null`` on every
grain column, a singular test asserting the grain is actually unique, a
row-count expectation test, and a freshness test against the relation's own
guarantee. A model whose contract promises something the data does not deliver
fails ``dbt test`` rather than quietly serving a wrong number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlglot
import yaml
from sqlglot import exp

from snowobs_dataproducts.contracts import ContractDataset, DataContract
from snowobs_dataproducts.emitters import EmitterError, SnowflakeTarget, one_line
from snowobs_dataproducts.model import DataProduct
from snowobs_dataproducts.resolve import (
    DatasetSpec,
    compile_dataset,
    resolve_datasets,
    unbounded_sql,
)
from snowobs_semantics.compiler import TIME_COLUMN_ALIAS
from snowobs_semantics.model import SemanticModel, default_model
from snowobs_semantics.registry import SourceRegistry, default_registry

#: Lineage columns every emitted model carries (§8.5).
LINEAGE_COLUMNS = ("_LOADED_AT", "_SOURCE_VIEW", "_BATCH_ID")
#: Placeholder a source relation is renamed to on the parse tree before the
#: jinja ``source()`` call is spliced in — a parser has no node for jinja.
_SOURCE_PLACEHOLDER = "__DBT_SOURCE_{}__"


@dataclass(frozen=True)
class DbtProject:
    """The emitted project: path within the bundle → file contents."""

    files: dict[str, str]

    def __getitem__(self, name: str) -> str:
        return self.files[name]

    @property
    def names(self) -> list[str]:
        return sorted(self.files)


def emit_dbt_project(
    product: DataProduct,
    contract: DataContract,
    model: SemanticModel | None = None,
    registry: SourceRegistry | None = None,
    *,
    target: SnowflakeTarget | None = None,
) -> DbtProject:
    """Render a runnable dbt project for one data product."""
    resolved_model = model or default_model()
    sources = registry or default_registry()
    resolved = target or SnowflakeTarget()
    specs = {spec.view_name: spec for spec in resolve_datasets(product, resolved_model)}

    files: dict[str, str] = {
        "dbt/dbt_project.yml": _project_yml(product, resolved),
        f"dbt/models/{product.id}/sources.yml": _sources_yml(contract, sources),
        f"dbt/models/{product.id}/schema.yml": _schema_yml(product, contract),
    }
    for dataset in contract.datasets:
        spec = specs[dataset.name]
        files[f"dbt/models/{product.id}/{spec.dbt_model}.sql"] = _model_sql(
            product, dataset, spec, resolved_model, sources
        )
        files[f"dbt/tests/{spec.dbt_model}_grain_is_unique.sql"] = _grain_test(dataset, spec)
        files[f"dbt/tests/{spec.dbt_model}_row_expectations.sql"] = _row_count_test(dataset, spec)
        if TIME_COLUMN_ALIAS in dataset.grain:
            files[f"dbt/tests/{spec.dbt_model}_freshness.sql"] = _freshness_test(dataset, spec)
    return DbtProject(files=files)


def _project_yml(product: DataProduct, target: SnowflakeTarget) -> str:
    project: dict[str, Any] = {
        "name": product.id,
        "version": str(product.version),
        "config-version": 2,
        "profile": product.id,
        "model-paths": ["models"],
        "test-paths": ["tests"],
        "target-path": "target",
        "clean-targets": ["target", "dbt_packages"],
        "models": {
            product.id: {
                "+materialized": "table",
                "+on_schema_change": "append_new_columns",
                "+database": target.database,
                "+schema": target.published_schema,
                "+tags": [product.domain, product.classification.value],
            }
        },
        "vars": {
            "product_id": product.id,
            "product_version": str(product.version),
            "freshness_target_minutes": product.sla.freshness_target_minutes,
        },
    }
    return yaml.safe_dump(
        project, sort_keys=False, default_flow_style=False, width=100, allow_unicode=True
    )


def _source_group(snowflake_object: str) -> tuple[str, str, str, str]:
    """(group name, database, schema, object) for a fully qualified source."""
    parts = snowflake_object.split(".")
    if len(parts) != 3:
        raise EmitterError(
            f"source {snowflake_object!r} is not a three-part relation and cannot be a dbt source"
        )
    database, schema, name = parts
    return schema.lower(), database, schema, name


def _sources_yml(contract: DataContract, registry: SourceRegistry) -> str:
    groups: dict[str, dict[str, Any]] = {}
    for source_id in contract.sources:
        source = registry.get(source_id)
        group_name, database, schema, name = _source_group(source.snowflake_object)
        entry = groups.setdefault(
            group_name,
            {
                "name": group_name,
                "database": database,
                "schema": schema,
                "description": one_line(
                    f"Snowflake {schema} views. Read-only; the platform never writes here."
                ),
                "tables": [],
            },
        )
        table: dict[str, Any] = {
            "name": name,
            "description": one_line(
                f"{source.domain} · criticality {source.criticality.value} · documented "
                f"latency {source.documented_latency_minutes} minutes · retention "
                f"{source.retention_days} days"
                + ("" if source.latency_verified else " (latency unverified — ASSUMPTIONS U-1)")
            ),
        }
        if source.time_column:
            warn = source.documented_latency_minutes or 60
            table["loaded_at_field"] = source.time_column
            table["freshness"] = {
                "warn_after": {"count": warn, "period": "minute"},
                "error_after": {"count": warn * 2, "period": "minute"},
            }
        entry["tables"].append(table)

    for entry in groups.values():
        tables: list[dict[str, Any]] = entry["tables"]
        tables.sort(key=lambda table: str(table["name"]))
    document = {"version": 2, "sources": [groups[name] for name in sorted(groups)]}
    return yaml.safe_dump(
        document, sort_keys=False, default_flow_style=False, width=100, allow_unicode=True
    )


def _schema_yml(product: DataProduct, contract: DataContract) -> str:
    models: list[dict[str, Any]] = []
    for dataset in contract.datasets:
        columns: list[dict[str, Any]] = []
        for column in dataset.columns:
            entry: dict[str, Any] = {
                "name": column.name,
                "description": one_line(column.description)
                + (f" Unit: {column.unit}." if column.unit else "")
                + (f" Governed metric: {column.metric_id}." if column.metric_id else ""),
                "data_type": column.type.value,
            }
            tests: list[str] = []
            if not column.nullable:
                tests.append("not_null")
            if tests:
                entry["data_tests"] = tests
            if column.sensitive:
                entry["meta"] = {"sensitive": True, "masking_policy": "MP_SNOWOBS_SENSITIVE"}
            columns.append(entry)
        models.append(
            {
                "name": _model_name(dataset),
                "description": one_line(
                    f"{dataset.description} Grain: {', '.join(dataset.grain)}. Freshness "
                    f"guarantee {dataset.freshness_minutes} minutes."
                ),
                "config": {"materialized": "table", "on_schema_change": "append_new_columns"},
                "meta": {
                    "product": product.id,
                    "product_version": str(product.version),
                    "owner": product.owner,
                    "entity": dataset.entity,
                    "grain": list(dataset.grain),
                    "freshness_minutes": dataset.freshness_minutes,
                    "expected_min_rows_per_day": dataset.expected_min_rows_per_day,
                    "expected_max_rows_per_day": dataset.expected_max_rows_per_day,
                },
                "columns": columns,
            }
        )
    return yaml.safe_dump(
        {"version": 2, "models": models}, sort_keys=False, default_flow_style=False, width=100
    )


def _model_name(dataset: ContractDataset) -> str:
    return dataset.name.removeprefix("V_").lower()


def _model_sql(
    product: DataProduct,
    dataset: ContractDataset,
    spec: DatasetSpec,
    model: SemanticModel,
    registry: SourceRegistry,
) -> str:
    compiled = compile_dataset(spec, model=model)
    body = _with_dbt_sources(
        unbounded_sql(compiled), model.entity(spec.entity_id).sources, registry
    )
    return (
        f"-- {dataset.name} · {product.name} {product.version}\n"
        f"-- Generated from the governed metrics {', '.join(dataset.metric_ids)}.\n"
        f"-- Edit the metric YAML, not this file: it is regenerated on every publish.\n"
        "{{ config(materialized='table', on_schema_change='append_new_columns') }}\n"
        "\n"
        "WITH product AS (\n"
        f"{_indent(body)}\n"
        ")\n"
        "SELECT\n"
        "  product.*,\n"
        "  CURRENT_TIMESTAMP() AS _LOADED_AT,\n"
        f"  '{dataset.name}' AS _SOURCE_VIEW,\n"
        "  '{{ invocation_id }}' AS _BATCH_ID\n"
        "FROM product\n"
    )


def _indent(text: str, spaces: int = 2) -> str:
    pad = " " * spaces
    return "\n".join(f"{pad}{line}" if line.strip() else line for line in text.splitlines())


def _with_dbt_sources(sql: str, source_ids: list[str], registry: SourceRegistry) -> str:
    """Rewrite registered relation names as dbt ``source()`` references.

    The rename happens on the parse tree — a textual substitution would also hit
    a column or alias that happens to share the source's name. The jinja itself
    is spliced in afterwards, because a parser has no representation for it.
    """
    wanted = {source_id.lower(): source_id for source_id in source_ids}
    statement = sqlglot.parse_one(sql, read="snowflake")
    for table in statement.find_all(exp.Table):
        source_id = wanted.get(table.name.lower())
        if source_id is None:
            continue
        table.set("this", exp.to_identifier(_SOURCE_PLACEHOLDER.format(source_id.upper())))
    rendered = statement.sql(dialect="snowflake", pretty=True)
    for source_id in source_ids:
        source = registry.get(source_id)
        group, _database, _schema, name = _source_group(source.snowflake_object)
        rendered = rendered.replace(
            _SOURCE_PLACEHOLDER.format(source_id.upper()),
            "{{ source('" + group + "', '" + name + "') }}",
        )
    return rendered


def _grain_test(dataset: ContractDataset, spec: DatasetSpec) -> str:
    """A singular test asserting the declared grain is one row per key."""
    keys = ", ".join(dataset.grain)
    return (
        f"-- {dataset.name}: the contract declares the grain ({keys}).\n"
        f"-- A duplicate key means every downstream aggregate double-counts.\n"
        "SELECT\n"
        f"  {keys},\n"
        "  COUNT(*) AS rows_at_key\n"
        "FROM {{ ref('" + spec.dbt_model + "') }}\n"
        f"GROUP BY {keys}\n"
        "HAVING COUNT(*) > 1\n"
    )


def _row_count_test(dataset: ContractDataset, spec: DatasetSpec) -> str:
    """A singular test asserting the contract's row-count expectation."""
    minimum = dataset.expected_min_rows_per_day
    maximum = dataset.expected_max_rows_per_day
    bounds = f"at least {minimum} row(s) per day"
    predicate = f"rows_per_day < {minimum}"
    if maximum is not None:
        bounds += f" and at most {maximum}"
        predicate = f"rows_per_day < {minimum} OR rows_per_day > {maximum}"
    bucket = (
        f"CAST({TIME_COLUMN_ALIAS} AS DATE)"
        if TIME_COLUMN_ALIAS in dataset.grain
        else "CURRENT_DATE()"
    )
    return (
        f"-- {dataset.name}: the contract promises {bounds}.\n"
        f"-- An empty day is a pipeline failure, not a quiet zero (R3).\n"
        "WITH daily AS (\n"
        "  SELECT\n"
        f"    {bucket} AS usage_day,\n"
        "    COUNT(*) AS rows_per_day\n"
        "  FROM {{ ref('" + spec.dbt_model + "') }}\n"
        "  GROUP BY 1\n"
        ")\n"
        "SELECT usage_day, rows_per_day\n"
        "FROM daily\n"
        f"WHERE {predicate}\n"
    )


def _freshness_test(dataset: ContractDataset, spec: DatasetSpec) -> str:
    """A singular test asserting the relation is inside its freshness guarantee."""
    return (
        f"-- {dataset.name}: the contract guarantees the newest row is no more than\n"
        f"-- {dataset.freshness_minutes} minutes old — the documented latency of the\n"
        f"-- slowest source behind it (R7).\n"
        "SELECT\n"
        f"  MAX({TIME_COLUMN_ALIAS}) AS newest_row,\n"
        f"  TIMESTAMPDIFF(minute, MAX({TIME_COLUMN_ALIAS}), CURRENT_TIMESTAMP()) AS age_minutes\n"
        "FROM {{ ref('" + spec.dbt_model + "') }}\n"
        "HAVING TIMESTAMPDIFF(minute, MAX(" + TIME_COLUMN_ALIAS + "), CURRENT_TIMESTAMP()) > "
        f"{dataset.freshness_minutes}\n"
    )
