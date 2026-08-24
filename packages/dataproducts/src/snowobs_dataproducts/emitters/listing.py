"""Internal-marketplace publication (BUILD_PROMPT §13.4).

The DDL is ``CREATE ORGANIZATION LISTING`` — an organization-scoped listing on
the internal marketplace, not the public one, and not the older share-only
``CREATE LISTING`` (ASSUMPTIONS §6, A-9). It carries a YAML manifest, which this
module emits as a separate bundle file so the manifest a reviewer reads is
byte-identical to the one the DDL embeds.

The share is scoped to ``PUBLISHED`` and ``SEMANTIC`` only. Curated and raw
layers are never in a share, and every object is granted by name so widening the
share is always a visible diff (§13.4).

``PUBLISH = FALSE``: creating the listing and publishing it to the organization
are two separate acts, and the second one is a human's (R8).
"""

from __future__ import annotations

from typing import Any

import yaml

from snowobs_dataproducts.contracts import DataContract
from snowobs_dataproducts.emitters import (
    SnowflakeTarget,
    header,
    ident,
    literal,
    one_line,
)
from snowobs_dataproducts.emitters.semantic_view import verified_queries
from snowobs_dataproducts.model import DataProduct
from snowobs_dataproducts.resolve import metric_column

#: How many worked examples the listing carries. Enough to show the shape of the
#: product without turning the manifest into the documentation.
USAGE_EXAMPLE_COUNT = 4


def share_name(product: DataProduct) -> str:
    return f"SHARE_{product.slug_upper}"


def listing_name(product: DataProduct) -> str:
    return f"LST_{product.slug_upper}"


def emit_listing_manifest(
    product: DataProduct,
    contract: DataContract,
    *,
    target: SnowflakeTarget | None = None,
) -> str:
    """The organization listing manifest, as YAML."""
    resolved = target or SnowflakeTarget()
    manifest: dict[str, Any] = {
        "title": product.name,
        "subtitle": one_line(product.description)[:110],
        "description": _description(product, contract),
        "organization_profile": "INTERNAL",
        "organization_targets": {
            "access": (
                [{"account": account} for account in resolved.organization_accounts]
                if resolved.organization_accounts
                else [{"all_accounts": True}]
            ),
            "support_contact": product.sla.support_channel,
            "approver_contact": product.sla.support_channel,
        },
        "auto_fulfillment": {
            "refresh_schedule": _refresh_schedule(product),
            "refresh_type": "SUB_DATABASE",
        },
        "categories": list(product.categories),
        "resources": {
            "documentation": product.documentation_url,
            "support": product.sla.support_channel,
        },
        "data_dictionary": {
            "featured": [
                {
                    "database_name": resolved.database,
                    "objects": [
                        {
                            "name": dataset.name,
                            "schema": resolved.published_schema,
                            "domain": "VIEW",
                        }
                        for dataset in contract.datasets
                    ]
                    + [
                        {
                            "name": product.slug_upper,
                            "schema": resolved.semantic_schema,
                            "domain": "SEMANTIC_VIEW",
                        }
                    ],
                }
            ]
        },
        "usage_examples": _usage_examples(product, contract, resolved),
    }
    if resolved.access_regions:
        manifest["locations"] = {"access_regions": [{"name": r} for r in resolved.access_regions]}
    return yaml.safe_dump(
        manifest, sort_keys=False, default_flow_style=False, width=100, allow_unicode=True
    )


def _description(product: DataProduct, contract: DataContract) -> str:
    """The consumer-facing description, with the contract's promises stated.

    A marketplace tile that omits the freshness floor is a figure without its
    latency (R7, §27.9) — so the promise is part of the description, not an
    afterthought in the documentation.
    """
    lines = [
        one_line(product.description),
        "",
        f"Owner: {product.owner} · support: {product.sla.support_channel}",
        f"Classification: {product.classification.value}",
        f"Freshness guarantee: {_humanise(contract.freshness_guarantee_minutes)} "
        f"(the documented latency of the slowest source behind this product).",
        f"Availability target: {contract.availability_pct}% · "
        f"retention: {contract.retention_days} days",
        f"Deprecation notice: {contract.deprecation_notice_days} days",
        "",
        f"Relations ({len(contract.datasets)}):",
    ]
    lines.extend(
        f"  - {dataset.name} — {len(dataset.columns)} columns, grain "
        f"{', '.join(dataset.grain)}, freshness {_humanise(dataset.freshness_minutes)}"
        for dataset in contract.datasets
    )
    lines.extend(
        [
            "",
            f"Governed metrics ({len(contract.metric_ids)}): " + ", ".join(contract.metric_ids),
            "",
            "Breaking-change policy: " + contract.breaking_change_policy,
        ]
    )
    return "\n".join(lines)


