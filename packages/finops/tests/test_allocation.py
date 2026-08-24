"""Allocation engine tests, anchored on the HLD's worked example.

BUILD_PROMPT §10.2: "Implement the HLD's worked example as a unit test fixture
(PRD_SHARED_BI_WH, 40 credits, Marketing 18 / Finance 9 / Ops 3 direct, 10 idle,
$6 cloud services → Marketing $75.60, total $156). If that test fails, the
engine is wrong."
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from snowobs_finops.allocation import (
    UNATTRIBUTED,
    AllocationEngine,
    AllocationMethod,
    AttributionRule,
    QueryCost,
    TeamRegistry,
    WarehouseDay,
    apportion,
    attribute,
)

DAY = date(2026, 8, 15)
WAREHOUSE = "PRD_SHARED_BI_WH"
CREDIT_PRICE = Decimal("3.00")


def _query(team: str, credits: str, index: int = 0) -> QueryCost:
    return QueryCost(
        query_id=f"q-{team}-{index}",
        warehouse=WAREHOUSE,
        usage_day=DAY,
        credits=Decimal(credits),
        query_tag_team=team,
    )


# ═══════════════════════════════════════════ the HLD worked example ══════════
def test_hld_worked_example_to_the_cent() -> None:
    """The acceptance test for the whole chargeback engine.

    40 metered credits on one warehouse-day. Direct usage: Marketing 18,
    Finance 9, Ops 3 (30 attributed). Idle is therefore 10 credits, shared
    pro-rata to direct usage. Account cloud services for the day is $6 worth of
    credits, shared pro-rata to compute. At $3/credit the total is $156 and
    Marketing's share is $75.60.
    """
    engine = AllocationEngine()
    warehouse_day = WarehouseDay(warehouse=WAREHOUSE, usage_day=DAY, metered_credits=Decimal("40"))
    queries = [
        _query("MARKETING", "18"),
        _query("FINANCE", "9"),
        _query("OPS", "3"),
    ]
    # $6 of cloud services at $3/credit is 2 credits.
    cloud_services = {DAY: Decimal("6") / CREDIT_PRICE}

    result = engine.allocate([warehouse_day], queries, cloud_services_credits=cloud_services)

    by_team = {a.team: a for a in result.allocations}

    # Direct is untouched by apportionment.
    assert by_team["MARKETING"].direct_credits == Decimal("18")
    assert by_team["FINANCE"].direct_credits == Decimal("9")
    assert by_team["OPS"].direct_credits == Decimal("3")

    # Idle: 10 credits pro-rata to 18/9/3 → 6/3/1.
    assert by_team["MARKETING"].idle_credits == Decimal("6.000000000")
    assert by_team["FINANCE"].idle_credits == Decimal("3.000000000")
    assert by_team["OPS"].idle_credits == Decimal("1.000000000")

    # Cloud services: 2 credits pro-rata to compute (24/12/4 of 40) → 1.2/0.6/0.2.
    assert by_team["MARKETING"].cloud_services_credits == Decimal("1.200000000")
    assert by_team["FINANCE"].cloud_services_credits == Decimal("0.600000000")
    assert by_team["OPS"].cloud_services_credits == Decimal("0.200000000")

    # And the headline figures from the HLD, to the cent.
    usd = result.by_team_usd(CREDIT_PRICE)
    assert usd["MARKETING"] == Decimal("75.60")
    assert usd["FINANCE"] == Decimal("37.80")
    assert usd["OPS"] == Decimal("12.60")

    # The compute + cloud-services total these figures imply. The HLD summary
    # quotes $156, which is $30 (10 credits) more than the stated inputs can
    # produce: 40 metered credits at $3 is $120, plus $6 of cloud services is
    # $126, and the three per-team figures sum to exactly that. The per-team
    # numbers are the ones that validate the allocation maths, and the engine
    # matches every one of them; the $30 balance is presumed to be the storage,
    # serverless, and AI components the summary does not itemise. Recorded in
    # docs/ASSUMPTIONS.md (A-17) for the HLD owner to confirm.
    assert sum(usd.values()) == Decimal("126.00")


def test_worked_example_components_sum_to_the_metered_total() -> None:
    """Nothing may be lost or invented between metering and allocation."""
    engine = AllocationEngine()
    result = engine.allocate(
        [WarehouseDay(WAREHOUSE, DAY, Decimal("40"))],
        [_query("MARKETING", "18"), _query("FINANCE", "9"), _query("OPS", "3")],
    )
    compute = sum((a.direct_credits + a.idle_credits for a in result.allocations), Decimal(0))
    assert compute == Decimal("40")


# ══════════════════════════════════════════════ the waterfall ════════════════
def test_query_tag_wins_over_every_other_signal() -> None:
    registry = TeamRegistry(
        warehouse_owner_team={WAREHOUSE: "PLATFORM"},
        role_to_team={"ROLE_X": "ROLE_TEAM"},
        user_to_team={"alice": "USER_TEAM"},
    )
    query = QueryCost(
        query_id="q1",
        warehouse=WAREHOUSE,
        usage_day=DAY,
        credits=Decimal("1"),
        query_tag_team="TAGGED_TEAM",
        role="ROLE_X",
        user="alice",
    )
    attribution = attribute(query, registry)
    assert attribution.team == "TAGGED_TEAM"
    assert attribution.method is AllocationMethod.QUERY_TAG


def test_waterfall_falls_through_in_order() -> None:
    registry = TeamRegistry(
        warehouse_owner_team={WAREHOUSE: "OWNER_TEAM"},
        role_to_team={"ROLE_X": "ROLE_TEAM"},
        user_to_team={"alice": "USER_TEAM"},
    )
    base = {
        "query_id": "q",
        "warehouse": WAREHOUSE,
        "usage_day": DAY,
        "credits": Decimal("1"),
    }

    # No tag → warehouse owner tag.
    assert (
        attribute(QueryCost(**base, role="ROLE_X", user="alice"), registry).method
        is AllocationMethod.OBJECT_TAG
    )

    # No tag, no owner → role.
    no_owner = TeamRegistry(role_to_team=registry.role_to_team, user_to_team=registry.user_to_team)
    assert attribute(QueryCost(**base, role="ROLE_X", user="alice"), no_owner).team == "ROLE_TEAM"

    # No tag, no owner, unknown role → user.
    assert attribute(QueryCost(**base, user="alice"), no_owner).team == "USER_TEAM"

    # Nothing at all → UNATTRIBUTED, never a guess.
    assert attribute(QueryCost(**base), TeamRegistry()).team == UNATTRIBUTED


def test_waterfall_order_is_configurable() -> None:
    """§10.1: rules are reorderable, and reordering genuinely changes the answer."""
    registry = TeamRegistry(warehouse_owner_team={WAREHOUSE: "OWNER_TEAM"})
    query = QueryCost(
        query_id="q",
        warehouse=WAREHOUSE,
        usage_day=DAY,
        credits=Decimal("1"),
        query_tag_team="TAGGED_TEAM",
    )
    owner_first = (
        AttributionRule(id="warehouse_owner_tag", method=AllocationMethod.OBJECT_TAG),
        AttributionRule(id="query_tag_team", method=AllocationMethod.QUERY_TAG),
        AttributionRule(id="unattributed", method=AllocationMethod.FALLBACK),
    )
    assert attribute(query, registry, owner_first).team == "OWNER_TEAM"


def test_disabled_rule_is_skipped() -> None:
    registry = TeamRegistry(warehouse_owner_team={WAREHOUSE: "OWNER_TEAM"})
    rules = (
        AttributionRule(id="query_tag_team", method=AllocationMethod.QUERY_TAG, enabled=False),
        AttributionRule(id="warehouse_owner_tag", method=AllocationMethod.OBJECT_TAG),
        AttributionRule(id="unattributed", method=AllocationMethod.FALLBACK),
    )
    query = QueryCost(
        query_id="q",
        warehouse=WAREHOUSE,
        usage_day=DAY,
        credits=Decimal("1"),
        query_tag_team="TAGGED_TEAM",
    )
    assert attribute(query, registry, rules).team == "OWNER_TEAM"


# ══════════════════════════════════════════════ idle apportionment ═══════════
def test_a_team_that_did_not_use_the_warehouse_pays_none_of_its_idle() -> None:
    """The HLD's rule, stated plainly (§10.2)."""
    engine = AllocationEngine()
    result = engine.allocate(
        [WarehouseDay(WAREHOUSE, DAY, Decimal("100"))],
        [_query("MARKETING", "40"), _query("FINANCE", "10")],
    )
    teams = {a.team: a for a in result.allocations}
    assert "OPS" not in teams  # never used it, never charged
    assert teams["MARKETING"].idle_credits == Decimal("40.000000000")
    assert teams["FINANCE"].idle_credits == Decimal("10.000000000")


