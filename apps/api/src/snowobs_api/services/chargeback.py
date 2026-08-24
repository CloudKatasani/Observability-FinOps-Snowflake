"""Chargeback orchestration: gather, allocate, reconcile, gate.

This service is where the allocation engine meets real data. It reads the
warehouse-day metering, the per-query attribution, and the account's daily
billed cloud services through the governed metric layer, runs the waterfall,
then puts the result behind the reconciliation gate before anything is
published (R6).

It does not know which engine answers those queries. The same three metric
requests are compiled for Snowflake or for DuckDB by `services/engines.py`, so
chargeback is one implementation rather than two (R1).
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from snowobs_api.services.engines import EngineChoice, open_engine
from snowobs_api.services.metrics import MetricService
from snowobs_common.config import Settings
from snowobs_common.errors import DataUnavailableError
from snowobs_engines.base import QueryResult
from snowobs_finops.allocation import (
    UNATTRIBUTED,
    AllocationEngine,
    AllocationResult,
    QueryCost,
    TeamRegistry,
    WarehouseDay,
)
from snowobs_finops.reconciliation import ReconciliationRun, reconcile
from snowobs_semantics.compiler import (
    CompiledQuery,
    MetricRequest,
    SemanticCompiler,
    TimeRange,
)
from snowobs_semantics.model import TimeGrain
from snowobs_semantics.scope import Scope, ScopeRequest

if TYPE_CHECKING:  # pragma: no cover - typing only
    from snowobs_api.routers.chargeback import (
        AllocationResponse,
        ReconciliationResponse,
        SqlDisclosure,
    )

#: Sources the chargeback engine cannot work without.
REQUIRED_SOURCES = ("warehouse_metering_history", "query_attribution_history")

#: How far back LIVE mode allocates when a caller names no dates. A month of
#: complete days: long enough to be a useful chargeback period, short enough
#: that an unqualified request never scans a year of ACCOUNT_USAGE.
LIVE_DEFAULT_WINDOW_DAYS = 30
CENTS = Decimal("0.01")

#: What each compiled query contributes to the allocation, so "show the SQL"
#: reads as an explanation rather than as a stack of anonymous statements.
#: Keyed by (metric, is-it-sliced), because the same metric is run twice for
#: two different jobs: per warehouse-day to drive the waterfall, and as an
#: account total for the gate to reconcile against. Labelling both the same
#: would make the response look like it repeated itself.
_SQL_PURPOSES = {
    ("cost.by_warehouse_credits", True): (
        "Metered credits per warehouse-day — the pool each warehouse's cost is allocated from."
    ),
    ("cost.by_warehouse_credits", False): (
        "Metered credits per day for the whole account — the billed figure the "
        "reconciliation gate checks the allocation against (R6)."
    ),
    ("cost.by_team_credits", True): (
        "Attributed credits per team — the direct component of the waterfall."
    ),
    ("cost.cloud_services_credits", False): (
        "Daily billed cloud services — apportioned across teams by their compute share."
    ),
}


class ChargebackService:
    def __init__(self, settings: Settings, tenant: str = "default") -> None:
        self.settings = settings
        self.tenant = tenant
        self.compiler = SemanticCompiler()
        #: Scope questions — which accounts landed, which the organization
        #: contains — are answered by the metric service rather than
        #: re-implemented here, so chargeback and the KPI tiles cannot disagree
        #: about what an account is or when a roll-up is partial.
        self._metrics = MetricService(settings, tenant=tenant)
        #: Every query this run compiled, so the response can show its own SQL
        #: (R5) and derive provenance from what actually ran rather than from a
        #: constant written next to the response model.
        self._compiled: list[CompiledQuery] = []

    def _engine(self) -> AbstractContextManager[EngineChoice]:
        """LIVE or OFFLINE, chosen in one place for the whole application (R10)."""
        return open_engine(self.settings, tenant=self.tenant)

    # ------------------------------------------------------------- gathering
    def _warehouse_days(
        self, chosen: EngineChoice, start: date, end: date, account: str | None
    ) -> list[WarehouseDay]:
        request = MetricRequest(
            metrics=["cost.by_warehouse_credits"],
            dimensions=["warehouse"],
            account=account,
            grain=TimeGrain.DAY,
            time_range=TimeRange(start=start, end=end),
            limit=50_000,
        )
        rows = self._execute(chosen, request)
        return [
            WarehouseDay(
                warehouse=str(row["WAREHOUSE"]),
                usage_day=_as_date(row["TIME_BUCKET"]),
                metered_credits=Decimal(str(row["COST_BY_WAREHOUSE_CREDITS"] or 0)),
            )
            for row in rows
            if row.get("WAREHOUSE") and row.get("TIME_BUCKET")
        ]

    def _query_costs(
        self, chosen: EngineChoice, start: date, end: date, account: str | None
    ) -> list[QueryCost]:
        # The team dimension already applies the query-tag rule of the waterfall;
        # rows carrying UNATTRIBUTED fall through to the remaining rules below.
        request = MetricRequest(
            metrics=["cost.by_team_credits"],
            dimensions=["team", "warehouse", "user", "role"],
            account=account,
            grain=TimeGrain.DAY,
            time_range=TimeRange(start=start, end=end),
            limit=50_000,
        )
        rows = self._execute(chosen, request)
        costs: list[QueryCost] = []
        for index, row in enumerate(rows):
            team = str(row.get("TEAM") or "")
            costs.append(
                QueryCost(
                    query_id=f"agg-{index}",
                    warehouse=str(row.get("WAREHOUSE") or ""),
                    usage_day=_as_date(row["TIME_BUCKET"]),
                    credits=Decimal(str(row.get("COST_BY_TEAM_CREDITS") or 0)),
                    query_tag_team=None if team in ("", UNATTRIBUTED) else team,
                    user=str(row.get("USER") or "") or None,
                    role=str(row.get("ROLE") or "") or None,
                )
            )
        return costs

    def _cloud_services(
        self, chosen: EngineChoice, start: date, end: date, account: str | None
    ) -> dict[date, Decimal]:
        request = MetricRequest(
            metrics=["cost.cloud_services_credits"],
            account=account,
            grain=TimeGrain.DAY,
            time_range=TimeRange(start=start, end=end),
            limit=10_000,
        )
        rows = self._execute(chosen, request)
        return {
            _as_date(row["TIME_BUCKET"]): Decimal(str(row["COST_CLOUD_SERVICES_CREDITS"] or 0))
            for row in rows
            if row.get("TIME_BUCKET")
        }

    def _metered_by_day(
        self, chosen: EngineChoice, start: date, end: date, account: str | None
    ) -> dict[date, Decimal]:
        """The billed figure the gate reconciles against (R6).

        Scoped exactly like the allocation it checks: reconciling one account's
        allocated credits against the whole organization's bill would report a
        gaping variance and block publication for a figure that is correct.
        """
        request = MetricRequest(
            metrics=["cost.by_warehouse_credits"],
            account=account,
            grain=TimeGrain.DAY,
            time_range=TimeRange(start=start, end=end),
            limit=10_000,
        )
        rows = self._execute(chosen, request)
        return {
            _as_date(row["TIME_BUCKET"]): Decimal(str(row["COST_BY_WAREHOUSE_CREDITS"] or 0))
            for row in rows
            if row.get("TIME_BUCKET")
        }

    def _execute(self, chosen: EngineChoice, request: MetricRequest) -> list[dict[str, Any]]:
        compiled = self.compiler.compile(request, chosen.dialect)
        self._compiled.append(compiled)
        result: QueryResult = chosen.engine.execute(compiled)
        return result.dicts()

    # ------------------------------------------------------------ provenance
    def _provenance(self) -> tuple[bool, int, list[str], list[SqlDisclosure]]:
        """Provenance for the whole allocation, taken from the queries that ran.

        An allocation is a composite of three metric queries, so each field is
        the *least favourable* of its parts: provisional if any input is still
        restating, and floored at the slowest source. Reporting anything better
        would overstate how settled the chargeback is.
        """
        from snowobs_api.routers.chargeback import SqlDisclosure

        provisional = any(query.provisional for query in self._compiled)
        latency_floor = max((query.latency_floor_minutes for query in self._compiled), default=0)
        sources = sorted({source for query in self._compiled for source in query.gating_sources})
        disclosures = [
            SqlDisclosure(
                purpose=_SQL_PURPOSES.get(
                    (query.metrics[0] if query.metrics else "", bool(query.dimensions)),
                    "Supporting query for the allocation.",
                ),
                metrics=list(query.metrics),
                dimensions=list(query.dimensions),
                sql=query.sql,
            )
            for query in self._compiled
        ]
        return provisional, latency_floor, sources, disclosures

    # --------------------------------------------------------------- results
    def _default_window(
        self, chosen: EngineChoice, account: str | None = None
    ) -> tuple[date, date] | None:
        """The period to allocate when the caller names no dates.

        The two modes answer this differently because the question means
        different things in each. OFFLINE has a *landed* window — the extracts
        cover what they cover, and allocating outside it would reconcile a full
        allocation against a partly empty bill. LIVE has no such boundary: the
        account holds a year of ACCOUNT_USAGE, so the sensible default is a
        recent period rather than a scan of all of it.
        """
        if chosen.mode == "live":
            end = date.today() - timedelta(days=1)  # noqa: DTZ011 — account-date granularity
            return end - timedelta(days=LIVE_DEFAULT_WINDOW_DAYS - 1), end

        catalog = getattr(chosen.engine, "catalog", None)
        if catalog is None:
            return None
        starts, ends = [], []
        for source_id in REQUIRED_SOURCES:
            # One account's extracts can cover a different window from its
            # siblings', so an account-scoped allocation defaults to *that*
            # account's landed window rather than the fleet's.
            stats = catalog.stats(source_id, account) if account else catalog.stats(source_id)
            if stats is not None and stats.window is not None:
                starts.append(stats.window[0])
                ends.append(stats.window[1])
        if not starts:
            return None
        # The overlap, not the union: a day only one input covers cannot be
        # allocated and reconciled.
        return max(starts), min(ends)

    def allocate(
        self,
        start: date | None,
        end: date | None,
        scope: ScopeRequest | None = None,
    ) -> tuple[AllocationResult, ReconciliationRun, date, date]:
        scope = scope or ScopeRequest()
        account = self._resolve_account(scope)
        self._compiled.clear()
        with self._engine() as chosen:
            if start is None or end is None:
                landed = self._default_window(chosen, account)
                if landed is None:
                    # R3: no inputs is not a zero-cost account, and the caller
                    # gets told which sources are missing rather than an
                    # allocation of nothing.
                    whose = f" for {account}" if account else ""
                    raise DataUnavailableError(
                        "Chargeback needs "
                        f"{' and '.join(REQUIRED_SOURCES)} to be landed{whose}; neither is. "
                        "Upload an extract, or check the coverage page for the "
                        "remediation for each."
                    )
                start = start or landed[0]
                end = end or landed[1]

            engine = AllocationEngine(registry=self._registry())
            allocation = engine.allocate(
                self._warehouse_days(chosen, start, end, account),
                self._query_costs(chosen, start, end, account),
                cloud_services_credits=self._cloud_services(chosen, start, end, account),
            )
            run = reconcile(
                allocation,
                self._metered_by_day(chosen, start, end, account),
                tolerance_pct=self.settings.finops.reconcile_tolerance_pct,
                period_start=start,
                period_end=end,
            )
            return allocation, run, start, end

    # ----------------------------------------------------------------- scope
    def _resolve_account(self, scope: ScopeRequest) -> str | None:
        """The account filter for this request, or None for the organization.

        An account scope is checked against what has actually landed before any
        query runs. Allocating an account the lake has never seen would produce
        an empty waterfall that reconciles perfectly against an empty bill — a
        green gate over a chargeback of nothing, which is exactly the "zero for
        unknown" R3 forbids.
        """
        if scope.scope is not Scope.ACCOUNT or not scope.account:
            return None
        known = self._metrics.landed_accounts()
        if known and scope.account not in known:
            raise DataUnavailableError(
                f"No chargeback inputs have landed for {scope.account}. "
                f"Accounts with data: {', '.join(known)}."
            )
        return scope.account

    def _scope_fields(self, scope: ScopeRequest) -> dict[str, Any]:
        """Where these figures were computed, in the metric endpoints' shape.

        Account scope reports no missing accounts: one account's allocation is
        complete when that account's data has landed, whatever its siblings have
        done. Organization scope is partial exactly when billing names an
        account whose own extracts never arrived — the same test the KPI tiles
        apply, asked through the same service.
        """
        if scope.scope is Scope.ACCOUNT and scope.account:
            return {
                "scope": scope.scope.value,
                "scope_account": scope.account,
                "scope_partial": False,
                "contributing_accounts": [scope.account],
                "missing_accounts": [],
            }
        landed = self._metrics.landed_accounts()
        missing = sorted(set(self._metrics.organization_roster()) - set(landed))
        return {
            "scope": Scope.ORGANIZATION.value,
            "scope_account": None,
            "scope_partial": bool(missing),
            "contributing_accounts": landed,
            "missing_accounts": missing,
        }

    def _registry(self) -> TeamRegistry:
        """Role/user/warehouse mappings.

        These are tenant configuration edited in the admin UI and persisted with
        the tenant's metadata; until that store exists the waterfall runs on the
        query tag alone, which is the rule that resolves most cost in practice.
        """
        return TeamRegistry()

    def allocation_response(
        self,
        start: date | None = None,
        end: date | None = None,
        scope: ScopeRequest | None = None,
    ) -> AllocationResponse:
        from snowobs_api.routers.chargeback import AllocationResponse, TeamCost

        scope = scope or ScopeRequest()
        allocation, run, start, end = self.allocate(start, end, scope)
        provisional, latency_floor, sources, disclosures = self._provenance()
        price = self.settings.finops.credit_price_usd
        totals = allocation.by_team()
        grand_total = sum(totals.values(), Decimal(0))

        components: dict[str, dict[str, Decimal]] = {}
        for entry in allocation.allocations:
            bucket = components.setdefault(
                entry.team,
                {"direct": Decimal(0), "idle": Decimal(0), "cloud": Decimal(0)},
            )
            bucket["direct"] += entry.direct_credits
            bucket["idle"] += entry.idle_credits
            bucket["cloud"] += entry.cloud_services_credits

        teams = [
            TeamCost(
                team=team,
                direct_credits=str(parts["direct"]),
                idle_credits=str(parts["idle"]),
                cloud_services_credits=str(parts["cloud"]),
                total_credits=str(totals[team]),
                cost_usd=str((totals[team] * price).quantize(CENTS)) if price else None,
                share_of_total=(
                    str((totals[team] / grand_total).quantize(Decimal("0.0001")))
                    if grand_total
                    else "0"
                ),
            )
            for team, parts in sorted(components.items(), key=lambda kv: -totals[kv[0]])
        ]

        return AllocationResponse(
            period_start=start,
            period_end=end,
            mode=self.settings.finops.mode,
            # R6: when the gate is red the figures are withheld, not shown with
            # a warning next to them.
            teams=teams if run.publication_allowed else [],
            unattributed_share=str(allocation.unattributed_share().quantize(Decimal("0.0001"))),
            credit_price_usd=str(price) if price else None,
            reconciliation=self._to_response(run),
            figures_published=run.publication_allowed,
            as_of=datetime.now(tz=UTC),
            provisional=provisional,
            latency_floor_minutes=latency_floor,
            sources=sources,
            sql=disclosures,
            **self._scope_fields(scope),
        )

    def reconciliation_response(
        self, start: date, end: date, scope: ScopeRequest | None = None
    ) -> ReconciliationResponse:
        _, run, _resolved_start, _resolved_end = self.allocate(start, end, scope)
        return self._to_response(run)

    @staticmethod
    def _to_response(run: ReconciliationRun) -> ReconciliationResponse:
        from snowobs_api.routers.chargeback import ReconciliationResponse

        return ReconciliationResponse(
            outcome=run.outcome.value,
            allocated_credits=str(run.allocated_credits),
            metered_credits=str(run.metered_credits),
            variance_credits=str(run.variance_credits),
            variance_pct=str(run.variance_pct) if run.variance_pct is not None else None,
            tolerance_pct=str(run.tolerance_pct),
            publication_allowed=run.publication_allowed,
            banner=run.banner(),
            ran_at=run.ran_at,
            worst_days=[
                {
                    "usage_day": day.usage_day.isoformat(),
                    "allocated_credits": str(day.allocated_credits),
                    "metered_credits": str(day.metered_credits),
                    "variance_credits": str(day.variance_credits),
                    "variance_pct": (
                        str(day.variance_pct) if day.variance_pct is not None else None
                    ),
                }
                for day in run.worst_days[:10]
            ],
        )


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
