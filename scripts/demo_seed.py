#!/usr/bin/env python3
"""Seed the demo dataset: generate a synthetic account, then ingest it (§19, §24).

This is the *only* way demo data enters the platform, and it goes in through the
same pipeline a customer's own extracts go through — profile → identify → map →
validate → land → drift. Nothing is written straight into the lake, so what the
demo shows is what the real ingestion path produces.

Run it directly::

    uv run python scripts/demo_seed.py                 # anchor the window to today
    uv run python scripts/demo_seed.py --end-date 2026-08-24 --force

or through ``make demo`` / the ``demo-seed`` service in ``docker-compose.demo.yml``,
both of which call this file with the same arguments.

Re-running is a no-op unless ``--force`` is given: the demo stack starts fast on
the second run rather than re-landing a dataset that is already there.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from snowobs_fixtures.config import GeneratorConfig, Scale
from snowobs_fixtures.generator import generate, summarise, write_csv
from snowobs_ingest.catalog import DuckDBCatalog
from snowobs_ingest.loader import IngestPipeline

#: Matches ``DatasetService.storage_root`` for the default (non-``local``)
#: storage providers. Passed explicitly rather than read from settings so the
#: seeder cannot land data somewhere the API will not look for it.
DEFAULT_ROOT = Path(".data")
DEFAULT_TENANT = "default"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="demo_seed",
        description="Generate and ingest the synthetic demo account.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="Storage root the API reads in OFFLINE mode (default: .data)",
    )
    parser.add_argument("--tenant", default=DEFAULT_TENANT, help="Tenant prefix (default: default)")
    parser.add_argument("--seed", type=int, default=42, help="Generator seed (default: 42)")
    parser.add_argument("--days", type=int, default=120, help="Days of history (default: 120)")
    parser.add_argument("--warehouses", type=int, default=12)
    parser.add_argument("--teams", type=int, default=8)
    parser.add_argument(
        "--scale", choices=[s.value for s in Scale], default=Scale.SMALL.value, help="Row volume"
    )
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        default=None,
        help=(
            "Last day of generated history (YYYY-MM-DD). Defaults to today so the "
            "dashboards' 'last 30 days' preset lands inside the data."
        ),
    )
    parser.add_argument(
        "--extracts",
        type=Path,
        default=None,
        help=(
            "Where to write the CSV extracts that are then ingested "
            "(default: <root>/_extracts). They are kept so the upload path can be "
            "re-run by hand from the same files."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-seed even if the tenant already holds landed data.",
    )
    return parser


def _landed_source_count(root: Path, tenant: str) -> int:
    tenant_root = root / tenant
    if not tenant_root.is_dir():
        return 0
    return sum(1 for child in tenant_root.iterdir() if child.is_dir() and any(child.glob("part-*")))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root: Path = args.root
    tenant: str = args.tenant
    extracts: Path = args.extracts if args.extracts is not None else root / "_extracts"

    already = _landed_source_count(root, tenant)
    if already and not args.force:
        print(
            f"Demo data already present: {already} source(s) landed under "
            f"{root / tenant}. Re-run with --force to regenerate."
        )
        return 0

    if args.force:
        shutil.rmtree(root / tenant, ignore_errors=True)
        shutil.rmtree(extracts, ignore_errors=True)

    end_date = args.end_date or datetime.now(tz=UTC).date()
    config = GeneratorConfig(
        seed=args.seed,
        days=args.days,
        warehouses=args.warehouses,
        teams=args.teams,
        scale=Scale(args.scale),
        end_date=end_date,
    )

    print(
        f"==> Generating a synthetic account: seed={args.seed} days={args.days} "
        f"scale={args.scale} window ending {end_date.isoformat()}"
    )
    account = generate(config)
    counts = summarise(account)
    print(f"    {sum(counts.values()):,} rows across {len(counts)} sources")

    print(f"==> Writing extracts to {extracts}")
    write_csv(account, extracts)

    print(f"==> Ingesting through the real upload pipeline into {root / tenant}")
    pipeline = IngestPipeline(root, tenant=tenant)
    summary = pipeline.ingest_directory(extracts)

    landed_rows = summary.total_rows
    print(f"    landed {len(summary.landed)} file(s), {landed_rows:,} rows")
    for result in summary.pending_confirmation:
        print(f"    ! needs confirmation: {result.file_name} — {result.mapping.reason}")
    for result in summary.unrecognised:
        print(f"    ! unrecognised: {result.file_name} — {result.mapping.reason}")

    rejected = sum(r.report.rows_rejected for r in summary.results if r.report is not None)
    if rejected:
        print(f"    {rejected:,} row(s) rejected by validation (see the quality report in-app)")

    with DuckDBCatalog(root, tenant=tenant) as catalog:
        registered = catalog.register_all()
    print(f"==> Catalog registered {len(registered)} source view(s)")

    if not registered:
        print(
            "Seeding produced no queryable sources — the demo would come up empty.", file=sys.stderr
        )
        return 1

    print(f"==> Demo data ready under {root / tenant}")
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entry point
    sys.exit(main())