def test_warehouse_with_no_queries_reports_idle_as_unattributed() -> None:
    """A zombie warehouse's cost is surfaced, not spread over innocent teams."""
    engine = AllocationEngine()
    result = engine.allocate([WarehouseDay("WH_ZOMBIE", DAY, Decimal("50"))], [])
    assert len(result.allocations) == 1
    allocation = result.allocations[0]
    assert allocation.team == UNATTRIBUTED
    assert allocation.direct_credits == Decimal(0)
    assert allocation.idle_credits == Decimal("50")


def test_attribution_exceeding_metering_never_produces_negative_idle() -> None:
    engine = AllocationEngine()
    result = engine.allocate(
        [WarehouseDay(WAREHOUSE, DAY, Decimal("10"))], [_query("MARKETING", "12")]
    )
    assert result.allocations[0].idle_credits == Decimal(0)


# ═══════════════════════════════════════════════ apportionment ═══════════════
def test_apportionment_loses_nothing_to_rounding() -> None:
    """Three-way splits of an indivisible amount must still sum to the total."""
    total = Decimal("10")
    weights = {"a": Decimal("1"), "b": Decimal("1"), "c": Decimal("1")}
    shares = apportion(total, weights)
    assert sum(shares.values()) == total
    assert len(shares) == 3


