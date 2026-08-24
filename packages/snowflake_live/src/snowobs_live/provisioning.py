"""Provisioning SQL generator (BUILD_PROMPT §7.2, R4).

Generates the idempotent, read-only role the platform connects with, built from
**granular Snowflake database roles** — never blanket
``IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE`` (§27.3). The exact grants are
derived from the source registry, so a newly registered view brings its own
grant with it and the script never drifts from what the app actually reads.

The write path (data-product publication) is a *separate* role, generated
separately, and every statement it contains is shown to a human before it runs
(R4, R8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from snowobs_semantics.registry import SourceRegistry, default_registry

DEFAULT_READER_ROLE = "SNOWOBS_READER"
DEFAULT_WRITER_ROLE = "SNOWOBS_PUBLISHER"
DEFAULT_WAREHOUSE = "WH_SNOWOBS_APP"
DEFAULT_MONITOR = "RM_SNOWOBS_APP"

#: Blanket grants that must never appear in generated SQL (§27.3).
FORBIDDEN_GRANTS = ("IMPORTED PRIVILEGES", "ACCOUNTADMIN", "SECURITYADMIN")


@dataclass(frozen=True)
class ProvisioningPlan:
    """The generated script plus what it will grant, for human review (R8)."""

    role: str
    warehouse: str
    statements: list[str]
    #: database role → the sources it unlocks, so a reviewer can see the reason
    #: for every grant rather than a bare list.
    grants: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def sql(self) -> str:
        return "\n".join(self.statements) + "\n"

    def grant_summary(self) -> list[str]:
        return [
            f"{role}: {len(sources)} source(s) — {', '.join(sorted(sources)[:4])}"
            + ("…" if len(sources) > 4 else "")
            for role, sources in sorted(self.grants.items())
        ]


def _required_roles(registry: SourceRegistry) -> dict[str, list[str]]:
    """Which database roles the registered sources actually need."""
    needed: dict[str, list[str]] = {}
    for source in registry:
        role = source.required_db_role
        if not role:
            continue
        needed.setdefault(role, []).append(source.id)
    return {role: sorted(sources) for role, sources in sorted(needed.items())}


def generate_reader_role_sql(
    registry: SourceRegistry | None = None,
    *,
    role: str = DEFAULT_READER_ROLE,
    warehouse: str = DEFAULT_WAREHOUSE,
    create_warehouse: bool = True,
    warehouse_size: str = "XSMALL",
    auto_suspend_seconds: int = 60,
    monthly_credit_quota: int = 50,
    grant_to_role: str = "SYSADMIN",
) -> ProvisioningPlan:
    """Generate the idempotent read-only provisioning script."""
    registry = registry or default_registry()
    needed = _required_roles(registry)
    generated_at = datetime.now(tz=UTC).isoformat(timespec="seconds")

    statements: list[str] = [
        "-- Observability & FinOps Platform for Snowflake — read-only provisioning",
        f"-- Generated {generated_at}",
        "--",
        "-- Idempotent: safe to re-run. Read-only: this script grants SELECT on",
        "-- usage views through Snowflake's granular database roles and creates",
        "-- one small warehouse for the platform's own queries. It grants no",
        "-- privileges on your data, and no blanket IMPORTED PRIVILEGES.",
        "--",
        "-- Run as a role that can create roles and warehouses (typically",
        "-- USERADMIN + SYSADMIN, or ACCOUNTADMIN). The platform itself never",
        "-- runs this: a human reviews and executes it.",
        "",
        "USE ROLE USERADMIN;",
        f"CREATE ROLE IF NOT EXISTS {role}",
        "  COMMENT = 'Read-only role for the Observability & FinOps Platform';",
        "",
        "USE ROLE ACCOUNTADMIN;  -- required to grant SNOWFLAKE database roles",
    ]

    for database_role, sources in needed.items():
        statements.append("")
        statements.append(
            f"-- {database_role}: {len(sources)} source(s) "
            f"({', '.join(sources[:5])}{'…' if len(sources) > 5 else ''})"
        )
        statements.append(f"GRANT DATABASE ROLE {database_role} TO ROLE {role};")

    if create_warehouse:
        statements.extend(
            [
                "",
                "-- A small, resource-monitored warehouse so the platform's own cost is",
                "-- visible and bounded. Its consumption is reported as a first-class",
                "-- KPI (cost.platform_self_cost).",
                "USE ROLE SYSADMIN;",
                f"CREATE WAREHOUSE IF NOT EXISTS {warehouse}",
                f"  WAREHOUSE_SIZE = {warehouse_size}",
                f"  AUTO_SUSPEND = {auto_suspend_seconds}",
                "  AUTO_RESUME = TRUE",
                "  INITIALLY_SUSPENDED = TRUE",
                "  COMMENT = 'Observability & FinOps Platform application warehouse';",
                "",
                "USE ROLE ACCOUNTADMIN;",
                f"CREATE RESOURCE MONITOR IF NOT EXISTS {DEFAULT_MONITOR}",
                f"  WITH CREDIT_QUOTA = {monthly_credit_quota}",
                "  FREQUENCY = MONTHLY",
                "  START_TIMESTAMP = IMMEDIATELY",
                "  -- Notify only. This monitor guards the platform's own warehouse;",
                "  -- production warehouses are never hard-suspended (§14, §27.8).",
                "  TRIGGERS ON 80 PERCENT DO NOTIFY",
                "           ON 100 PERCENT DO NOTIFY;",
                f"ALTER WAREHOUSE {warehouse} SET RESOURCE_MONITOR = {DEFAULT_MONITOR};",
                "",
                "USE ROLE SECURITYADMIN;",
                f"GRANT USAGE ON WAREHOUSE {warehouse} TO ROLE {role};",
            ]
        )

    statements.extend(
        [
            "",
            "-- Grant the reader role to the service user and to an operator role.",
            f"GRANT ROLE {role} TO ROLE {grant_to_role};",
            f"-- GRANT ROLE {role} TO USER <SNOWOBS_SERVICE_USER>;",
            "",
            "-- Verify: this should list the granted database roles.",
            f"SHOW GRANTS TO ROLE {role};",
        ]
    )

    notes = [
        "Key-pair authentication is the default and the recommendation. "
        "Snowflake's MFA rollout completes in October 2026, after which service "
        "users may only use key-pair, OAuth, PAT, or WIF.",
        "ORGANIZATION_USAGE views additionally require the account to have "
        "ORGADMIN enabled, or an organization account; those grants are in the "
        "separate organization section of this script.",
    ]
    return ProvisioningPlan(
        role=role, warehouse=warehouse, statements=statements, grants=needed, notes=notes
    )


def generate_grant_remediation(
    missing_sources: list[str],
    registry: SourceRegistry | None = None,
    *,
    role: str = DEFAULT_READER_ROLE,
) -> list[str]:
    """The copy-pastable fix shown next to each inaccessible source (§7.2, R3)."""
    registry = registry or default_registry()
    by_role: dict[str, list[str]] = {}
    for source_id in missing_sources:
        source = registry.get(source_id)
        if source.required_db_role:
            by_role.setdefault(source.required_db_role, []).append(source.snowflake_object)

    return [
        f"GRANT DATABASE ROLE {database_role} TO ROLE {role};"
        f"  -- unlocks {', '.join(sorted(objects)[:3])}" + ("…" if len(objects) > 3 else "")
        for database_role, objects in sorted(by_role.items())
    ]


def generate_publisher_role_sql(
    *,
    role: str = DEFAULT_WRITER_ROLE,
    database: str = "OBSERVABILITY",
    warehouse: str = DEFAULT_WAREHOUSE,
) -> ProvisioningPlan:
    """The **separate** write role used only for data-product publication (R4).

    Deliberately not merged with the reader role: the platform's day-to-day
    connection must be incapable of writing to the customer's account, and a
    publication must be an explicit, separately-authorised act (R8).
    """
    generated_at = datetime.now(tz=UTC).isoformat(timespec="seconds")
    statements = [
        "-- Observability & FinOps Platform — data-product PUBLISHER role",
        f"-- Generated {generated_at}",
        "--",
        "-- This role can create objects in ONE database, and nothing else. It is",
        "-- separate from the read-only role the platform connects with day to day,",
        "-- and every statement it executes is shown to a human for approval first.",
        "",
        "USE ROLE USERADMIN;",
        f"CREATE ROLE IF NOT EXISTS {role}",
        f"  COMMENT = 'Publishes observability data products. Write scope: {database} only.';",
        "",
        "USE ROLE SYSADMIN;",
        f"CREATE DATABASE IF NOT EXISTS {database}",
        "  COMMENT = 'Published observability data products';",
        f"CREATE SCHEMA IF NOT EXISTS {database}.PUBLISHED;",
        f"CREATE SCHEMA IF NOT EXISTS {database}.SEMANTIC;",
        "",
        f"GRANT USAGE ON DATABASE {database} TO ROLE {role};",
        f"GRANT ALL ON SCHEMA {database}.PUBLISHED TO ROLE {role};",
        f"GRANT ALL ON SCHEMA {database}.SEMANTIC TO ROLE {role};",
        f"GRANT USAGE ON WAREHOUSE {warehouse} TO ROLE {role};",
        "",
        "-- Publication also needs the reader role's SELECT access to build the",
        "-- published views from the usage data.",
        f"GRANT ROLE {DEFAULT_READER_ROLE} TO ROLE {role};",
        "",
        f"SHOW GRANTS TO ROLE {role};",
    ]
    return ProvisioningPlan(
        role=role,
        warehouse=warehouse,
        statements=statements,
        grants={},
        notes=[
            "This role is used only during an approved publication and is not the "
            "platform's standing connection.",
        ],
    )


def audit_script(sql: str) -> list[str]:
    """Return any §27.3 violations found in generated SQL. Empty means clean."""
    problems: list[str] = []
    upper = sql.upper()
    for forbidden in FORBIDDEN_GRANTS:
        for line in upper.splitlines():
            stripped = line.strip()
            if stripped.startswith("--"):
                continue  # a comment mentioning the role is not a grant of it
            if forbidden in stripped and stripped.startswith("GRANT "):
                problems.append(f"Generated SQL grants {forbidden}: {line.strip()}")
    return problems
