"""Extract-kit generator (BUILD_PROMPT §7.3).

Produces the files an operator runs inside their own Snowflake account to
produce an OFFLINE upload: the COPY INTO script, a download script, and a
manifest the app validates the upload against. Everything is generated from the
source registry, so a newly registered view appears in the kit with no code
change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from snowobs_semantics.registry import (
    LoadStrategy,
    SourceRegistry,
    SourceScope,
    default_registry,
)

STAGE = "@~/snowobs"


@dataclass(frozen=True)
class ExtractKit:
    """The downloadable kit: file name → contents."""

    files: dict[str, str]

    def __getitem__(self, name: str) -> str:
        return self.files[name]

    @property
    def names(self) -> list[str]:
        return sorted(self.files)


def _copy_statement(
    snowflake_object: str, source_id: str, time_column: str | None, days: int, file_format: str
) -> str:

    # from registry-owned identifiers; the platform never executes it.
    where = (
        f"\n    WHERE {time_column} >= DATEADD(day, -{days}, CURRENT_DATE())" if time_column else ""
    )
    if file_format == "PARQUET":
        options = "FILE_FORMAT = (TYPE = PARQUET COMPRESSION = SNAPPY)\n  HEADER = TRUE"
    else:
        options = (
            "FILE_FORMAT = (TYPE = CSV COMPRESSION = GZIP "
            "FIELD_OPTIONALLY_ENCLOSED_BY = '\"' NULL_IF = ('') "
            "EMPTY_FIELD_AS_NULL = TRUE)\n"
            "  HEADER = TRUE"
        )
    return (
        f"COPY INTO {STAGE}/{source_id}/\n"
        f"FROM (\n"
        f"    SELECT *\n"
        f"    FROM {snowflake_object}{where}\n"
        f")\n"
        f"  {options}\n"
        f"  OVERWRITE = TRUE\n"
        f"  MAX_FILE_SIZE = 512000000;\n"
    )


def _scope_banner(scope: SourceScope) -> str:
    """Header separating the per-account block from the organization block."""
    if scope is SourceScope.ORGANIZATION:
        return (
            "-- ======================================================================\n"
            "-- ORGANIZATION_USAGE — run this block ONCE, in the organization account\n"
            "-- (or a regular account with ORGADMIN enabled). These views name every\n"
            "-- account in the organization in their own ACCOUNT_NAME column, so they\n"
            "-- are exported once for the whole fleet rather than once per account.\n"
            "-- ======================================================================\n"
        )
    return (
        "-- ======================================================================\n"
        "-- ACCOUNT_USAGE — run this block in EACH account you want observed.\n"
        "-- These views carry no account column, so keep each account's download in\n"
        "-- its own directory and tell the platform which account it came from when\n"
        "-- you upload it; that is what the coverage matrix reports per account.\n"
        "-- ======================================================================\n"
    )


def _show_statement(source_id: str) -> str:
    # SHOW-based sources have no COPY path; the operator downloads the grid.
    return (
        f"-- {source_id}: run this and download the result grid as CSV named "
        f"'{source_id}.csv'\n"
        f"SHOW WAREHOUSES;\n"
    )


def generate_extract_kit(
    registry: SourceRegistry | None = None,
    *,
    days: int = 120,
    file_format: str = "PARQUET",
    warehouse: str | None = None,
    include_optional: bool = True,
) -> ExtractKit:
    """Generate the tailored extract kit for the registered sources."""
    registry = registry or default_registry()
    generated_at = datetime.now(tz=UTC).isoformat(timespec="seconds")
    fmt = file_format.upper()
    if fmt not in {"CSV", "PARQUET"}:
        raise ValueError("file_format must be CSV or PARQUET")

    selected = [
        source
        for source in registry
        if include_optional or source.criticality.value in {"core", "important"}
    ]
    # Account-scoped views first, then the organization-scoped ones: an
    # enterprise runs the first block once per account and the second block once,
    # in the ORGADMIN-enabled or organization account.
    selected.sort(key=lambda s: (s.scope.value, s.criticality.value, s.domain, s.id))

    extract_lines = [
        "-- Observability & FinOps Platform for Snowflake — extract script",
        f"-- Generated {generated_at} · window: last {days} days · format: {fmt}",
        "--",
        "-- Run as a role holding the granular SNOWFLAKE database roles listed per view.",
        "-- This script only READS. It writes to your own user stage (@~), never to a",
        "-- shared location, and creates no objects in your databases.",
        "",
    ]
    if warehouse:
        extract_lines.append(f"USE WAREHOUSE {warehouse};")
    extract_lines.append(f"CREATE STAGE IF NOT EXISTS {STAGE.removeprefix('@~/')};")
    extract_lines.append("")

    manifest_files: list[dict[str, object]] = []
    emitted_scopes: set[str] = set()
    for source in selected:
        if source.scope.value not in emitted_scopes:
            emitted_scopes.add(source.scope.value)
            extract_lines.append(_scope_banner(source.scope))
        role = source.required_db_role or "(no additional grant required)"
        extract_lines.append(
            f"-- {source.id} · {source.snowflake_object}\n"
            f"-- criticality={source.criticality.value} · edition>={source.edition_min.value} "
            f"· scope={source.scope.value} · requires {role}"
        )
        if source.snowflake_object.startswith("SHOW "):
            extract_lines.append(_show_statement(source.id))
        else:
            window_days = days if source.load_strategy is LoadStrategy.INCREMENTAL_WATERMARK else 0
            extract_lines.append(
                _copy_statement(
                    source.snowflake_object,
                    source.id,
                    source.time_column if window_days else None,
                    window_days or days,
                    fmt,
                )
            )
        manifest_files.append(
            {
                "source_id": source.id,
                "snowflake_object": source.snowflake_object,
                "expected_prefix": f"{source.id}/",
                "criticality": source.criticality.value,
                "scope": source.scope.value,
                "required_db_role": source.required_db_role,
            }
        )

    download_sh = f"""#!/usr/bin/env bash
