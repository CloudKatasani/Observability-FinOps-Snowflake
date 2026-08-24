"""Snowflake DDL for a published data product (BUILD_PROMPT §13.4).

Three artifacts:

* **foundations** — the database, schemas, warehouses, and consumer roles the
  product lands in, all idempotent;
* **published views** — one ``SECURE VIEW`` per contract dataset, built from the
  semantic compiler's own SQL so the published relation and the dashboard tile
  are the same query (R1, R5);
* **policies and grants** — masking on every column the product marks sensitive,
  a row-access policy on restricted products, and explicit per-object grants.

Grants are enumerated object by object. ``GRANT SELECT ON ALL VIEWS`` would
quietly widen the share the next time somebody adds a view to the schema.
"""

from __future__ import annotations

from snowobs_dataproducts.contracts import ContractDataset, DataContract
from snowobs_dataproducts.emitters import SnowflakeTarget, header, ident, literal, one_line
from snowobs_dataproducts.model import Classification, DataProduct
from snowobs_dataproducts.resolve import (
    DatasetSpec,
    compile_dataset,
    resolve_datasets,
    unbounded_sql,
)
from snowobs_semantics.model import SemanticModel, default_model

#: Name of the masking policy applied to every sensitive column in a product.
MASKING_POLICY = "MP_SNOWOBS_SENSITIVE"
#: Name of the row-access policy applied to restricted products.
ROW_ACCESS_POLICY = "RAP_SNOWOBS_RESTRICTED"


def emit_foundations_ddl(target: SnowflakeTarget | None = None) -> str:
    """The idempotent landing zone for every published product."""
    resolved = target or SnowflakeTarget()
    statements = [
        header(
            "Observability & FinOps Platform — data product foundations",
            [
                "Creates the database, schemas, warehouses, and consumer roles that",
                "published data products land in. Idempotent: safe to re-run.",
                "",
                "Run as a role that can create databases, warehouses, and roles.",
            ],
        ),
        "USE ROLE SYSADMIN;",
        "",
        f"CREATE DATABASE IF NOT EXISTS {ident(resolved.database)}",
        "  COMMENT = 'Published observability data products';",
        f"CREATE SCHEMA IF NOT EXISTS {ident(resolved.database)}."
        f"{ident(resolved.published_schema)}",
        "  COMMENT = 'Secure views consumers read';",
        f"CREATE SCHEMA IF NOT EXISTS {ident(resolved.database)}.{ident(resolved.semantic_schema)}",
        "  COMMENT = 'Semantic views for Cortex Analyst';",
        f"CREATE SCHEMA IF NOT EXISTS {ident(resolved.database)}.{ident(resolved.search_schema)}",
        "  COMMENT = 'Cortex Search services over product text columns';",
        f"CREATE SCHEMA IF NOT EXISTS {ident(resolved.database)}.{ident(resolved.agent_schema)}",
        "  COMMENT = 'Cortex Agents scoped to a single data product';",
        "",
        "-- Agent traffic runs on its own small, resource-monitored warehouse so a",
        "-- conversational workload cannot consume refresh capacity (§14).",
        f"CREATE WAREHOUSE IF NOT EXISTS {ident(resolved.agent_warehouse)}",
        "  WAREHOUSE_SIZE = XSMALL",
        "  AUTO_SUSPEND = 60",
        "  AUTO_RESUME = TRUE",
        "  INITIALLY_SUSPENDED = TRUE",
        "  COMMENT = 'Cortex Agent serving warehouse for observability data products';",
        "",
        "USE ROLE USERADMIN;",
        f"CREATE ROLE IF NOT EXISTS {ident(resolved.consumer_role)}",
        "  COMMENT = 'Reads published observability data products';",
        f"CREATE ROLE IF NOT EXISTS {ident(resolved.agent_role)}",
        "  COMMENT = 'Talks to observability data product agents';",
        "",
        "USE ROLE SECURITYADMIN;",
        f"GRANT USAGE ON DATABASE {ident(resolved.database)} TO ROLE "
        f"{ident(resolved.consumer_role)};",
        f"GRANT USAGE ON SCHEMA {ident(resolved.database)}.{ident(resolved.published_schema)} "
        f"TO ROLE {ident(resolved.consumer_role)};",
        f"GRANT USAGE ON WAREHOUSE {ident(resolved.agent_warehouse)} TO ROLE "
        f"{ident(resolved.agent_role)};",
        "",
    ]
    return "\n".join(statements)


