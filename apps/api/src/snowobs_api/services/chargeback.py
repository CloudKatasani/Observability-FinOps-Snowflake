"""Chargeback orchestration: read the catalog, allocate, reconcile, gate.

This service is where the allocation engine meets real landed data. It reads
the warehouse-day metering, the per-query attribution, and the account's daily
billed cloud services, runs the waterfall, then puts the result behind the
reconciliation gate before anything is published (R6).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from snowobs_common.config import Settings
from snowobs_common.errors import DataUnavailableError
from snowobs_finops.allocation import (
    UNATTRIBUTED,
    AllocationEngine,
    AllocationResult,
    QueryCost,
    TeamRegistry,
    WarehouseDay,
)
from snowobs_finops.reconciliation import ReconciliationRun, reconcile
from snowobs_ingest.catalog import DuckDBCatalog
from snowobs_semantics.compiler import (
    CompiledQuery,
    MetricRequest,
    SemanticCompiler,
    TimeRange,
)
from snowobs_semantics.dialect_shims import Dialect
from snowobs_semantics.model import TimeGrain

if TYPE_CHECKING:  # pragma: no cover - typing only
    from snowobs_api.routers.chargeback import (
        AllocationResponse,
        ReconciliationResponse,
        SqlDisclosure,
    )

#: Sources the chargeback engine cannot work without.
REQUIRED_SOURCES = ("warehouse_metering_history", "query_attribution_history")
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
        #: Every query this run compiled, so the response can show its own SQL
        #: (R5) and derive provenance from what actually ran rather than from a
        #: constant written next to the response model.
        self._compiled: list[CompiledQuery] = []

    def _catalog(self) -> DuckDBCatalog:
        from snowobs_api.services.datasets import storage_root

        catalog = DuckDBCatalog(storage_root(self.settings), tenant=self.tenant)
        catalog.register_all()
        return catalog

    # ------------------------------------------------------------- gathering
    def _warehouse_days(self, catalog: DuckDBCatalog, start: date, end: date) -> list[WarehouseDay]:
        request = MetricRequest(
            metrics=["cost.by_warehouse_credits"],
            dimensions=["warehouse"],
            grain=TimeGrain.DAY,
            time_range=TimeRange(start=start, end=end),
            limit=50_000,
        )
        rows = self._execute(catalog, request)
        return [
            WarehouseDay(
                warehouse=str(row["WAREHOUSE"]),
                usage_day=_as_date(row["TIME_BUCKET"]),
                metered_credits=Decimal(str(row["COST_BY_WAREHOUSE_CREDITS"] or 0)),
            )
            for row in rows
            if row.get("WAREHOUSE") and row.get("TIME_BUCKET")
        ]

    def _query_costs(self, catalog: DuckDBCatalog, start: date, end: date) -> list[QueryCost]:
        # The team dimension already applies the query-tag rule of the waterfall;
        # rows carrying UNATTRIBUTED fall through to the remaining rules below.
        request = MetricRequest(
            metrics=["cost.by_team_credits"],
            dimensions=["team", "warehouse", "user", "role"],
            grain=TimeGrain.DAY,
            time_range=TimeRange(start=start, end=end),
            limit=50_000,
        )
        rows = self._execute(catalog, request)
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
        self, catalog: DuckDBCatalog, start: date, end: date
    ) -> dict[date, Decimal]:
        request = MetricRequest(
            metrics=["cost.cloud_services_credits"],
            grain=TimeGrain.DAY,
            time_range=TimeRange(start=start, end=end),
            limit=10_000,
        )
        rows = self._execute(catalog, request)
        return {
            _as_date(row["TIME_BUCKET"]): Decimal(str(row["COST_CLOUD_SERVICES_CREDITS"] or 0))
            for row in rows
            if row.get("TIME_BUCKET")
        }

    def _metered_by_day(
        self, catalog: DuckDBCatalog, start: date, end: date
    ) -> dict[date, Decimal]:
        """The billed figure the gate reconciles against (R6)."""
        request = MetricRequest(
            metrics=["cost.by_warehouse_credits"],
            grain=TimeGrain.DAY,
            time_range=TimeRange(start=start, end=end),
            limit=10_000,
        )
        rows = self._execute(catalog, request)
        return {
            _as_date(row["TIME_BUCKET"]): Decimal(str(row["COST_BY_WAREHOUSE_CREDITS"] or 0))
            for row in rows
            if row.get("TIME_BUCKET")
        }

    def _execute(self, catalog: DuckDBCatalog, request: MetricRequest) -> list[dict[str, Any]]:
        from snowobs_engines.duckdb_engine import DuckDBEngine

        compiled = self.compiler.compile(request, Dialect.DUCKDB)
        self._compiled.append(compiled)
        engine = DuckDBEngine(catalog)
        result = engine.execute(compiled)
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
    def _landed_window(self, catalog: DuckDBCatalog) -> tuple[date, date] | None:
        """The period the allocation inputs actually cover.

        Used when a caller names no dates. Taken from the sources the waterfall
        reads rather than from every landed source: a snapshot of `users` that
        stretches further back than the metering history would widen the window
        to a period there is no cost data for, and the reconciliation gate would
        then compare a full allocation against a partly empty bill.
        """
        starts, ends = [], []
        for source_id in REQUIRED_SOURCES:
            stats = catalog.stats(source_id)
            if stats is not None and stats.window is not None:
                starts.append(stats.window[0])
                ends.append(stats.window[1])
        if not starts:
            return None
        # The overlap, not the union: a day only one input covers cannot be
        # allocated and reconciled.
        return max(starts), min(ends)

    def allocate(
        self, start: date | None, end: date | None
    ) -> tuple[AllocationResult, ReconciliationRun, date, date]:
        catalog = self._catalog()
        self._compiled.clear()
        try:
            if start is None or end is None:
                landed = self._landed_window(catalog)
                if landed is None:
                    # R3: no inputs is not a zero-cost account, and the caller
                    # gets told which sources are missing rather than an
                    # allocation of nothing.
                    raise DataUnavailableError(
                        "Chargeback needs "
                        f"{' and '.join(REQUIRED_SOURCES)} to be landed; neither is. "
                        "Upload an extract, or check the coverage page for the "
                        "remediation for each."
                    )
                start = start or landed[0]
                end = end or landed[1]

            engine = AllocationEngine(registry=self._registry())
            allocation = engine.allocate(
                self._warehouse_days(catalog, start, end),
                self._query_costs(catalog, start, end),
                cloud_services_credits=self._cloud_services(catalog, start, end),
            )
            run = reconcile(
                allocation,
                self._metered_by_day(catalog, start, end),
                tolerance_pct=self.settings.finops.reconcile_tolerance_pct,
                period_start=start,
                period_end=end,
            )
            return allocation, run, start, end
        finally:
            catalog.close()

    def _registry(self) -> TeamRegistry:
        """Role/user/warehouse mappings.

        These are tenant configuration edited in the admin UI and persisted with
        the tenant's metadata; until that store exists the waterfall runs on the
        query tag alone, which is the rule that resolves most cost in practice.
        """
        return TeamRegistry()

    def allocation_response(
        self, start: date | None = None, end: date | None = None
    ) -> AllocationResponse:
        from snowobs_api.routers.chargeback import AllocationResponse, TeamCost

        allocation, run, start, end = self.allocate(start, end)
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
        )

    def reconciliation_response(self, start: date, end: date) -> ReconciliationResponse:
        _, run, _resolved_start, _resolved_end = self.allocate(start, end)
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
