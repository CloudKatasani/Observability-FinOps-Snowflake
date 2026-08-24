"""Synthetic account generator (BUILD_PROMPT §7.5).

Produces schema-faithful rows for every registered source that carries planted
behaviour, plus the ground-truth file. Output is written as CSV (and optionally
Parquet) so it loads through the *real* ingestion pipeline — there is no back
door into the catalog.

The daily metering roll-up implements the verified cloud-services rule: the
account is billed only for cloud-services credits exceeding 10% of that day's
compute, and ``CREDITS_ADJUSTMENT_CLOUD_SERVICES`` carries the negative rebate
(ASSUMPTIONS §4). The reconciliation gate is tested against these numbers.
"""

from __future__ import annotations

import csv
import gzip
import json
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from snowobs_fixtures.account import Account, build_account, days_iter
from snowobs_fixtures.config import GeneratorConfig
from snowobs_fixtures.domains import (
    ai_usage_rows,
    dynamic_table_rows,
    grant_rows,
    login_rows,
    serverless_task_rows,
    storage_rows,
    table_storage_metrics,
    task_rows,
    user_snapshot,
    warehouse_snapshot,
)
from snowobs_fixtures.ground_truth import GroundTruth, build_ground_truth
from snowobs_fixtures.workload import WorkloadGenerator, _dec

CLOUD_SERVICES_FREE_RATIO = Decimal("0.10")
CLOUD_SERVICES_RATE = Decimal("0.06")  # cloud-services credits per compute credit


class GeneratedAccount:
    """In-memory result of a generation run, keyed by source id."""

    def __init__(self, account: Account, ground_truth: GroundTruth) -> None:
        self.account = account
        self.ground_truth = ground_truth
        self.tables: dict[str, list[dict[str, Any]]] = {}

    def add(self, source_id: str, rows: Iterable[dict[str, Any]]) -> None:
        self.tables.setdefault(source_id, []).extend(rows)

    def row_count(self, source_id: str) -> int:
        return len(self.tables.get(source_id, []))

    @property
    def source_ids(self) -> list[str]:
        return sorted(self.tables)


def _metering_daily(
    day: date, compute_credits: Decimal, serverless_credits: Decimal, ai_credits: Decimal
) -> list[dict[str, Any]]:
    """METERING_DAILY_HISTORY rows with the verified cloud-services adjustment."""
    cloud_used = (compute_credits * CLOUD_SERVICES_RATE).quantize(Decimal("0.000000001"))
    free_allowance = (compute_credits * CLOUD_SERVICES_FREE_RATIO).quantize(Decimal("0.000000001"))
    # The rebate never exceeds the day's actual cloud-services usage.
    adjustment = -min(cloud_used, free_allowance)
    billed_cloud = cloud_used + adjustment

    rows = [
        {
            "SERVICE_TYPE": "WAREHOUSE_METERING",
            "USAGE_DATE": day.isoformat(),
            "CREDITS_USED_COMPUTE": str(compute_credits),
            "CREDITS_USED_CLOUD_SERVICES": str(cloud_used),
            "CREDITS_USED": str(compute_credits + cloud_used),
            "CREDITS_ADJUSTMENT_CLOUD_SERVICES": str(adjustment),
            "CREDITS_BILLED": str(compute_credits + billed_cloud),
        }
    ]
    if serverless_credits > 0:
        rows.append(
            {
                "SERVICE_TYPE": "SERVERLESS_TASK",
                "USAGE_DATE": day.isoformat(),
                "CREDITS_USED_COMPUTE": str(serverless_credits),
                "CREDITS_USED_CLOUD_SERVICES": "0.000000000",
                "CREDITS_USED": str(serverless_credits),
                "CREDITS_ADJUSTMENT_CLOUD_SERVICES": "0.000000000",
                "CREDITS_BILLED": str(serverless_credits),
            }
        )
    if ai_credits > 0:
        rows.append(
            {
                "SERVICE_TYPE": "AI_SERVICES",
                "USAGE_DATE": day.isoformat(),
                "CREDITS_USED_COMPUTE": str(ai_credits),
                "CREDITS_USED_CLOUD_SERVICES": "0.000000000",
                "CREDITS_USED": str(ai_credits),
                "CREDITS_ADJUSTMENT_CLOUD_SERVICES": "0.000000000",
                "CREDITS_BILLED": str(ai_credits),
            }
        )
    return rows


def _usage_in_currency(day: date, billed_credits: Decimal, price: Decimal) -> list[dict[str, Any]]:
    return [
        {
            "ORGANIZATION_NAME": "ACME_GROUP",
            "CONTRACT_NUMBER": "CN-100042",
            "ACCOUNT_NAME": "ACME_PROD",
            "ACCOUNT_LOCATOR": "AB12345",
            "REGION": "AWS_EU_WEST_1",
            "SERVICE_LEVEL": "Enterprise",
            "USAGE_DATE": day.isoformat(),
            "USAGE_TYPE": "compute",
            "CURRENCY": "USD",
            "USAGE": str(billed_credits),
            "USAGE_IN_CURRENCY": str((billed_credits * price).quantize(Decimal("0.01"))),
            "BALANCE_SOURCE": "capacity",
        }
    ]