def emit_published_views(
    product: DataProduct,
    contract: DataContract,
    model: SemanticModel | None = None,
    *,
    target: SnowflakeTarget | None = None,
) -> str:
    """One secure view per contract dataset, with column comments."""
    resolved_model = model or default_model()
    resolved = target or SnowflakeTarget()
    specs = {spec.view_name: spec for spec in resolve_datasets(product, resolved_model)}

    parts = [
        header(
            f"{product.name} {product.version} — published views",
            [
                f"Product: {product.id} · owner: {product.owner} · "
                f"classification: {product.classification.value}",
                f"Freshness guarantee: {contract.freshness_guarantee_minutes} minutes "
                f"(the slowest source behind this product).",
                "",
                "Each view is the semantic layer's own compiled SQL for the metrics in",
                "the product's boundary — the same query a dashboard tile runs, so a",
                "consumer and a tile can never disagree about a figure (R1, R5).",
                "",
                "The interactive row cap the SQL guard forces on ad-hoc execution is",
                "deliberately absent here: a published relation is not truncated.",
            ],
        ),
        f"USE ROLE {ident(resolved.publisher_role)};",
        f"USE WAREHOUSE {ident(resolved.warehouse)};",
        "",
    ]
    for dataset in contract.datasets:
        parts.append(_view_ddl(product, dataset, specs[dataset.name], resolved_model, resolved))
    return "\n".join(parts)


def _view_ddl(
    product: DataProduct,
    dataset: ContractDataset,
    spec: DatasetSpec,
    model: SemanticModel,
    target: SnowflakeTarget,
) -> str:
    compiled = compile_dataset(spec, model=model)
    columns = ",\n".join(
        f"  {ident(column.name)} COMMENT "
        f"{literal(_column_comment(column.description, column.unit))}"
        for column in dataset.columns
    )
    comment = one_line(
        f"{dataset.description} Grain: {', '.join(dataset.grain)}. "
        f"Freshness floor {dataset.freshness_minutes} minutes. "
        f"Product {product.id} {product.version}, owner {product.owner}."
    )
    return (
        f"-- {dataset.name}: {len(dataset.columns)} columns · "
        f"sources {', '.join(dataset.sources)}\n"
        f"CREATE OR REPLACE SECURE VIEW {target.view(dataset.name)} (\n"
        f"{columns}\n"
        f")\n"
        f"  COMMENT = {literal(comment)}\n"
        f"AS\n"
        f"{unbounded_sql(compiled)};\n"
    )


def _column_comment(description: str, unit: str | None) -> str:
    text = one_line(description)
    return f"{text} Unit: {unit}." if unit else text


def emit_policies(
    product: DataProduct,
    contract: DataContract,
    *,
    target: SnowflakeTarget | None = None,
) -> str | None:
    """Masking and row-access policy DDL, or ``None`` when the product needs none.

    Returning ``None`` rather than an empty script matters: a bundle that ships
    a policy file containing nothing reads as "reviewed, nothing to protect",
    which is a claim this function is not entitled to make on a product that
    simply declared no sensitive columns.
    """
    resolved = target or SnowflakeTarget()
    sensitive = [
        (dataset.name, column.name)
        for dataset in contract.datasets
        for column in dataset.columns
        if column.sensitive
    ]
    restricted = product.classification is Classification.RESTRICTED
    if not sensitive and not restricted:
        return None

    masking_comment = one_line(
        "Hashes identifying values for roles outside the product consumer role; "
        "the hash stays joinable."
    )
    row_access_comment = one_line("Restricted product: only the product consumer role sees rows.")
    parts = [
        header(
            f"{product.name} {product.version} — masking and row access policies",
            [
                f"Classification: {product.classification.value}.",
                f"{len(sensitive)} column(s) carry values that must not be read in the",
                "clear by a consumer who is not entitled to them.",
                "",
                "Policies are attached to the published views only. The curated and raw",
                "layers are never in a share (§13.4).",
            ],
        ),
        f"USE ROLE {ident(resolved.publisher_role)};",
        "",
    ]
    if sensitive:
        parts.append(
            f"CREATE MASKING POLICY IF NOT EXISTS "
            f"{resolved.qualified(resolved.published_schema, MASKING_POLICY)}\n"
            f"  AS (value STRING) RETURNS STRING ->\n"
            f"    CASE\n"
            f"      WHEN IS_ROLE_IN_SESSION({literal(resolved.consumer_role)}) THEN value\n"
            f"      ELSE SHA2(value, 256)\n"
            f"    END\n"
            f"  COMMENT = {literal(masking_comment)};\n"
        )
        for view_name, column in sensitive:
            parts.append(
                f"ALTER VIEW {resolved.view(view_name)} MODIFY COLUMN {ident(column)}\n"
                f"  SET MASKING POLICY "
                f"{resolved.qualified(resolved.published_schema, MASKING_POLICY)};"
            )
        parts.append("")
    if restricted:
        parts.append(
            f"CREATE ROW ACCESS POLICY IF NOT EXISTS "
            f"{resolved.qualified(resolved.published_schema, ROW_ACCESS_POLICY)}\n"
            f"  AS (row_owner STRING) RETURNS BOOLEAN ->\n"
            f"    IS_ROLE_IN_SESSION({literal(resolved.consumer_role)})\n"
            f"  COMMENT = {literal(row_access_comment)};\n"
        )
        for dataset in contract.datasets:
            anchor = next(
                (c.name for c in dataset.columns if c.sensitive),
                dataset.grain[0] if dataset.grain else None,
            )
            if anchor is None:
                continue
            parts.append(
                f"ALTER VIEW {resolved.view(dataset.name)}\n"
                f"  ADD ROW ACCESS POLICY "
                f"{resolved.qualified(resolved.published_schema, ROW_ACCESS_POLICY)}\n"
                f"  ON ({ident(anchor)});"
            )
        parts.append("")
    return "\n".join(parts)


