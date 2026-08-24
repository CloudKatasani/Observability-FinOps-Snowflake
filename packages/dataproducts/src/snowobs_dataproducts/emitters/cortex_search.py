"""``CREATE CORTEX SEARCH SERVICE`` for a product's text columns (§13.4).

Semantic SQL can count log lines; it cannot answer "find the failures that look
like last Tuesday's". A search service over the product's free-text column
closes that gap.

Two hard limits, both from R2/§12.5:

* only a column the contract marks **searchable** may be indexed — a column the
  product classified sensitive never is;
* the indexed window is bounded, so the service does not become a second copy of
  the customer's telemetry.
"""

from __future__ import annotations

from snowobs_dataproducts.contracts import ContractDataset, DataContract
from snowobs_dataproducts.emitters import (
    EmitterError,
    SnowflakeTarget,
    header,
    ident,
    literal,
    one_line,
)
from snowobs_dataproducts.model import DataProduct

#: Column names that must never be indexed, whatever a product declares. Query
#: text leaving the account, even into a search index, is an explicit opt-in
#: decision that this emitter is not the place to make (§27.5).
NEVER_INDEXED = frozenset({"QUERY_TEXT", "QUERY_BODY", "SQL_TEXT"})


def search_service_name(product: DataProduct) -> str:
    if product.search is None:
        raise EmitterError(f"{product.id} declares no search column")
    return f"{product.slug_upper}_{product.search.column.upper()}"


def emit_cortex_search(
    product: DataProduct,
    contract: DataContract,
    *,
    target: SnowflakeTarget | None = None,
) -> str:
    """Render the product's Cortex Search service.

    Raises when the product declares no searchable column: a product with no
    free text has nothing to search, and emitting an empty service would put an
    unused, refreshing object on the customer's bill.
    """
    spec = product.search
    if spec is None:
        raise EmitterError(
            f"{product.id} declares no search column; this product has no free-text "
            f"surface to index"
        )
    resolved = target or SnowflakeTarget()
    dataset, column_names = _locate(contract, spec.column)

    if spec.column.upper() in NEVER_INDEXED:
        raise EmitterError(f"{product.id}: column {spec.column} must never be indexed (§27.5)")
    column = dataset.column(spec.column)
    if column is None or not column.searchable:
        raise EmitterError(
            f"{product.id}: column {spec.column} is not marked searchable in the contract"
        )
    unknown = [name for name in spec.attributes if name not in column_names]
    if unknown:
        raise EmitterError(f"{product.id}: search attributes are not contract columns: {unknown}")
    sensitive = [name for name in spec.attributes if _is_sensitive(dataset, name)]
    if sensitive:
        raise EmitterError(
            f"{product.id}: sensitive columns cannot be search attributes: {sensitive}"
        )

    attributes = list(spec.attributes)
    selected = ", ".join(ident(name) for name in [spec.column, *attributes])
    time_column = next((c for c in dataset.grain if c == "TIME_BUCKET"), None)
    where = (
        f"\n      WHERE {ident(time_column)} >= "
        f"DATEADD(day, -{spec.window_days}, CURRENT_DATE())\n"
        f"        AND {ident(spec.column)} IS NOT NULL"
        if time_column
        else f"\n      WHERE {ident(spec.column)} IS NOT NULL"
    )
    comment = one_line(
        f"Free-text search over {spec.column} in {dataset.name} for {product.name} "
        f"{product.version}. Indexed window: {spec.window_days} days."
    )
    name = resolved.qualified(resolved.search_schema, search_service_name(product))
    return (
        header(
            f"{product.name} {product.version} — Cortex Search service",
            [
                f"Indexes {spec.column} from {dataset.name} over a {spec.window_days}-day window.",
                "",
                "Only columns the contract marks searchable are indexed. Sensitive",
                "columns and query text are never indexed (R2, §12.5).",
            ],
        )
        + f"USE ROLE {ident(resolved.publisher_role)};\n"
        + "\n"
        + f"CREATE OR REPLACE CORTEX SEARCH SERVICE {name}\n"
        + f"  ON {ident(spec.column)}\n"
        + (f"  ATTRIBUTES {', '.join(ident(a) for a in attributes)}\n" if attributes else "")
        + f"  WAREHOUSE = {ident(resolved.warehouse)}\n"
        + f"  TARGET_LAG = {literal(product.refresh.target_lag_clause)}\n"
        + f"  COMMENT = {literal(comment)}\n"
        + "  AS (\n"
        + f"    SELECT {selected}\n"
        + f"      FROM {resolved.view(dataset.name)}{where}\n"
        + "  );\n"
    )


def _locate(contract: DataContract, column: str) -> tuple[ContractDataset, set[str]]:
    for dataset in contract.datasets:
        if dataset.column(column) is not None:
            return dataset, {c.name for c in dataset.columns}
    raise EmitterError(f"{contract.product_id}: no dataset carries a column named {column}")


def _is_sensitive(dataset: ContractDataset, name: str) -> bool:
    column = dataset.column(name)
    return column is not None and column.sensitive