def generate(config: GeneratorConfig | None = None) -> GeneratedAccount:
    """Generate a complete synthetic account in memory."""
    config = config or GeneratorConfig()
    account = build_account(config)
    ground_truth = build_ground_truth(config)
    workload = WorkloadGenerator(account, ground_truth)
    result = GeneratedAccount(account, ground_truth)
    price = Decimal(str(config.credit_price_usd))

    for day in days_iter(config):
        history, attribution = workload.queries_for_day(day)
        result.add("query_history", history)
        result.add("query_attribution_history", attribution)
        result.add("warehouse_metering_history", workload.warehouse_metering(day))

        serverless = serverless_task_rows(account, ground_truth, day)
        result.add("serverless_task_history", serverless)
        ai_rows = ai_usage_rows(account, ground_truth, day)
        result.add("cortex_functions_usage_history", ai_rows)

        compute_credits = workload.daily_compute_credits(day)
        serverless_credits = sum(
            (Decimal(r["CREDITS_USED"]) for r in serverless), Decimal(0)
        ).quantize(Decimal("0.000000001"))
        ai_credits = sum((Decimal(r["TOKEN_CREDITS"]) for r in ai_rows), Decimal(0)).quantize(
            Decimal("0.000000001")
        )

        metering = _metering_daily(day, compute_credits, serverless_credits, ai_credits)
        result.add("metering_daily_history", metering)
        billed = sum((Decimal(r["CREDITS_BILLED"]) for r in metering), Decimal(0))
        result.add("usage_in_currency_daily", _usage_in_currency(day, billed, price))

        for source_id, rows in storage_rows(account, ground_truth, day).items():
            result.add(source_id, rows)
        result.add("task_history", task_rows(account, ground_truth, day))
        result.add("dynamic_table_refresh_history", dynamic_table_rows(account, ground_truth, day))
        result.add("login_history", login_rows(account, ground_truth, day))

    # Point-in-time snapshots.
    result.add("warehouses", warehouse_snapshot(account))
    result.add("users", user_snapshot(account, ground_truth))
    result.add("grants_to_users", grant_rows(account, ground_truth))
    result.add("table_storage_metrics", table_storage_metrics(account, ground_truth))
    return result


def write_tables_csv(
    tables: dict[str, list[dict[str, Any]]], output_dir: Path, *, compress: bool = False
) -> dict[str, Path]:
    """Write one CSV per non-empty source, named for the ingestion pipeline.

    Shared by the single-account writer and the organization writer so that an
    account extract and an organization extract are produced by exactly the
    same code — a difference between the two would be a difference the upload
    path could see.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for source_id in sorted(tables):
        rows = tables[source_id]
        if not rows:
            continue
        suffix = ".csv.gz" if compress else ".csv"
        path = output_dir / f"{source_id}{suffix}"
        fieldnames = list(rows[0])
        opener = (
            gzip.open(path, "wt", newline="", encoding="utf-8")
            if compress
            else path.open("w", newline="", encoding="utf-8")
        )
        with opener as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        written[source_id] = path
    return written


def write_manifest(
    output_dir: Path,
    written: dict[str, Path],
    tables: dict[str, list[dict[str, Any]]],
    ground_truth: GroundTruth,
    *,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Write the extract manifest and the ground-truth file beside the data."""
    manifest: dict[str, Any] = {
        "generated_by": "snowobs-fixtures",
        "seed": ground_truth.seed,
        "window": {
            "start": ground_truth.start_date.isoformat(),
            "end": ground_truth.end_date.isoformat(),
        },
        "files": {
            source_id: {"path": path.name, "rows": len(tables.get(source_id, []))}
            for source_id, path in written.items()
        },
    }
    if extra:
        manifest.update(extra)
    manifest_path = output_dir / "03_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (output_dir / "ground_truth.json").write_text(
        ground_truth.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    return manifest_path


def write_csv(
    generated: GeneratedAccount, output_dir: Path, *, compress: bool = False
) -> dict[str, Path]:
    """Write one CSV per source, named so the ingestion pipeline can identify it."""
    written = write_tables_csv(generated.tables, output_dir, compress=compress)
    write_manifest(output_dir, written, generated.tables, generated.ground_truth)
    return written


def write_parquet(generated: GeneratedAccount, output_dir: Path) -> dict[str, Path]:
    """Write one Parquet file per source (types preserved — the preferred path)."""
    import polars as pl

    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for source_id in generated.source_ids:
        rows = generated.tables[source_id]
        if not rows:
            continue
        path = output_dir / f"{source_id}.parquet"
        pl.DataFrame(rows, infer_schema_length=None).write_parquet(path)
        written[source_id] = path
    return written


def summarise(generated: GeneratedAccount) -> dict[str, int]:
    return {source_id: generated.row_count(source_id) for source_id in generated.source_ids}


__all__ = [
    "GeneratedAccount",
    "GeneratorConfig",
    "_dec",
    "generate",
    "summarise",
    "write_csv",
    "write_manifest",
    "write_parquet",
    "write_tables_csv",
]