def emit_grants(
    product: DataProduct,
    contract: DataContract,
    *,
    target: SnowflakeTarget | None = None,
) -> str:
    """Explicit, per-object grants for the consumer and agent roles."""
    resolved = target or SnowflakeTarget()
    semantic_view = resolved.qualified(resolved.semantic_schema, product.slug_upper)
    consumer_summary = (
        ", ".join(c.grantee or c.name for c in product.consumers) or "none registered"
    )
    parts = [
        header(
            f"{product.name} {product.version} — consumer and agent grants",
            [
                "Every object is granted by name. GRANT ... ON ALL VIEWS would widen",
                "the product silently the next time a view is added to the schema.",
                "",
                f"Consumers: {consumer_summary}",
            ],
        ),
        "USE ROLE SECURITYADMIN;",
        "",
        f"GRANT USAGE ON DATABASE {ident(resolved.database)} "
        f"TO ROLE {ident(resolved.consumer_role)};",
        f"GRANT USAGE ON SCHEMA {ident(resolved.database)}.{ident(resolved.published_schema)} "
        f"TO ROLE {ident(resolved.consumer_role)};",
        f"GRANT USAGE ON SCHEMA {ident(resolved.database)}.{ident(resolved.semantic_schema)} "
        f"TO ROLE {ident(resolved.consumer_role)};",
        "",
    ]
    for dataset in contract.datasets:
        parts.append(
            f"GRANT SELECT ON VIEW {resolved.view(dataset.name)} "
            f"TO ROLE {ident(resolved.consumer_role)};"
        )
    parts.extend(
        [
            "",
            f"GRANT REFERENCES ON SEMANTIC VIEW {semantic_view} "
            f"TO ROLE {ident(resolved.consumer_role)};",
            f"GRANT REFERENCES ON SEMANTIC VIEW {semantic_view} "
            f"TO ROLE {ident(resolved.agent_role)};",
            "",
        ]
    )
    if product.search is not None:
        service = resolved.qualified(
            resolved.search_schema, f"{product.slug_upper}_{product.search.column}"
        )
        parts.append(
            f"GRANT USAGE ON CORTEX SEARCH SERVICE {service} TO ROLE {ident(resolved.agent_role)};"
        )
    parts.extend(
        [
            f"GRANT USAGE ON AGENT "
            f"{resolved.qualified(resolved.agent_schema, product.slug_upper + '_AGENT')} "
            f"TO ROLE {ident(resolved.agent_role)};",
            f"GRANT USAGE ON WAREHOUSE {ident(resolved.agent_warehouse)} "
            f"TO ROLE {ident(resolved.agent_role)};",
            "",
            "-- Grant the consumer role onward to the registered consumers. Each line is",
            "-- one entry in the product's consumer register; adding a consumer is a",
            "-- registry change, not an ad-hoc grant.",
        ]
    )
    for consumer in product.consumers:
        if consumer.grantee:
            parts.append(
                f"GRANT ROLE {ident(resolved.consumer_role)} TO ROLE {ident(consumer.grantee)};"
                f"  -- {consumer.name}"
            )
    parts.append("")
    parts.append(f"SHOW GRANTS TO ROLE {ident(resolved.consumer_role)};")
    parts.append("")
    return "\n".join(parts)