@pytest.mark.parametrize(
    "total",
    ["0.000000001", "1", "7", "12345.678901234", "0.1"],
)
def test_apportionment_is_exact_for_awkward_totals(total: str) -> None:
    weights = {"a": Decimal("3"), "b": Decimal("5"), "c": Decimal("7"), "d": Decimal("11")}
    shares = apportion(Decimal(total), weights)
    assert sum(shares.values()) == Decimal(total)


def test_apportionment_is_deterministic() -> None:
    weights = {"a": Decimal("1"), "b": Decimal("1"), "c": Decimal("1")}
    first = apportion(Decimal("10"), weights)
    second = apportion(Decimal("10"), weights)
    assert first == second


def test_apportionment_ignores_zero_and_negative_weights() -> None:
    shares = apportion(Decimal("9"), {"a": Decimal("2"), "b": Decimal("0"), "c": Decimal("-1")})
    assert shares == {"a": Decimal("9")}


def test_apportioning_zero_yields_nothing() -> None:
    assert apportion(Decimal(0), {"a": Decimal("1")}) == {}


# ═══════════════════════════════════════════════ reporting ═══════════════════
def test_unattributed_share_is_reported() -> None:
    engine = AllocationEngine()
    result = engine.allocate(
        [WarehouseDay(WAREHOUSE, DAY, Decimal("100")), WarehouseDay("WH_B", DAY, Decimal("100"))],
        [_query("MARKETING", "80"), QueryCost("q2", "WH_B", DAY, Decimal("80"))],
    )
    # WH_B's queries carry no tag at all, so its whole cost is unattributed.
    assert result.unattributed_share() == Decimal("0.5")


def test_money_is_decimal_and_rounds_once_at_the_end() -> None:
    engine = AllocationEngine()
    result = engine.allocate(
        [WarehouseDay(WAREHOUSE, DAY, Decimal("1"))],
        [_query("MARKETING", "0.333333333")],
    )
    usd = result.by_team_usd(Decimal("3.00"))
    for value in usd.values():
        assert isinstance(value, Decimal)
        assert value.as_tuple().exponent == -2  # exactly two decimal places
