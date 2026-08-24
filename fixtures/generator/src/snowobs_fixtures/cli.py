"""``snowobs-generate`` — write a synthetic account, or a whole organization.

``--organization`` switches from one account to the shipped fleet of four
accounts under one organization: one directory of ACCOUNT_USAGE extracts per
account, plus one directory of ORGANIZATION_USAGE extracts, which is how an
enterprise actually exports.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from snowobs_fixtures.config import GeneratorConfig, Scale
from snowobs_fixtures.generator import generate, summarise, write_csv, write_parquet
from snowobs_fixtures.organization import (
    OrganizationConfig,
    generate_organization,
    summarise_organization,
    write_organization_csv,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snowobs-generate",
        description="Generate a deterministic synthetic Snowflake account for snowobs.",
    )
    parser.add_argument("--out", type=Path, required=True, help="Output directory")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--warehouses", type=int, default=12)
    parser.add_argument("--teams", type=int, default=8)
    parser.add_argument("--scale", choices=[s.value for s in Scale], default=Scale.SMALL.value)
    parser.add_argument("--queries-per-day", type=int, default=None)
    parser.add_argument(
        "--end-date", type=date.fromisoformat, default=date(2026, 8, 20), help="YYYY-MM-DD anchor"
    )
    parser.add_argument("--format", choices=["csv", "csv.gz", "parquet", "both"], default="csv")
    parser.add_argument(
        "--organization",
        action="store_true",
        help="Generate the whole organization (several accounts) instead of one account.",
    )
    parser.add_argument(
        "--org-name",
        default=OrganizationConfig.organization_name,
        help="Organization name used in the ORGANIZATION_USAGE extracts.",
    )
    return parser


def _generate_organization(args: argparse.Namespace) -> int:
    if args.format not in ("csv", "csv.gz"):
        print(
            "Organization extracts are written as CSV; re-run with --format csv or csv.gz.",
            file=sys.stderr,
        )
        return 2
    config = OrganizationConfig(
        organization_name=args.org_name,
        seed=args.seed,
        days=args.days,
        end_date=args.end_date,
        scale=Scale(args.scale),
    )
    organization = generate_organization(config)
    layout = write_organization_csv(organization, args.out, compress=args.format == "csv.gz")

    counts = summarise_organization(organization)
    total = sum(counts.values())
    print(
        f"Generated organization {organization.organization_name}: {total:,} rows "
        f"across {len(organization.accounts)} accounts into {args.out}"
    )
    for name, directory in layout.account_dirs.items():
        print(f"  {name:<20} {counts[name]:>9,} rows  {directory}")
    print(
        f"  {organization.organization_name:<20} "
        f"{counts[organization.organization_name]:>9,} rows  {layout.organization_dir}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.organization:
        return _generate_organization(args)
    config = GeneratorConfig(
        seed=args.seed,
        days=args.days,
        warehouses=args.warehouses,
        teams=args.teams,
        scale=Scale(args.scale),
        queries_per_day=args.queries_per_day,
        end_date=args.end_date,
    )
    generated = generate(config)

    if args.format in ("csv", "both"):
        write_csv(generated, args.out)
    if args.format == "csv.gz":
        write_csv(generated, args.out, compress=True)
    if args.format in ("parquet", "both"):
        write_parquet(generated, args.out)

    counts = summarise(generated)
    total = sum(counts.values())
    print(f"Generated {total:,} rows across {len(counts)} sources into {args.out}")
    for source_id, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {source_id:<38} {count:>9,}")
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
