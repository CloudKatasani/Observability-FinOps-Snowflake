"""Coverage matrix (BUILD_PROMPT §7.4) — the page that makes R3 real.

For every registered source: is it present, how many rows, what window, how
fresh against its documented latency, and how many KPIs it enables. For every
KPI: enabled, degraded, or unavailable — and which missing source blocks it.

An enterprise runs many accounts under one organization, and "the source is
present" is not a useful answer when account A has query history landed and
account B has only billing. Every source therefore also reports its status
**per account** (``SourceCoverage.accounts``), derived from the ``_ACCOUNT``
stamp ingest records. The top-level figures keep their original meaning — the
whole tenant, every account together — so a single-account deployment sees
exactly what it saw before, with an empty per-account list.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from snowobs_ingest.catalog import DuckDBCatalog, SourceStats, freshness_minutes
from snowobs_semantics.registry import SourceDefinition, SourceRegistry, SourceScope


class SourceStatus(StrEnum):
    AVAILABLE = "available"
    STALE = "stale"  # present but older than its documented latency allows
    EMPTY = "empty"  # matched and landed, but no usable rows
    MISSING = "missing"  # never uploaded / no grant


class MetricAvailability(StrEnum):
    ENABLED = "enabled"
    DEGRADED = "degraded"  # optional inputs missing; the figure is still sound
    UNAVAILABLE = "unavailable"


class AccountCoverage(BaseModel):
    """One account's slice of one source.

    Present only when ingest recorded which account a batch came from; the
    account is never inferred from the rows, because ACCOUNT_USAGE extracts
    carry no account column to infer it from.
    """

    account: str
    status: SourceStatus
    rows: int = 0
    batches: int = 0
    window_start: date | None = None
    window_end: date | None = None
    freshness_minutes: float | None = None

    @property
    def blocking(self) -> bool:
        return self.status in (SourceStatus.MISSING, SourceStatus.EMPTY)


class SourceCoverage(BaseModel):
    source_id: str
    snowflake_object: str
    domain: str
    criticality: str
    #: "account" or "organization" — an organization-scoped view is exported
    #: once for the whole fleet, not once per account.
    scope: str = SourceScope.ACCOUNT.value
    status: SourceStatus
    rows: int = 0
    batches: int = 0
    window_start: date | None = None
    window_end: date | None = None
    freshness_minutes: float | None = None
    documented_latency_minutes: int
    latency_verified: bool = True
    #: How to fix it — copy-pastable remediation, never a bare "no data".
    remediation: str | None = None
    enables_metric_count: int = 0
    #: Per-account status, empty when the lake records no account stamps.
    accounts: list[AccountCoverage] = Field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return self.status in (SourceStatus.MISSING, SourceStatus.EMPTY)

    def for_account(self, account: str) -> AccountCoverage | None:
        return next((a for a in self.accounts if a.account == account), None)


class MetricCoverage(BaseModel):
    metric_id: str
    availability: MetricAvailability
    required_sources: list[str] = Field(default_factory=list)
    missing_sources: list[str] = Field(default_factory=list)
    #: The user-facing explanation required by R3.
    explanation: str


class CoverageMatrix(BaseModel):
    as_of: datetime
    mode: str
    sources: list[SourceCoverage]
    metrics: list[MetricCoverage] = Field(default_factory=list)
    #: Accounts whose extracts are present in this tenant's lake. Empty for a
    #: deployment that never told ingest which account it was uploading.
    accounts: list[str] = Field(default_factory=list)

    @property
    def available_sources(self) -> list[SourceCoverage]:
        return [s for s in self.sources if s.status is SourceStatus.AVAILABLE]

    @property
    def missing_core_sources(self) -> list[SourceCoverage]:
        return [s for s in self.sources if s.criticality == "core" and s.blocking]

    def source(self, source_id: str) -> SourceCoverage:
        return next(s for s in self.sources if s.source_id == source_id)

    def account_matrix(self, account: str) -> list[AccountCoverage]:
        """Every source's status for one account, in the matrix's source order.

        A source the account has landed nothing for reports MISSING for that
        account even when a sibling account has it — which is the whole point:
        "account X has query history, account Y only has billing" is a coverage
        answer, not a silence.
        """
        rows: list[AccountCoverage] = []
        for source in self.sources:
            existing = source.for_account(account)
            rows.append(
                existing
                if existing is not None
                else AccountCoverage(account=account, status=SourceStatus.MISSING)
            )
        return rows

    def data_window(self) -> tuple[date, date] | None:
        starts = [s.window_start for s in self.sources if s.window_start]
        ends = [s.window_end for s in self.sources if s.window_end]
        if not starts or not ends:
            return None
        return min(starts), max(ends)


def _remediation_for(source: SourceDefinition, status: SourceStatus, mode: str) -> str | None:
    if status is SourceStatus.AVAILABLE:
        return None
    if mode == "live":
        if source.required_db_role:
            return (
                f"GRANT DATABASE ROLE {source.required_db_role} TO ROLE <SNOWOBS_READER>; "
                f"-- enables {source.snowflake_object}"
            )
        return f"Verify SELECT access to {source.snowflake_object}."
    if status is SourceStatus.EMPTY:
        return (
            f"{source.snowflake_object} was uploaded but contained no usable rows. "
            "Re-export with a wider time window, or check the extract's WHERE clause."
        )
    if status is SourceStatus.STALE:
        # A stale source landed once and then aged out, so telling its owner to
        # "upload an extract" reads as though nothing was ever provided and
        # sends them looking for a problem that is not there.
        return (
            f"{source.snowflake_object} landed but its newest row is older than "
            f"{source.documented_latency_minutes} minutes of documented latency allows. "
            f"Re-export it and upload '{source.id}.csv' or '{source.id}.parquet' again; "
            "if the export is running on a schedule, check that the schedule is still firing."
        )
    return (
        f"Upload an extract of {source.snowflake_object} "
        f"(expected file name '{source.id}.csv' or '{source.id}.parquet'). "
        "The extract kit generates the COPY INTO statement for this view."
    )


def _assess(
    source: SourceDefinition, stats: SourceStats | None, reference: datetime
) -> tuple[SourceStatus, int, int, tuple[date, date] | None, float | None]:
    """Status, rows, batches, window, freshness for one set of statistics."""
    if stats is None:
        return SourceStatus.MISSING, 0, 0, None, None
    if stats.rows == 0:
        return SourceStatus.EMPTY, 0, stats.batches, None, None
    freshness = freshness_minutes(stats, as_of=reference)
    # A snapshot source has no time column; it cannot be judged stale.
    if freshness is None or source.time_column is None:
        status = SourceStatus.AVAILABLE
    else:
        # Allow the documented latency plus a day of extract age before
        # calling a source stale — R7 honesty, not alarmism.
        budget = source.documented_latency_minutes + 24 * 60
        status = SourceStatus.AVAILABLE if freshness <= budget else SourceStatus.STALE
    return status, stats.rows, stats.batches, stats.window, freshness


def build_source_coverage(
    catalog: DuckDBCatalog,
    *,
    mode: str = "offline",
    as_of: datetime | None = None,
    accounts: list[str] | None = None,
) -> list[SourceCoverage]:
    """Assess every registered source against what actually landed.

    ``accounts`` restricts the per-account breakdown; by default every account
    the lake knows about is reported for every source it landed.
    """
    reference = as_of or datetime.now()  # noqa: DTZ005 — naive, matches source stamps
    registry = catalog.registry
    known_accounts = catalog.accounts() if accounts is None else accounts
    coverage: list[SourceCoverage] = []

    for source in registry:
        stats = catalog.stats(source.id)
        status, rows, batches, window, freshness = _assess(source, stats, reference)

        per_account: list[AccountCoverage] = []
        if known_accounts and stats is not None and catalog.has_account_column(source.id):
            landed_for = set(catalog.accounts_for(source.id))
            for account in known_accounts:
                account_stats = catalog.stats(source.id, account) if account in landed_for else None
                a_status, a_rows, a_batches, a_window, a_fresh = _assess(
                    source, account_stats, reference
                )
                per_account.append(
                    AccountCoverage(
                        account=account,
                        status=a_status,
                        rows=a_rows,
                        batches=a_batches,
                        window_start=a_window[0] if a_window else None,
                        window_end=a_window[1] if a_window else None,
                        freshness_minutes=round(a_fresh, 1) if a_fresh is not None else None,
                    )
                )

        coverage.append(
            SourceCoverage(
                source_id=source.id,
                snowflake_object=source.snowflake_object,
                domain=source.domain,
                criticality=source.criticality.value,
                scope=source.scope.value,
                status=status,
                rows=rows,
                batches=batches,
                window_start=window[0] if window else None,
                window_end=window[1] if window else None,
                freshness_minutes=round(freshness, 1) if freshness is not None else None,
                documented_latency_minutes=source.documented_latency_minutes,
                latency_verified=source.latency_verified,
                remediation=_remediation_for(source, status, mode),
                enables_metric_count=len(source.enables_metrics),
                accounts=per_account,
            )
        )
    return sorted(coverage, key=lambda c: (c.domain, c.source_id))


def assess_metrics(
    sources: list[SourceCoverage],
    metric_requirements: dict[str, list[str]],
    optional_requirements: dict[str, list[str]] | None = None,
) -> list[MetricCoverage]:
    """Map each metric to enabled / degraded / unavailable with its blocker."""
    optional_requirements = optional_requirements or {}
    usable = {
        s.source_id for s in sources if s.status in (SourceStatus.AVAILABLE, SourceStatus.STALE)
    }
    assessments: list[MetricCoverage] = []

    for metric_id, required in sorted(metric_requirements.items()):
        missing = [source_id for source_id in required if source_id not in usable]
        optional = optional_requirements.get(metric_id, [])
        missing_optional = [source_id for source_id in optional if source_id not in usable]

        if missing:
            objects = ", ".join(sorted(missing))
            assessments.append(
                MetricCoverage(
                    metric_id=metric_id,
                    availability=MetricAvailability.UNAVAILABLE,
                    required_sources=required,
                    missing_sources=missing,
                    explanation=f"Unavailable — requires {objects}",
                )
            )
        elif missing_optional:
            objects = ", ".join(sorted(missing_optional))
            assessments.append(
                MetricCoverage(
                    metric_id=metric_id,
                    availability=MetricAvailability.DEGRADED,
                    required_sources=required,
                    missing_sources=missing_optional,
                    explanation=(f"Available, with reduced breakdown — {objects} not loaded"),
                )
            )
        else:
            assessments.append(
                MetricCoverage(
                    metric_id=metric_id,
                    availability=MetricAvailability.ENABLED,
                    required_sources=required,
                    explanation="Available",
                )
            )
    return assessments


def build_coverage_matrix(
    catalog: DuckDBCatalog,
    metric_requirements: dict[str, list[str]] | None = None,
    *,
    mode: str = "offline",
    as_of: datetime | None = None,
    optional_requirements: dict[str, list[str]] | None = None,
) -> CoverageMatrix:
    reference = as_of or datetime.now()  # noqa: DTZ005
    accounts = catalog.accounts()
    sources = build_source_coverage(catalog, mode=mode, as_of=reference, accounts=accounts)
    metrics = (
        assess_metrics(sources, metric_requirements, optional_requirements)
        if metric_requirements
        else []
    )
    # Freshness arithmetic stays naive because Snowflake's ACCOUNT_USAGE stamps
    # land that way, but the reported `as_of` is labelled UTC — which is what
    # those stamps are — so every timestamp this API returns carries an offset
    # and a client never has to guess which zone a bare one is in.
    return CoverageMatrix(
        as_of=as_utc(reference),
        mode=mode,
        sources=sources,
        metrics=metrics,
        accounts=accounts,
    )


def as_utc(value: datetime) -> datetime:
    """Label a naive account-usage timestamp as the UTC it already is."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def registry_metric_requirements(registry: SourceRegistry) -> dict[str, list[str]]:
    """Invert ``enables_metrics`` globs into metric-pattern → sources.

    Used before the metric layer exists (Phase 2 replaces this with the metric
    YAML's own ``requires_sources``), and afterwards as a cross-check that no
    source claims to enable a metric the metric does not claim back.
    """
    requirements: dict[str, list[str]] = {}
    for source in registry:
        for pattern in source.enables_metrics:
            requirements.setdefault(pattern, []).append(source.id)
    return {pattern: sorted(sources) for pattern, sources in requirements.items()}
