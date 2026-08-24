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
from snowobs_semantics.compiler import MetricRequest, SemanticCompiler, TimeRange
from snowobs_semantics.dialect_shims import Dialect
from snowobs_semantics.model import TimeGrain

if TYPE_CHECKING:  # pragma: no cover - typing only
    from snowobs_api.routers.chargeback import AllocationResponse, ReconciliationResponse

#: Sources the chargeback engine cannot work without.
REQUIRED_SOURCES = ("warehouse_metering_history", "query_attribution_history")
CENTS = Decimal("0.01")


class ChargebackService:
    def __init__(self, settings: Settings, tenant: str = "default") -> None:
        self.settings = settings
        self.tenant = tenant
        self.compiler = SemanticCompiler()

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

        engine = DuckDBEngine(catalog)
        result = engine.execute(self.compiler.compile(request, Dialect.DUCKDB))
        return result.dicts()

    # --------------------------------------------------------------- results
    def allocate(self, start: date, end: date) -> tuple[AllocationResult, ReconciliationRun]:
        catalog = self._catalog()
        try:
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
            return allocation, run
        finally:
            catalog.close()

    def _registry(self) -> TeamRegistry:
        """Role/user/warehouse mappings.

        These are tenant configuration edited in the admin UI and persisted with
        the tenant's metadata; until that store exists the waterfall runs on the
        query tag alone, which is the rule that resolves most cost in practice.
        """
        return TeamRegistry()

    def allocation_response(self, start: date, end: date) -> AllocationResponse:
        from snowobs_api.routers.chargeback import AllocationResponse, TeamCost

        allocation, run = self.allocate(start, end)
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
            latency_floor_minutes=480,  # QUERY_ATTRIBUTION_HISTORY is the floor
            sources=[*REQUIRED_SOURCES, "metering_daily_history"],
        )

    def reconciliation_response(self, start: date, end: date) -> ReconciliationResponse:
        _, run = self.allocate(start, end)
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