# Download the extract produced by 01_extract.sql, then zip it for upload.
# Requires the Snowflake CLI (`snow`) with a configured connection.
set -euo pipefail

CONNECTION="${{1:-default}}"
OUT="${{2:-./snowobs-extract}}"
mkdir -p "$OUT"

echo "Downloading from {STAGE} using connection '$CONNECTION'..."
snow sql --connection "$CONNECTION" --query "GET {STAGE}/ file://$OUT/" --format json > /dev/null

# Flatten the per-source directories into <source_id>.<ext> files the app expects.
find "$OUT" -mindepth 2 -type f | while read -r file; do
  source_id="$(basename "$(dirname "$file")")"
  ext="${{file##*.}}"
  mv "$file" "$OUT/${{source_id}}_$(basename "$file")" 2>/dev/null || true
done
find "$OUT" -mindepth 1 -type d -empty -delete

cp "$(dirname "$0")/03_manifest.json" "$OUT/" 2>/dev/null || true
( cd "$OUT" && zip -r ../snowobs-extract.zip . )
echo "Wrote snowobs-extract.zip — upload this file to the platform."
"""

    download_ps1 = f"""# Download the extract produced by 01_extract.sql (Windows / PowerShell).
param(
  [string]$Connection = "default",
  [string]$Out = ".\\snowobs-extract"
)
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $Out | Out-Null

Write-Host "Downloading from {STAGE} using connection '$Connection'..."
snow sql --connection $Connection --query "GET {STAGE}/ file://$Out/" | Out-Null

Get-ChildItem -Path $Out -Recurse -File | ForEach-Object {{
  $sourceId = Split-Path $_.DirectoryName -Leaf
  Move-Item $_.FullName (Join-Path $Out "$($sourceId)_$($_.Name)") -Force
}}
Get-ChildItem -Path $Out -Recurse -Directory |
  Where-Object {{ -not (Get-ChildItem $_.FullName) }} | Remove-Item

Copy-Item (Join-Path $PSScriptRoot "03_manifest.json") $Out -ErrorAction SilentlyContinue
Compress-Archive -Path "$Out\\*" -DestinationPath ".\\snowobs-extract.zip" -Force
Write-Host "Wrote snowobs-extract.zip - upload this file to the platform."
"""

    import json

    manifest = json.dumps(
        {
            "kit_version": 1,
            "generated_at": generated_at,
            "window_days": days,
            "file_format": fmt,
            "files": manifest_files,
            "checksums": {},  # filled by the download script or left for the app to compute
        },
        indent=2,
    )

    readme = f"""# Extract kit — Observability & FinOps Platform for Snowflake

Generated {generated_at}. Window: the last **{days} days**. Format: **{fmt}**.

## What this does

Exports the Snowflake usage views the platform reads, to *your own user stage*,
so you can download them and upload them to the platform in OFFLINE mode. The
script is read-only: it creates no tables, alters no objects, and touches no
data outside `{STAGE}`.

## Steps

1. **Run `01_extract.sql`** in Snowsight (or `snow sql -f 01_extract.sql`) as a
   role holding the granular database roles noted above each statement
   (`SNOWFLAKE.USAGE_VIEWER`, `GOVERNANCE_VIEWER`, `SECURITY_VIEWER`,
   `OBJECT_VIEWER`). Statements for views you cannot read will error — that is
   expected and safe; the platform degrades gracefully for whatever is missing.
2. **Run `02_download.sh`** (or `02_download.ps1` on Windows) to `GET` the files
   and produce `snowobs-extract.zip`.
3. **Upload the zip** in the platform's onboarding wizard. The coverage matrix
   will show exactly what landed and what is still missing.

## Organizations with more than one account

The script is in two blocks. Run the **ACCOUNT_USAGE** block in *each* account
you want observed, keeping each account's download in its own directory, and
name the account when you upload it — those views carry no account column, so
the platform records the provenance itself and cannot infer it. Run the
**ORGANIZATION_USAGE** block *once*, in the organization account (or a regular
account with ORGADMIN enabled): those views already name every account in the
organization, and exporting them from each account would produce contradictory
copies of the same organization-wide table.

## If stages or the CLI are blocked

Run each `SELECT` from `01_extract.sql` directly in Snowsight and download the
result grid as CSV, naming each file `<source_id>.csv` (e.g. `query_history.csv`).
Snowsight's result download is **row-limited**, so large views will be truncated:
the platform will show the shortened window on the coverage page rather than
pretending the data is complete. Prefer the stage path whenever it is available.

## Notes on freshness

Each view has its own documented latency (`QUERY_HISTORY` ~45 min,
`QUERY_ATTRIBUTION_HISTORY` ~8 h, `METERING_DAILY_HISTORY` ~3 h, the
`ORGANIZATION_USAGE` currency views 24–72 h with month-end restatement). Figures
inside a restatement window are flagged provisional in the app.
"""

    return ExtractKit(
        files={
            "01_extract.sql": "\n".join(extract_lines) + "\n",
            "02_download.sh": download_sh,
            "02_download.ps1": download_ps1,
            "03_manifest.json": manifest + "\n",
            "README.md": readme,
        }
    )
