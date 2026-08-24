#!/usr/bin/env python3
"""Regenerate the Snowflake grant artefacts from the source registry (R4).

There is exactly one statement of which Snowflake database roles this platform
needs, and it is the source registry: every registered source declares the
granular database role that unlocks it. Both consumers are generated from it —
the provisioning SQL a human runs, and the Terraform input the ``snowflake``
module turns into ``snowflake_grant_database_role`` resources — so a newly
registered view brings its grant with it and the two can never drift apart.

    uv run python scripts/gen_snowflake_grants.py           # write the artefacts
    uv run python scripts/gen_snowflake_grants.py --check   # fail if they are stale

``--check`` is what CI runs: it regenerates into memory and compares, so a
registry change that nobody propagated fails the build instead of silently
under-granting a deployment.

The generated grants are audited before they are written: a blanket
``IMPORTED PRIVILEGES``, ``ACCOUNTADMIN``, or ``SECURITYADMIN`` grant fails the
run outright (§27.3).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from snowobs_live.provisioning import (
    DEFAULT_READER_ROLE,
    audit_script,
    generate_publisher_role_sql,
    generate_reader_role_sql,
)
from snowobs_semantics.registry import default_registry

REPO_ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = REPO_ROOT / "snowflake" / "provisioning"
TFVARS = REPO_ROOT / "deploy" / "terraform" / "snowflake" / "grants.auto.tfvars.json"

#: Snowflake's ORGANIZATION_* database roles are granted in the organization
#: account, not the member account, so Terraform applied against a member
#: account must not try to grant them. They are still emitted, flagged, and the
#: module decides — rather than being quietly dropped here.
ORGANIZATION_ROLE_PREFIX = "SNOWFLAKE.ORGANIZATION_"


def build_tfvars() -> dict[str, object]:
    """The Terraform input: database role → the sources it unlocks."""
    registry = default_registry()
    by_role: dict[str, list[str]] = {}
    for source in registry:
        if source.required_db_role:
            by_role.setdefault(source.required_db_role, []).append(source.id)

    database_roles = {
        role: {
            "sources": sorted(sources),
            "objects": sorted(registry.get(s).snowflake_object for s in sources),
            "organization_scoped": role.startswith(ORGANIZATION_ROLE_PREFIX),
        }
        for role, sources in sorted(by_role.items())
    }
    return {
        "generated_by": (
            "scripts/gen_snowflake_grants.py, from the source registry in "
            "packages/semantics/sources/. Do not edit by hand — run `make provisioning`."
        ),
        "reader_role_name": DEFAULT_READER_ROLE,
        "database_roles": database_roles,
    }


def render_sql() -> dict[Path, str]:
    reader = generate_reader_role_sql()
    publisher = generate_publisher_role_sql()
    for plan in (reader, publisher):
        problems = audit_script(plan.sql)
        if problems:
            raise SystemExit(
                "Generated provisioning SQL violates §27.3:\n  " + "\n  ".join(problems)
            )
    return {
        SQL_DIR / "01_reader_role.sql": reader.sql,
        SQL_DIR / "02_publisher_role.sql": publisher.sql,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gen_snowflake_grants")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; exit non-zero if the Terraform input is out of date.",
    )
    args = parser.parse_args(argv)

    tfvars = json.dumps(build_tfvars(), indent=2, ensure_ascii=False) + "\n"

    if args.check:
        current = TFVARS.read_text(encoding="utf-8") if TFVARS.is_file() else ""
        if current != tfvars:
            print(
                f"{TFVARS.relative_to(REPO_ROOT)} is out of date with the source registry.\n"
                "Run `make provisioning` and commit the result.",
                file=sys.stderr,
            )
            return 1
        print("Snowflake grant artefacts are in sync with the source registry.")
        return 0

    # Rendering the SQL also audits it; do that before writing anything.
    sql_files = render_sql()

    TFVARS.parent.mkdir(parents=True, exist_ok=True)
    TFVARS.write_text(tfvars, encoding="utf-8")
    for path, body in sql_files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    roles = json.loads(tfvars)["database_roles"]
    print(f"Wrote {TFVARS.relative_to(REPO_ROOT)} — {len(roles)} database role(s):")
    for role, detail in roles.items():
        scope = " (organization account)" if detail["organization_scoped"] else ""
        print(f"  {role:<42} {len(detail['sources']):>2} source(s){scope}")
    for path in sql_files:
        print(f"Wrote {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entry point
    sys.exit(main())
