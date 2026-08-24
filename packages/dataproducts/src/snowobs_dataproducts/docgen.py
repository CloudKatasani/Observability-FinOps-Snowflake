"""Generate ``docs/DATA_CONTRACTS.md`` from the product registry (§13.2).

The contract documentation is generated, never hand-written, for the same reason
the KPI catalogue is: a document that can drift from the contract it describes is
worse than no document, because people trust it.

Run with ``make contracts``.
"""

from __future__ import annotations

from pathlib import Path

from snowobs_dataproducts.contracts import (
    BREAKING_CHANGE_POLICY,
    ContractStore,
    DataContract,
    build_contract,
)
from snowobs_dataproducts.model import DataProduct
from snowobs_dataproducts.registry import ProductRegistry, load_products
from snowobs_semantics.model import SemanticModel, default_model
from snowobs_semantics.registry import SourceRegistry, default_registry


def _humanise(minutes: int) -> str:
    if minutes == 0:
        return "point-in-time"
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 60 * 24:
        hours = minutes / 60
        return f"{hours:.0f} h" if hours.is_integer() else f"{hours:.1f} h"
    days = minutes / (60 * 24)
    return f"{days:.0f} d" if days.is_integer() else f"{days:.1f} d"


def _anchor(product: DataProduct) -> str:
    return product.id.replace("_", "-")


def _product_section(
    product: DataProduct,
    contract: DataContract,
    registry: SourceRegistry,
    store: ContractStore,
) -> list[str]:
    lines = [
        f"## {product.name} — `{product.id}` {product.version}",
        "",
        " ".join(product.description.split()),
        "",
        "| | |",
        "|---|---|",
        f"| **Owner** | {product.owner} |",
        f"| **Domain** | {product.domain} |",
        f"| **Status** | {product.status.value} |",
        f"| **Classification** | {product.classification.value} |",
        f"| **Freshness guarantee** | {_humanise(contract.freshness_guarantee_minutes)} "
        f"— the documented latency of the slowest source; no surface may imply fresher |",
        f"| **SLA target** | {_humanise(product.sla.freshness_target_minutes)} freshness, "
        f"{contract.availability_pct}% availability |",
        f"| **Retention** | {contract.retention_days} days |",
        f"| **Refresh** | every {_humanise(product.refresh.target_lag_minutes)} "
        f"(`{product.refresh.cron}`) |",
        f"| **Deprecation notice** | {contract.deprecation_notice_days} days |",
        f"| **Support** | {contract.support_channel} |",
        f"| **Documentation** | {product.documentation_url} |",
        f"| **Governed metrics** | {len(contract.metric_ids)} |",
        f"| **Relations** | {len(contract.datasets)} |",
        "",
    ]

    published = [str(v) for v in store.versions(product.id)]
    if published:
        lines.extend(
            [
                f"Published contract snapshots on file: {', '.join(published)}.",
                "",
            ]
        )

    if product.consumers:
        lines.extend(
            ["### Consumers", "", "| Consumer | Contact | Purpose | Grantee |", "|---|---|---|---|"]
        )
        lines.extend(
            f"| {c.name} | {c.contact} | {' '.join(c.purpose.split())} | "
            f"{f'`{c.grantee}`' if c.grantee else '—'} |"
            for c in product.consumers
        )
        lines.append("")

    lines.extend(["### Relations", ""])
    for dataset in contract.datasets:
        lines.extend(
            [
                f"#### `{dataset.name}`",
                "",
                " ".join(dataset.description.split()),
                "",
                f"- **Entity:** `{dataset.entity}`",
                f"- **Grain:** {', '.join(f'`{g}`' for g in dataset.grain)}"
                + (f" at {dataset.time_grain} buckets" if dataset.time_grain else ""),
                f"- **Freshness guarantee:** {_humanise(dataset.freshness_minutes)}",
                "- **Row expectation:** "
                + (
                    f"{dataset.expected_min_rows_per_day}–"
                    f"{dataset.expected_max_rows_per_day} rows per day"
                    if dataset.expected_max_rows_per_day is not None
                    else f"at least {dataset.expected_min_rows_per_day} row(s) per day"
                ),
                "- **Sources:** "
                + ", ".join(f"`{registry.get(s).snowflake_object}`" for s in dataset.sources),
                "",
                "| Column | Type | Null | Governed metric | Description |",
                "|---|---|---|---|---|",
            ]
        )
        for column in dataset.columns:
            flags = []
            if column.sensitive:
                flags.append("**sensitive**")
            if column.searchable:
                flags.append("searchable")
            description = " ".join(column.description.split())
            if column.unit:
                description += f" _(unit: {column.unit})_"
            if flags:
                description += " · " + ", ".join(flags)
            lines.append(
                f"| `{column.name}` | `{column.type.value}` | "
                f"{'yes' if column.nullable else 'no'} | "
                f"{f'`{column.metric_id}`' if column.metric_id else '—'} | {description} |"
            )
        lines.append("")

    if product.search is not None:
        lines.extend(
            [
                "### Free-text search",
                "",
                f"A Cortex Search service indexes `{product.search.column}` over a "
                f"{product.search.window_days}-day window"
                + (
                    f", filterable by {', '.join(f'`{a}`' for a in product.search.attributes)}."
                    if product.search.attributes
                    else "."
                ),
                "",
            ]
        )

    lines.extend(
        [
            "### Change history",
            "",
            "| Version | Released | Breaking | Summary |",
            "|---|---|---|---|",
        ]
    )
    for entry in reversed(product.change_log):
        lines.append(
            f"| {entry.version} | {entry.released_on} | "
            f"{'**yes**' if entry.breaking else 'no'} | "
            f"{' '.join(entry.summary.split())} |"
        )
    lines.append("")
    for entry in reversed(product.change_log):
        if entry.migration_note:
            lines.extend(
                [
                    f"**Migration note, {entry.version}:** "
                    f"{' '.join(entry.migration_note.split())}",
                    "",
                ]
            )
    return lines


