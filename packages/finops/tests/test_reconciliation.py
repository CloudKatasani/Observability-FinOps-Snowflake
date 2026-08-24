"""The reconciliation gate blocks publication or it is not a gate (R6)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from snowobs_finops.allocation import AllocationEngine, QueryCost, WarehouseDay
from snowobs_finops.reconciliation import GateOutcome, reconcile

DAY = date(2026, 8, 15)


def _allocation(metered: str, attributed: str, warehouse: str = "WH_A", day: date = DAY):  # type: ignore[no-untyped-def]
    engine = AllocationEngine()
    return engine.allocate(
        [WarehouseDay(warehouse, day, Decimal(metered))],
        [
            QueryCost(
                query_id="q1",
                warehouse=warehouse,
                usage_day=day,
                credits=Decimal(attributed),
                query_tag_team="TEAM_A",
            )
        ],
    )


def test_exact_reconciliation_passes_and_allows_publication() -> None:
    allocation = _allocation("100", "80")
    run = reconcile(allocation, {DAY: Decimal("100")})
    assert run.outcome is GateOutcome.PASSED
    assert run.publication_allowed
    assert run.variance_credits == Decimal(0)
    assert "Reconciled" in run.banner()


def test_variance_inside_tolerance_passes() -> None:
    allocation = _allocation("100", "80")
    # 0.4% under: allocated 100, metered 100.4.
    run = reconcile(allocation, {DAY: Decimal("100.4")})
    assert run.outcome is GateOutcome.PASSED
    assert abs(run.variance_pct or Decimal(0)) < Decimal("0.5")


def test_injected_drift_blocks_publication() -> None:
    """§24 Phase 3 exit criterion: the gate blocks on injected drift."""
    allocation = _allocation("100", "80")
    run = reconcile(allocation, {DAY: Decimal("110")})  # 9.1% variance
    assert run.outcome is GateOutcome.FAILED
    assert not run.publication_allowed
    banner = run.banner()
    assert "blocked" in banner
    assert "outside" in banner


def test_failure_banner_decomposes_the_variance_by_day() -> None:
    """The banner names which days drift and by how much (§10.3)."""
    engine = AllocationEngine()
    days = [DAY, DAY + timedelta(days=1), DAY + timedelta(days=2)]
    allocation = engine.allocate(
        [WarehouseDay("WH_A", day, Decimal("100")) for day in days],
        [
            QueryCost(f"q{i}", "WH_A", day, Decimal("100"), query_tag_team="TEAM_A")
            for i, day in enumerate(days)
        ],
    )
    # Only the middle day drifts, and by a lot.
    metered = {days[0]: Decimal("100"), days[1]: Decimal("40"), days[2]: Decimal("100")}
    run = reconcile(allocation, metered)

    assert run.outcome is GateOutcome.FAILED
    assert run.worst_days[0].usage_day == days[1]
    assert run.worst_days[0].variance_credits == Decimal("60")
    assert str(days[1]) in run.banner()


def test_tolerance_is_configurable() -> None:
    allocation = _allocation("100", "80")
    metered = {DAY: Decimal("102")}  # 2% variance
    assert reconcile(allocation, metered).outcome is GateOutcome.FAILED
    assert reconcile(allocation, metered, tolerance_pct=Decimal("5")).outcome is GateOutcome.PASSED


def test_no_metered_data_is_reported_as_no_data_not_as_a_pass() -> None:
    """R3: absence of data is not evidence of correctness."""
    run = reconcile(_allocation("100", "80"), {})
    assert run.outcome is GateOutcome.NO_DATA
    assert not run.publication_allowed
    assert run.variance_pct is None
    assert "could not run" in run.banner()


def test_run_records_its_inputs_for_the_audit_trail() -> None:
    """§10.3: every run is stored with inputs and outcome — pass or fail."""
    allocation = _allocation("100", "80")
    run = reconcile(allocation, {DAY: Decimal("100")})
    assert run.allocated_credits == Decimal("100")
    assert run.metered_credits == Decimal("100")
    assert run.tolerance_pct == Decimal("0.5")
    assert run.period_start == DAY
    assert run.period_end == DAY
    assert run.ran_at is not None


def test_day_variance_ratio_is_null_not_zero_for_a_zero_denominator() -> None:
    allocation = _allocation("50", "50")
    run = reconcile(allocation, {DAY: Decimal("0"), DAY + timedelta(days=1): Decimal("50")})
    zero_day = next(d for d in run.worst_days if d.metered_credits == 0)
    assert zero_day.variance_pct is None


def test_allocation_totals_reconcile_on_realistic_multi_warehouse_data() -> None:
    """The whole point: the engine's own output passes its own gate."""
    engine = AllocationEngine()
    warehouses = ["WH_ELT", "WH_BI", "WH_ADHOC"]
    days = [DAY + timedelta(days=offset) for offset in range(5)]

    warehouse_days = [
        WarehouseDay(warehouse, day, Decimal("37.5")) for warehouse in warehouses for day in days
    ]
    queries = [
        QueryCost(
            query_id=f"{warehouse}-{day}-{team}",
            warehouse=warehouse,
            usage_day=day,
            credits=Decimal("7.5"),
            query_tag_team=team,
        )
        for warehouse in warehouses
        for day in days
        for team in ("TEAM_A", "TEAM_B", "TEAM_C")
    ]
    allocation = engine.allocate(warehouse_days, queries)
    metered = {day: Decimal("37.5") * len(warehouses) for day in days}

    run = reconcile(allocation, metered)
    assert run.outcome is GateOutcome.PASSED
    assert run.variance_credits == Decimal(0)  # exact, not merely within tolerance