def _humanise(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 60 * 24:
        hours = minutes // 60
        return f"{hours} h"
    days = minutes // (60 * 24)
    return f"{days} d"


def _refresh_schedule(product: DataProduct) -> str:
    """Auto-fulfilment cadence, expressed in the units the manifest accepts."""
    minutes = product.refresh.target_lag_minutes
    if minutes % (60 * 24) == 0:
        return f"{minutes // (60 * 24) * 24} HOUR"
    if minutes % 60 == 0:
        return f"{minutes // 60} HOUR"
    return f"{minutes} MINUTE"


def _usage_examples(
    product: DataProduct, contract: DataContract, target: SnowflakeTarget
) -> list[dict[str, str]]:
    examples = []
    for query in verified_queries(product, contract, target=target)[:USAGE_EXAMPLE_COUNT]:
        examples.append(
            {
                "title": query.question[:110],
                "description": (
                    f"Answered from the product's own published views; the same SQL is a "
                    f"verified query on the {product.slug_upper} semantic view."
                ),
                "query": query.sql + ";",
            }
        )
    if not examples:
        # Unreachable for a validated product — every metric contributes a
        # question — but a listing with no worked example is not publishable.
        first = contract.datasets[0]
        column = metric_column(first.metric_ids[0])
        examples.append(
            {
                "title": f"Sample rows from {first.name}",
                "description": "The product's primary relation.",
                "query": (
                    f"SELECT {', '.join(first.grain)}, {column}\n"
                    f"FROM {target.view(first.name)}\n"
                    f"LIMIT 100;"
                ),
            }
        )
    return examples


def emit_listing_ddl(
    product: DataProduct,
    contract: DataContract,
    manifest_yaml: str,
    *,
    target: SnowflakeTarget | None = None,
) -> str:
    """Share creation, per-object grants, and the organization listing."""
    resolved = target or SnowflakeTarget()
    share = share_name(product)
    listing = listing_name(product)
    semantic_view = resolved.qualified(resolved.semantic_schema, product.slug_upper)

    lines = [
        header(
            f"{product.name} {product.version} — share and organization listing",
            [
                "Publishes the product to the organization's internal marketplace.",
                "",
                "The share carries the PUBLISHED and SEMANTIC schemas only — never the",
                "curated or raw layers — and grants each object by name.",
                "",
                "PUBLISH = FALSE: the listing is created but not offered. Publishing it",
                "to the organization is a separate, deliberate act by a human (R8).",
                "",
                "Requires ORGADMIN (or an organization account) to create an",
                "organization listing.",
            ],
        ),
        "USE ROLE ACCOUNTADMIN;",
        "",
        f"CREATE SHARE IF NOT EXISTS {ident(share)}",
        f"  COMMENT = {literal(one_line(f'{product.name} {product.version} — internal share'))};",
        "",
        f"GRANT USAGE ON DATABASE {ident(resolved.database)} TO SHARE {ident(share)};",
        f"GRANT USAGE ON SCHEMA {ident(resolved.database)}.{ident(resolved.published_schema)} "
        f"TO SHARE {ident(share)};",
        f"GRANT USAGE ON SCHEMA {ident(resolved.database)}.{ident(resolved.semantic_schema)} "
        f"TO SHARE {ident(share)};",
        "",
    ]
    lines.extend(
        f"GRANT SELECT ON VIEW {resolved.view(dataset.name)} TO SHARE {ident(share)};"
        for dataset in contract.datasets
    )
    lines.extend(
        [
            "",
            f"GRANT REFERENCES ON SEMANTIC VIEW {semantic_view} TO SHARE {ident(share)};",
            "",
            f"CREATE ORGANIZATION LISTING IF NOT EXISTS {ident(listing)}",
            f"  SHARE {ident(share)}",
            "  AS $$",
            manifest_yaml.rstrip("\n"),
            "$$",
            "  PUBLISH = FALSE;",
            "",
            f"SHOW ORGANIZATION LISTINGS LIKE {literal(listing)};",
            "",
        ]
    )
    return "\n".join(lines)
