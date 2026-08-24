"""Capability probe and the Coverage & Grants report (BUILD_PROMPT §7.2).

On connect, and on demand, the platform asks of every registered source: can we
read it, how fresh is it, and how much is there? Anything inaccessible comes
back with the *exact* `GRANT DATABASE ROLE` statement that would fix it — R3
made operational, and the reason a five-minute LIVE onboarding is possible.

The probe is deliberately cheap: a `SELECT ... LIMIT 1` and a windowed
`MAX(time_column)` per source, on the smallest warehouse. It never scans a
retention window.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from snowobs_common.logging import get_logger
from snowobs_live.provisioning import generate_grant_remediation
from snowobs_semantics.registry import SourceDefinition, SourceRegistry, default_registry

if TYPE_CHECKING:  # pragma: no cover - typing only
    from snowflake.connector import SnowflakeConnection

logger = get_logger(__name__)

#: How far back the probe looks when counting rows. Never the full retention.
PROBE_WINDOW_DAYS = 7


class ProbeStatus(StrEnum):
    ACCESSIBLE = "accessible"
    MISSING_GRANT = "missing_grant"
    NOT_APPLICABLE = "not_applicable"  # edition or feature gate
    EMPTY = "empty"  # readable, but no rows in the window
    ERROR = "error"


class SqlRunner(Protocol):
    """Executes a statement and returns rows. Implemented by the live engine."""

    def fetch(self, sql: str) -> list[tuple[Any, ...]]: ...


@dataclass
class SourceProbe:
    """What the probe learned about one source."""

    source_id: str
    snowflake_object: str
    status: ProbeStatus
    required_db_role: str | None = None
    row_count: int | None = None
    max_timestamp: datetime | None = None
    freshness_minutes: float | None = None
    documented_latency_minutes: int = 0
    error: str | None = None
    #: The copy-pastable fix (R3). Empty when nothing needs fixing.
    remediation: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.status is ProbeStatus.ACCESSIBLE

    @property
    def stale(self) -> bool:
        if self.freshness_minutes is None:
            return False
        return self.freshness_minutes > self.documented_latency_minutes * 2


@dataclass
class CoverageAndGrantsReport:
    """The onboarding screen: what works, what does not, and how to fix it."""

    probed_at: datetime
    account: str
    role: str | None
    sources: list[SourceProbe]
    #: Roles that would unlock the most, ranked — the "grant these three" list.
    suggested_grants: list[str] = field(default_factory=list)

    @property
    def accessible(self) -> list[SourceProbe]:
        return [s for s in self.sources if s.usable]

    @property
    def blocked(self) -> list[SourceProbe]:
        return [s for s in self.sources if s.status is ProbeStatus.MISSING_GRANT]

    @property
    def coverage_pct(self) -> float:
        return len(self.accessible) / len(self.sources) * 100 if self.sources else 0.0

    def summary(self) -> str:
        if not self.blocked:
            return (
                f"All {len(self.accessible)} registered sources are readable "
                f"({self.coverage_pct:.0f}% coverage)."
            )
        return (
            f"{len(self.accessible)} of {len(self.sources)} sources readable "
            f"({self.coverage_pct:.0f}%). {len(self.blocked)} need a grant — "
            f"{len(self.suggested_grants)} statement(s) would fix them."
        )


_SAFE_OBJECT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,2}$")
_SAFE_COLUMN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _vet(name: str, pattern: re.Pattern[str]) -> str:
    """Reject anything that is not a bare (qualified) identifier.

    These names come from the source registry rather than a user, but they
    still reach SQL by interpolation — so they are vetted rather than trusted.
    There is no string-concatenation escape hatch anywhere (R9).
    """
    if not pattern.match(name):
        raise ValueError(f"Unsafe SQL identifier in source registry: {name!r}")
    return name


def _probe_statements(source: SourceDefinition) -> tuple[str, str | None]:
    """(accessibility probe, freshness probe). Both are deliberately tiny."""
    if source.snowflake_object.startswith("SHOW "):
        return source.snowflake_object, None

    obj = _vet(source.snowflake_object, _SAFE_OBJECT)
    access = f"SELECT 1 FROM {obj} LIMIT 1"
    if source.time_column:
        column = _vet(source.time_column, _SAFE_COLUMN)
        freshness = (
            f"SELECT MAX({column}), COUNT(*) FROM {obj} "
            f"WHERE {column} >= DATEADD(day, -{PROBE_WINDOW_DAYS}, CURRENT_TIMESTAMP())"
        )
    else:
        freshness = f"SELECT NULL, COUNT(*) FROM {obj}"
    return access, freshness


def _classify_error(message: str) -> ProbeStatus:
    lowered = message.lower()
    if "does not exist or not authorized" in lowered or "insufficient privileges" in lowered:
        return ProbeStatus.MISSING_GRANT
    if "unsupported feature" in lowered or "not enabled" in lowered:
        return ProbeStatus.NOT_APPLICABLE
    return ProbeStatus.ERROR


def probe_source(
    runner: SqlRunner,
    source: SourceDefinition,
    *,
    now: datetime | None = None,
) -> SourceProbe:
    """Probe one source. Never raises: an unreadable source is a finding."""
    reference = now or datetime.now(tz=UTC)
    probe = SourceProbe(
        source_id=source.id,
        snowflake_object=source.snowflake_object,
        status=ProbeStatus.ERROR,
        required_db_role=source.required_db_role,
        documented_latency_minutes=source.documented_latency_minutes,
    )
    access_sql, freshness_sql = _probe_statements(source)

    try:
        runner.fetch(access_sql)
    except Exception as exc:
        probe.status = _classify_error(str(exc))
        probe.error = str(exc).split("\n")[0][:300]
        if probe.status is ProbeStatus.MISSING_GRANT:
            probe.remediation = generate_grant_remediation([source.id])
        return probe

    probe.status = ProbeStatus.ACCESSIBLE
    if freshness_sql is None:
        return probe

    try:
        rows = runner.fetch(freshness_sql)
    except Exception as exc:
        # Readable but not summarisable — still usable, worth reporting.
        probe.error = str(exc).split("\n")[0][:300]
        return probe

    if rows and len(rows[0]) >= 2:
        # Indexed defensively: this runs on the onboarding screen, and an
        # unexpected row shape there should degrade to "readable, freshness
        # unknown" rather than raise an IndexError over a connection that is
        # in fact working.
        newest, count = rows[0][0], rows[0][1]
        probe.row_count = int(count) if count is not None else None
        if isinstance(newest, datetime):
            stamp = newest if newest.tzinfo else newest.replace(tzinfo=UTC)
            probe.max_timestamp = stamp
            probe.freshness_minutes = max((reference - stamp).total_seconds() / 60.0, 0.0)
        if probe.row_count == 0:
            probe.status = ProbeStatus.EMPTY
            probe.remediation = [
                f"-- {source.snowflake_object} is readable but empty over the last "
                f"{PROBE_WINDOW_DAYS} days. This is expected if the feature is unused."
            ]
    return probe


def probe_all(
    runner: SqlRunner,
    registry: SourceRegistry | None = None,
    *,
    account: str = "",
    role: str | None = None,
    now: datetime | None = None,
) -> CoverageAndGrantsReport:
    """Probe every registered source and build the onboarding report."""
    registry = registry or default_registry()
    reference = now or datetime.now(tz=UTC)
    probes = [probe_source(runner, source, now=reference) for source in registry]

    blocked_ids = [p.source_id for p in probes if p.status is ProbeStatus.MISSING_GRANT]
    suggested = generate_grant_remediation(blocked_ids, registry) if blocked_ids else []

    logger.info(
        "capability_probe_complete",
        accessible=sum(1 for p in probes if p.usable),
        blocked=len(blocked_ids),
        total=len(probes),
    )
    return CoverageAndGrantsReport(
        probed_at=reference,
        account=account,
        role=role,
        sources=probes,
        suggested_grants=suggested,
    )


class ConnectionRunner:
    """Adapts a live Snowflake connection to the :class:`SqlRunner` protocol."""

    def __init__(self, connection: SnowflakeConnection) -> None:
        self.connection = connection

    def fetch(self, sql: str) -> list[tuple[Any, ...]]:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql)
            return list(cursor.fetchall())
        finally:
            cursor.close()


@dataclass
class OrganizationProbeReport:
    """What the platform can see across an entire organization.

    An enterprise's first question on connecting is not "does this work" but
    "which of my accounts can you actually see, and how deeply". The answer is
    rarely uniform: billing rolls up from one account granted
    `ORGANIZATION_USAGE`, while query-level detail exists only where an account
    has its own connection and grants. Reporting a single overall percentage
    would hide exactly the gaps an onboarding team needs to close.
    """

    probed_at: datetime
    organization: str | None
    #: Per account, in the order they were probed.
    accounts: list[CoverageAndGrantsReport] = field(default_factory=list)
    #: The account whose connection reads ORGANIZATION_USAGE, if any succeeded.
    organization_reader: str | None = None
    #: Accounts named in ORGANIZATION_USAGE that have no connection configured.
    #: These appear in every billing roll-up and in no detail view, which is the
    #: gap most likely to be mistaken for "that account is quiet".
    unconnected_accounts: list[str] = field(default_factory=list)
    #: Accounts whose connection could not be opened at all, with the reason.
    unreachable_accounts: dict[str, str] = field(default_factory=dict)

    @property
    def organization_wide_sources_available(self) -> bool:
        """Can the platform answer any organization-level question at all?"""
        if self.organization_reader is None:
            return False
        report = self.account(self.organization_reader)
        return report is not None and any(
            probe.usable for probe in report.sources if _is_organization_scoped(probe.source_id)
        )

    def account(self, name: str) -> CoverageAndGrantsReport | None:
        return next((report for report in self.accounts if report.account == name), None)

    def summary(self) -> str:
        if not self.accounts and not self.unreachable_accounts:
            return "No Snowflake account is configured, so nothing was probed."

        reachable = len(self.accounts)
        total = reachable + len(self.unreachable_accounts)
        parts = [f"{reachable} of {total} configured account(s) reachable."]

        if self.organization_wide_sources_available:
            parts.append(f"Organization-wide billing reads from {self.organization_reader}.")
        else:
            parts.append(
                "No account can read ORGANIZATION_USAGE, so cross-account roll-ups "
                "are unavailable — grant ORGANIZATION_USAGE_VIEWER to one account's role."
            )

        if self.unconnected_accounts:
            parts.append(
                f"{len(self.unconnected_accounts)} account(s) appear in the organization "
                f"but have no connection: {', '.join(sorted(self.unconnected_accounts))}. "
                "Their spend is counted; their queries are not visible."
            )
        return " ".join(parts)


def _is_organization_scoped(source_id: str) -> bool:
    """Does this source return the whole organization rather than one account?

    Decided by the schema the view lives in: `SNOWFLAKE.ORGANIZATION_USAGE.*`
    spans every account and `SNOWFLAKE.ACCOUNT_USAGE.*` never does. A declared
    `scope` on the source is preferred when one exists, and a test asserts the
    two agree — a declaration disagreeing with the object name would be the
    more misleading of the two, since the object name is where rows come from.
    """
    from snowobs_semantics.registry import default_registry

    try:
        source = default_registry().get(source_id)
    except Exception:
        return False
    declared = getattr(source, "scope", None)
    if declared is not None:
        return str(getattr(declared, "value", declared)) == "organization"
    return "ORGANIZATION_USAGE" in source.snowflake_object.upper()


def probe_organization(
    runners: Mapping[str, SqlRunner],
    *,
    organization: str | None = None,
    organization_reader: str | None = None,
    registry: SourceRegistry | None = None,
    roles: Mapping[str, str | None] | None = None,
    unreachable: Mapping[str, str] | None = None,
    known_accounts: Sequence[str] = (),
    now: datetime | None = None,
) -> OrganizationProbeReport:
    """Probe every connected account and report the organization's coverage.

    ``runners`` maps account name to an open runner; ``known_accounts`` is what
    `ORGANIZATION_USAGE` says the organization contains, so the report can name
    the accounts that are billed but invisible.
    """
    registry = registry or default_registry()
    reference = now or datetime.now(tz=UTC)
    roles = roles or {}

    reports = [
        probe_all(
            runner,
            registry,
            account=name,
            role=roles.get(name),
            now=reference,
        )
        for name, runner in runners.items()
    ]

    reader = organization_reader if organization_reader in runners else None
    connected = set(runners) | set(unreachable or {})
    missing = sorted(set(known_accounts) - connected)

    logger.info(
        "organization_probe_complete",
        accounts_probed=len(reports),
        unreachable=len(unreachable or {}),
        unconnected=len(missing),
    )
    return OrganizationProbeReport(
        probed_at=reference,
        organization=organization,
        accounts=reports,
        organization_reader=reader,
        unconnected_accounts=missing,
        unreachable_accounts=dict(unreachable or {}),
    )