def render_contracts(
    products: ProductRegistry | None = None,
    model: SemanticModel | None = None,
    registry: SourceRegistry | None = None,
    store: ContractStore | None = None,
) -> str:
    """Render the whole contract document."""
    resolved_products = products or load_products()
    resolved_model = model or default_model()
    resolved_registry = registry or default_registry()
    resolved_store = store or ContractStore()

    contracts = {
        product.id: build_contract(product, resolved_model, resolved_registry)
        for product in resolved_products
    }
    ordered = [resolved_products.get(product_id) for product_id in resolved_products.ids()]

    lines = [
        "# Data contracts",
        "",
        "**Generated from `packages/dataproducts/products/*.yaml` — do not edit by hand.**",
        "Regenerate with `make contracts`.",
        "",
        "Each data product below exposes a set of governed metrics as a small number of",
        "relations. The contract is *derived* from the product declaration and the",
        "semantic layer, so a product cannot contract for a column the metric layer does",
        "not produce, and a contract that stops matching the semantic layer is reported",
        "as drift rather than quietly served (R1, R5).",
        "",
        "**Freshness guarantees are never optimistic.** Each relation's guarantee is the",
        "*maximum* documented latency across the sources behind it, and the product's",
        "guarantee is the maximum across its relations (R7).",
        "",
        f"**{len(ordered)} products · "
        f"{sum(len(c.datasets) for c in contracts.values())} relations · "
        f"{sum(c.column_count for c in contracts.values())} contracted columns.**",
        "",
        "## Contents",
        "",
    ]
    for product in ordered:
        contract = contracts[product.id]
        lines.append(
            f"- [{product.name}](#{_anchor(product)}) — `{product.id}` {product.version}, "
            f"{len(contract.metric_ids)} metrics, freshness "
            f"{_humanise(contract.freshness_guarantee_minutes)}"
        )
    lines.extend(
        [
            "",
            "## Breaking-change policy",
            "",
            BREAKING_CHANGE_POLICY,
            "",
            "Nothing publishes without a recorded human approval naming the actor, the",
            "time, the reason, and the contract diff (R8). The platform emits the",
            "artifacts; a person applies them in their own account (R2).",
            "",
        ]
    )
    for product in ordered:
        lines.extend(
            _product_section(product, contracts[product.id], resolved_registry, resolved_store)
        )
    return "\n".join(lines)


def write_contracts(path: Path | None = None) -> Path:
    target = path or Path(__file__).resolve().parents[4] / "docs" / "DATA_CONTRACTS.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_contracts(), encoding="utf-8")
    return target


def main() -> int:
    path = write_contracts()
    products = load_products()
    print(f"Wrote {path} ({len(products)} data products)")
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
