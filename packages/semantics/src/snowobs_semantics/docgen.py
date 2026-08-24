"""Generate ``docs/KPI_CATALOG.md`` from the metric YAML (§9.2).

The catalogue is generated, never hand-written: a metric's definition, its
sources, its latency floor, and its allocation method are documented from the
same declarations the compiler reads, so the documentation cannot drift from
what the platform actually computes.

Run with ``make catalog``.
"""

from __future__ import annotations

from pathlib import Path

from snowobs_semantics.dialect_shims import shim_catalog
from snowobs_semantics.model import Metric, SemanticModel, default_model
from snowobs_semantics.registry import SourceRegistry, default_registry

DOMAIN_TITLES: dict[str, str] = {
    "cost": "D1 — Cost & spend",
    "warehouse": "D2 — Warehouse & compute efficiency",
    "query": "D3 — Query & workload performance",
    "storage": "D4 — Storage & data lifecycle",
    "pipeline": "D5 — Pipeline & orchestration reliability",
    "quality": "D6 — Data quality & freshness",
    "security": "D7 — Security, access & governance",
    "ai": "D8 — AI / Cortex & advanced features",
    "chargeback": "D9 — Chargeback, budget & commitment",
}


def _humanise_latency(minutes: int) -> str:
    if minutes == 0:
        return "point-in-time"
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 60 * 24:
        hours = minutes / 60
        return f"{hours:.0f} h" if hours.is_integer() else f"{hours:.1f} h"
    days = minutes / (60 * 24)
    return f"{days:.0f} d" if days.is_integer() else f"{days:.1f} d"


def _metric_section(metric: Metric, registry: SourceRegistry) -> str:
    lines = [f"#### `{metric.id}` — {metric.name}", ""]
    if metric.description:
        lines.append(metric.description.strip())
        lines.append("")

    facts = [
        ("Entity", f"`{metric.entity}`"),
        ("Expression", f"`{' '.join(metric.expression.split())}`"),
        ("Grain", metric.grain.value),
        (
            "Format",
            f"{metric.format.type.value}"
            + (f" ({metric.format.unit})" if metric.format.unit else ""),
        ),
        ("Direction", metric.direction.value.replace("_", " ")),
        ("Freshness floor", _humanise_latency(metric.latency_floor_minutes)),
        ("Owner", metric.owner),
    ]
    if metric.allocation_method:
        facts.append(("Allocation method", metric.allocation_method))
    if metric.provisional_window_days:
        facts.append(("Provisional window", f"{metric.provisional_window_days} days (restatement)"))
    if metric.dimensions:
        facts.append(("Dimensions", ", ".join(f"`{d}`" for d in sorted(metric.dimensions))))

    sources = []
    for source_id in metric.requires_sources:
        source = registry.get(source_id)
        sources.append(f"`{source.snowflake_object}`")
    facts.append(("Required sources", ", ".join(sources)))
    if metric.optional_sources:
        facts.append(
            (
                "Optional sources",
                ", ".join(f"`{registry.get(s).snowflake_object}`" for s in metric.optional_sources),
            )
        )
    if metric.thresholds:
        rendered = ", ".join(
            f"{name}: {value}" for name, value in metric.thresholds.items() if value is not None
        )
        if rendered:
            facts.append(("Thresholds", rendered))
    if metric.synonyms:
        facts.append(("Also known as", ", ".join(metric.synonyms)))

    lines.append("| | |")
    lines.append("|---|---|")
    lines.extend(f"| **{label}** | {value} |" for label, value in facts)
    lines.append("")

    if metric.verified_queries:
        lines.append("Verified questions:")
        lines.extend(f"- *{question}*" for question in metric.verified_queries)
        lines.append("")
    if metric.notes:
        lines.append(f"> {metric.notes.strip()}")
        lines.append("")
    return "\n".join(lines)


def render_catalog(
    model: SemanticModel | None = None, registry: SourceRegistry | None = None
) -> str:
    model = model or default_model()
    registry = registry or default_registry()

    domains = sorted(
        {metric.domain for metric in model.metrics.values()},
        key=lambda d: list(DOMAIN_TITLES).index(d) if d in DOMAIN_TITLES else 99,
    )

    lines = [
        "# KPI catalogue",
        "",
        "**Generated from `packages/semantics/metrics/*.yaml` — do not edit by hand.**",
        "Regenerate with `make catalog`.",
        "",
        "Every KPI below is defined once, in YAML, and compiled to both Snowflake and",
        "DuckDB SQL by the same compiler (R1). Each declares the source views it needs,",
        "which is what drives the coverage matrix: a KPI whose sources are missing renders",
        'as *"Unavailable — requires …"* with a remediation, never as a zero (R3).',
        "",
        "The **freshness floor** is the documented latency of the slowest source a KPI",
        "reads. No surface may imply a figure is fresher than this (R7).",
        "",
        f"**{len(model.metrics)} KPIs across {len(domains)} domains.**",
        "",
        "## Contents",
        "",
    ]
    for domain in domains:
        title = DOMAIN_TITLES.get(domain, domain.title())
        count = len(model.metrics_for_domain(domain))
        anchor = (
            title.lower().replace(" ", "-").replace("—", "").replace("&", "").replace("--", "-")
        )
        lines.append(f"- [{title}](#{anchor.strip('-')}) ({count})")
    lines.append("")

    for domain in domains:
        metrics = model.metrics_for_domain(domain)
        lines.append(f"## {DOMAIN_TITLES.get(domain, domain.title())}")
        lines.append("")
        lines.append("| KPI | Name | Freshness floor | Direction |")
        lines.append("|---|---|---|---|")
        for metric in metrics:
            lines.append(
                f"| `{metric.id}` | {metric.name} | "
                f"{_humanise_latency(metric.latency_floor_minutes)} | "
                f"{metric.direction.value.replace('_', ' ')} |"
            )
        lines.append("")
        for metric in metrics:
            lines.append(_metric_section(metric, registry))

    lines.extend(
        [
            "## Portability shims",
            "",
            "Constructs that do not express identically in both engines are translated by a",
            "shim, one construct each, every one covered by a parity test. Business logic is",
            "never forked per engine (R1).",
            "",
            "| Shim | Purpose |",
            "|---|---|",
        ]
    )
    for shim in shim_catalog():
        lines.append(f"| `{shim.name}` | {shim.description} |")
    lines.append("")
    lines.append(
        "Documented divergences and their tolerances are in "
        "[`PARITY_EXCEPTIONS.md`](PARITY_EXCEPTIONS.md)."
    )
    lines.append("")
    return "\n".join(lines)


def write_catalog(path: Path | None = None) -> Path:
    target = path or Path(__file__).resolve().parents[4] / "docs" / "KPI_CATALOG.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_catalog(), encoding="utf-8")
    return target


def main() -> int:
    path = write_catalog()
    model = default_model()
    print(f"Wrote {path} ({len(model.metrics)} KPIs)")
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
