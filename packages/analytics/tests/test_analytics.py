"""Forecasting, anomaly detection, levers, savings claims, and alerting.

The detection tests run against the **generated fixture account** and assert
against its ground-truth file, so a phenomenon that stops being detected fails
the build (§24 Phase 5 exit criterion).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from snowobs_analytics.alerting import (
    AlertEngine,
    AlertRule,
    AlertRuleError,
    AlertTier,
    Condition,
    backtest,
    to_snowflake_alert_ddl,
)
from snowobs_analytics.anomaly import Point, decompose, detect, explain_delta
from snowobs_analytics.forecast import (
    Observation,
    commitment_posture,
    evaluate,
    forecast_series,
)
from snowobs_analytics.levers import (
    Confidence,
    FingerprintEvidence,
    LeverId,
    WarehouseEvidence,
    rank,
)
from snowobs_analytics.savings import (
    ClaimStatus,
    Realisation,
    SavingsClaim,
    SavingsLedger,
    verify,
)

DAY0 = date(2026, 5, 1)


def _series(values: list[float], start: date = DAY0) -> list[Observation]:
    return [
        Observation(day=start + timedelta(days=i), value=Decimal(str(v)))
        for i, v in enumerate(values)
    ]


# ═══════════════════════════════════════════════════════════ forecasting ═════
def test_forecast_follows_a_clean_linear_trend() -> None:
    forecast = forecast_series(_series([100 + 2 * i for i in range(40)]), horizon_days=7)
    assert forecast.usable
    assert forecast.components.trend_slope == pytest.approx(2.0, abs=0.05)
    # The next day continues the line.
    assert float(forecast.points[0].value) == pytest.approx(180, abs=6)


def test_forecast_trend_is_robust_to_a_single_spike() -> None:
    """The reason for Theil–Sen: one 4x day must not tilt the whole forecast."""
    clean = [100.0] * 40
    spiked = list(clean)
    spiked[20] = 400.0

    baseline = forecast_series(_series(clean), horizon_days=7)
    with_spike = forecast_series(_series(spiked), horizon_days=7)

    assert abs(with_spike.components.trend_slope - baseline.components.trend_slope) < 0.5
    assert float(with_spike.points[0].value) == pytest.approx(100, abs=25)


def test_forecast_learns_weekday_seasonality() -> None:
    # Weekends at a fifth of weekday volume — the shape every account has.
    values = [20.0 if (DAY0 + timedelta(days=i)).weekday() >= 5 else 100.0 for i in range(56)]
    forecast = forecast_series(_series(values), horizon_days=14)
    assert forecast.usable
    weekend = [p for p in forecast.points if p.day.weekday() >= 5]
    weekday = [p for p in forecast.points if p.day.weekday() < 5]
    assert weekend and weekday
    assert max(float(p.value) for p in weekend) < min(float(p.value) for p in weekday)


def test_forecast_refuses_rather_than_guesses_on_thin_history() -> None:
    """R3: an unknowable forecast says so."""
    forecast = forecast_series(_series([100.0, 110.0, 120.0]), horizon_days=7)
    assert not forecast.usable
    assert forecast.insufficient_data_reason is not None
    assert "at least" in forecast.insufficient_data_reason
    assert "No forecast" in forecast.explain()


def test_forecast_interval_widens_with_the_horizon() -> None:
    import random

    rng = random.Random(7)  # noqa: S311 — deterministic test noise
    forecast = forecast_series(
        _series([100 + rng.uniform(-10, 10) for _ in range(60)]), horizon_days=30
    )
    first = forecast.points[0].upper - forecast.points[0].lower
    last = forecast.points[-1].upper - forecast.points[-1].lower
    assert last > first


def test_forecast_is_never_negative() -> None:
    forecast = forecast_series(_series([100 - 3 * i for i in range(40)]), horizon_days=30)
    assert all(point.value >= 0 for point in forecast.points)
    assert all(point.lower >= 0 for point in forecast.points)


def test_forecast_explains_itself() -> None:
    explanation = forecast_series(_series([100 + i for i in range(40)])).explain()
    assert "Trend rising" in explanation
    assert "fitted on 40 days" in explanation


def test_month_end_landing_combines_actuals_and_forecast() -> None:
    forecast = forecast_series(_series([100.0] * 40, start=date(2026, 5, 1)), horizon_days=30)
    landing = forecast.month_end_landing(
        actuals_to_date=Decimal("4000"), month_end=date(2026, 6, 30)
    )
    assert landing is not None
    assert landing > Decimal("4000")


def test_accuracy_report_computes_mape_and_excludes_zero_actuals() -> None:
    """R3: a percentage against zero is undefined, not infinite."""
    forecast = forecast_series(_series([100.0] * 40), horizon_days=3)
    actual = [
        Observation(day=forecast.points[0].day, value=Decimal("110")),
        Observation(day=forecast.points[1].day, value=Decimal("90")),
        Observation(day=forecast.points[2].day, value=Decimal("0")),
    ]
    report = evaluate(forecast.points, actual)
    assert report.observations == 3
    assert report.mape is not None
    assert 0 < report.mape < 30
    assert report.meets_target is not None


def test_accuracy_report_is_null_when_nothing_can_be_compared() -> None:
    report = evaluate([], [])
    assert report.mape is None
    assert report.meets_target is None


def test_commitment_posture_projects_exhaustion() -> None:
    posture = commitment_posture(Decimal("1000"), _series([50.0] * 10), today=date(2026, 6, 1))
    assert posture.daily_burn == Decimal("50.00")
    assert posture.exhaustion_date == date(2026, 6, 21)
    assert "exhausted" in posture.summary()


def test_commitment_posture_flags_stranding_risk() -> None:
    posture = commitment_posture(
        Decimal("100000"),
        _series([10.0] * 10),
        contract_end=date(2026, 12, 31),
        today=date(2026, 6, 1),
    )
    assert posture.stranding_risk is True
    assert posture.exhaustion_date is None
    assert "outlast the contract" in posture.summary()


# ═══════════════════════════════════════════════════════ anomaly detection ═══
def test_a_clean_series_produces_no_anomalies() -> None:
    """The most important property: it must be quiet when nothing is wrong."""
    points = [
        Point(day=DAY0 + timedelta(days=i), value=Decimal(str(100 + (i % 3)))) for i in range(60)
    ]
    assert detect(points) == []


def test_a_four_times_spike_is_detected() -> None:
    values = [100.0] * 40
    values[25] = 400.0
    points = [
        Point(day=DAY0 + timedelta(days=i), value=Decimal(str(v))) for i, v in enumerate(values)
    ]
    anomalies = detect(points)
    assert len(anomalies) == 1
    assert anomalies[0].day == DAY0 + timedelta(days=25)
    assert anomalies[0].direction.value == "spike"
    assert anomalies[0].relative_change > Decimal("2")


def test_weekday_seasonality_does_not_fire() -> None:
    """Without deseasonalising, every Monday would look anomalous."""
    points = [
        Point(
            day=DAY0 + timedelta(days=i),
            value=Decimal("20") if (DAY0 + timedelta(days=i)).weekday() >= 5 else Decimal("100"),
        )
        for i in range(60)
    ]
    assert detect(points) == []


def test_persistence_requirement_suppresses_a_one_day_blip() -> None:
    values = [100.0] * 40
    values[20] = 400.0
    points = [
        Point(day=DAY0 + timedelta(days=i), value=Decimal(str(v))) for i, v in enumerate(values)
    ]
    assert detect(points, require_persistence=1)
    assert detect(points, require_persistence=2) == []


def test_persistent_deviation_fires_even_with_persistence_required() -> None:
    values = [100.0] * 40
    values[20] = values[21] = values[22] = 400.0
    points = [
        Point(day=DAY0 + timedelta(days=i), value=Decimal(str(v))) for i, v in enumerate(values)
    ]
    assert detect(points, require_persistence=2)


def test_thin_history_produces_no_anomalies_rather_than_false_ones() -> None:
    points = [Point(day=DAY0 + timedelta(days=i), value=Decimal("100")) for i in range(5)]
    assert detect(points) == []


def test_decomposition_names_the_largest_contributor() -> None:
    """An alert that says 'spend is up' wastes an hour; this one says why."""
    values = [100.0] * 40
    values[30] = 400.0
    points = [
        Point(day=DAY0 + timedelta(days=i), value=Decimal(str(v))) for i, v in enumerate(values)
    ]
    anomaly = detect(points)[0]

    on_day = {
        "team": {
            "TEAM_ML": Decimal("310"),
            "TEAM_ANALYTICS": Decimal("60"),
            "TEAM_OPS": Decimal("30"),
        }
    }
    baseline = {
        "team": {
            "TEAM_ML": Decimal("20"),
            "TEAM_ANALYTICS": Decimal("50"),
            "TEAM_OPS": Decimal("30"),
        }
    }
    decomposed = decompose(anomaly, on_day, baseline)
    assert decomposed.contributions
    top = decomposed.contributions[0]
    assert top.member == "TEAM_ML"
    assert top.share_of_delta > Decimal("0.8")
    assert "TEAM_ML" in decomposed.narrative()


def test_decomposition_notices_a_workload_that_stopped() -> None:
    """A drop is caused by something that *was* there and is not any more."""
    anomaly_points = [Point(day=DAY0 + timedelta(days=i), value=Decimal("100")) for i in range(40)]
    anomaly_points[30] = Point(day=DAY0 + timedelta(days=30), value=Decimal("20"))
    anomaly = detect(anomaly_points)[0]

    decomposed = decompose(
        anomaly,
        {"team": {"TEAM_A": Decimal("20")}},
        {"team": {"TEAM_A": Decimal("20"), "TEAM_GONE": Decimal("80")}},
    )
    assert any(c.member == "TEAM_GONE" for c in decomposed.contributions)


def test_explain_delta_is_deterministic_and_ranked() -> None:
    """The agent's explain_delta tool: the tool computes, the agent narrates (R12)."""
    before = {"TEAM_A": Decimal("100"), "TEAM_B": Decimal("50")}
    after = {"TEAM_A": Decimal("300"), "TEAM_B": Decimal("40"), "TEAM_C": Decimal("10")}
    contributions = explain_delta(before, after, dimension="team")

    assert contributions[0].member == "TEAM_A"
    assert contributions[0].delta == Decimal("200")
    # Deterministic: the same inputs give the same answer, every time.
    assert explain_delta(before, after, dimension="team") == contributions


# ═════════════════════════════════════════════════════════════════ levers ════
def _warehouse(**overrides: object) -> WarehouseEvidence:
    base: dict[str, object] = {
        "warehouse": "WH_TEST",
        "size": "Large",
        "auto_suspend_seconds": 60,
        "min_clusters": 1,
        "max_clusters": 1,
        "days_observed": 14,
        "metered_credits": Decimal("1000"),
        "attributed_credits": Decimal("800"),
        "query_count": 5000,
        "queued_overload_ms": 0,
        "elapsed_ms": 1_000_000,
    }
    base.update(overrides)
    return WarehouseEvidence(**base)  # type: ignore[arg-type]


def test_oversized_warehouse_is_recommended_for_a_size_reduction() -> None:
    evidence = _warehouse(
        size="2X-Large", attributed_credits=Decimal("150"), metered_credits=Decimal("1000")
    )
    recommendations = rank([evidence])
    rightsize = next(r for r in recommendations if r.lever is LeverId.RIGHTSIZE)
    assert "2X-Large" in rightsize.title
    assert rightsize.modelled_monthly_credits_saved > 0
    assert "ALTER WAREHOUSE WH_TEST SET WAREHOUSE_SIZE" in rightsize.change_sql
    assert "2X-Large" in rightsize.rollback_sql  # rollback restores the original


def test_a_queueing_warehouse_is_never_recommended_for_downsizing() -> None:
    evidence = _warehouse(
        attributed_credits=Decimal("200"), queued_overload_ms=200_000, elapsed_ms=1_000_000
    )
    assert not any(r.lever is LeverId.RIGHTSIZE for r in rank([evidence]))


def test_remote_spill_vetoes_downsizing() -> None:
    """A spilling warehouse is under-sized however idle it looks."""
    evidence = _warehouse(attributed_credits=Decimal("100"), spill_remote_bytes=10**10)
    assert not any(r.lever is LeverId.RIGHTSIZE for r in rank([evidence]))


def test_smallest_size_is_not_recommended_for_downsizing() -> None:
    evidence = _warehouse(size="X-Small", attributed_credits=Decimal("50"))
    assert not any(r.lever is LeverId.RIGHTSIZE for r in rank([evidence]))


def test_long_autosuspend_is_flagged_against_policy() -> None:
    evidence = _warehouse(auto_suspend_seconds=3600, workload_class="elt", size="Medium")
    recommendation = next(r for r in rank([evidence]) if r.lever is LeverId.AUTOSUSPEND)
    assert "3600s to 60s" in recommendation.title
    assert "SET AUTO_SUSPEND = 60" in recommendation.change_sql
    assert "SET AUTO_SUSPEND = 3600" in recommendation.rollback_sql
    # Never claims more than the idle credits actually observed.
    assert recommendation.modelled_monthly_credits_saved <= Decimal("500")


def test_compliant_autosuspend_produces_no_recommendation() -> None:
    evidence = _warehouse(auto_suspend_seconds=60, workload_class="elt")
    assert not any(r.lever is LeverId.AUTOSUSPEND for r in rank([evidence]))


def test_zombie_warehouse_is_flagged_with_high_confidence() -> None:
    evidence = _warehouse(query_count=0, attributed_credits=Decimal("0"))
    recommendation = next(r for r in rank([evidence]) if r.lever is LeverId.ZOMBIE)
    assert recommendation.confidence is Confidence.HIGH
    assert "SUSPEND" in recommendation.change_sql
    assert "RESUME" in recommendation.rollback_sql
    # And it warns about the monthly-job blind spot rather than being glib.
    assert "monthly close" in recommendation.risk_note


def test_multicluster_minimum_is_flagged_when_nothing_queues() -> None:
    evidence = _warehouse(min_clusters=3, max_clusters=6, queued_overload_ms=0)
    recommendation = next(r for r in rank([evidence]) if r.lever is LeverId.MULTICLUSTER)
    assert "MIN_CLUSTER_COUNT = 1" in recommendation.change_sql
    assert "MIN_CLUSTER_COUNT = 3" in recommendation.rollback_sql


def test_pruning_collapse_is_flagged_as_a_query_lever() -> None:
    evidence = FingerprintEvidence(
        fingerprint="fp-pruning-regression-0001",
        warehouse="WH_ELT_CORE",
        credits=Decimal("500"),
        executions=1400,
        days_observed=14,
        partitions_scanned=9800,
        partitions_total=10_000,
    )
    recommendation = next(r for r in rank([], [evidence]) if r.lever is LeverId.QUERY_OPTIMISATION)
    assert "98%" in " ".join(recommendation.evidence)
    assert recommendation.modelled_monthly_credits_saved > 0
    # A query change is the owner's, not the platform's.
    assert "Owner action" in recommendation.change_sql


def test_well_pruned_fingerprint_is_not_flagged() -> None:
    evidence = FingerprintEvidence(
        fingerprint="fp-good",
        warehouse="WH_A",
        credits=Decimal("500"),
        executions=100,
        days_observed=14,
        partitions_scanned=50,
        partitions_total=10_000,
    )
    assert rank([], [evidence]) == []


def test_recommendations_are_ranked_by_modelled_saving() -> None:
    small = _warehouse(
        warehouse="WH_SMALL",
        size="Small",
        metered_credits=Decimal("100"),
        attributed_credits=Decimal("10"),
    )
    large = _warehouse(
        warehouse="WH_LARGE",
        size="4X-Large",
        metered_credits=Decimal("5000"),
        attributed_credits=Decimal("200"),
    )
    recommendations = rank([small, large])
    savings = [r.modelled_monthly_credits_saved for r in recommendations]
    assert savings == sorted(savings, reverse=True)


def test_change_record_is_cab_ready() -> None:
    recommendation = next(
        r for r in rank([_warehouse(query_count=0, attributed_credits=Decimal("0"))])
    )
    record = recommendation.change_record(credit_price=Decimal("3.00"))
    for section in (
        "CHANGE RECORD",
        "Modelled saving",
        "Risk",
        "Change:",
        "Rollback:",
        "Verification",
    ):
        assert section in record
    assert "$" in record  # currency when a price is configured


def test_change_record_omits_currency_when_no_price_is_configured() -> None:
    recommendation = next(
        r for r in rank([_warehouse(query_count=0, attributed_credits=Decimal("0"))])
    )
    assert "$" not in recommendation.change_record(credit_price=None)


# ═══════════════════════════════════════════════════════ savings claims ══════
def test_a_claim_requires_human_approval_before_it_is_tracked() -> None:
    """R8: nothing is applied without a recorded approval."""
    claim = SavingsClaim(
        id="c1",
        lever="autosuspend_tuning",
        target="WH_A",
        claimed_monthly_credits=Decimal("100"),
    )
    assert claim.status is ClaimStatus.PROPOSED
    assert claim.approved_by is None

    claim.approve("alice", baseline_daily_credits=Decimal("10"), on=date(2026, 6, 1))
    assert claim.status is ClaimStatus.ACCEPTED
    assert claim.approved_by == "alice"
    assert claim.verifiable_on == date(2026, 6, 15)


def test_a_met_claim_is_reported_as_met() -> None:
    claim = SavingsClaim(id="c", lever="l", target="t", claimed_monthly_credits=Decimal("90"))
    claim.approve("alice", baseline_daily_credits=Decimal("10"), on=date(2026, 6, 1))
    verify(claim, Decimal("7"), on=date(2026, 6, 20))  # 3/day saved → 90/month
    assert claim.realisation is Realisation.MET
    assert claim.realised_monthly_credits == Decimal("90.0")


def test_an_under_delivered_claim_is_not_rounded_up() -> None:
    """The honesty that makes the next number believable."""
    claim = SavingsClaim(id="c", lever="l", target="t", claimed_monthly_credits=Decimal("300"))
    claim.approve("alice", baseline_daily_credits=Decimal("10"), on=date(2026, 6, 1))
    verify(claim, Decimal("9"), on=date(2026, 6, 20))  # only 1/day → 30/month
    assert claim.realisation is Realisation.UNDER
    assert claim.realised_monthly_credits == Decimal("30.0")
    assert claim.variance == Decimal("-270.0")
    assert any("over-estimated" in note for note in claim.notes)


def test_a_change_that_increased_cost_is_reported_as_reversed() -> None:
    claim = SavingsClaim(id="c", lever="l", target="t", claimed_monthly_credits=Decimal("100"))
    claim.approve("alice", baseline_daily_credits=Decimal("10"), on=date(2026, 6, 1))
    verify(claim, Decimal("12"), on=date(2026, 6, 20))
    assert claim.realisation is Realisation.REVERSED
    assert any("did not fall" in note for note in claim.notes)


def test_verification_before_the_window_closes_is_refused() -> None:
    claim = SavingsClaim(id="c", lever="l", target="t", claimed_monthly_credits=Decimal("100"))
    claim.approve("alice", baseline_daily_credits=Decimal("10"), on=date(2026, 6, 1))
    verify(claim, Decimal("5"), on=date(2026, 6, 3))
    assert claim.realisation is Realisation.PENDING
    assert any("Too early" in note for note in claim.notes)


def test_ledger_realisation_rate_is_null_before_anything_is_verified() -> None:
    """R3: unmeasured is not unsuccessful."""
    ledger = SavingsLedger()
    ledger.add(SavingsClaim(id="c", lever="l", target="t", claimed_monthly_credits=Decimal("100")))
    assert ledger.realisation_rate is None
    assert "No claims verified yet" in ledger.summary()


def test_ledger_reports_realised_against_claimed() -> None:
    ledger = SavingsLedger()
    for index, (claimed, observed) in enumerate(
        [(Decimal("90"), Decimal("7")), (Decimal("300"), Decimal("9"))]
    ):
        claim = SavingsClaim(
            id=f"c{index}", lever="l", target=f"t{index}", claimed_monthly_credits=claimed
        )
        claim.approve("alice", baseline_daily_credits=Decimal("10"), on=date(2026, 6, 1))
        verify(claim, observed, on=date(2026, 6, 20))
        ledger.add(claim)

    assert ledger.total_claimed == Decimal("390")
    assert ledger.total_realised == Decimal("120.0")
    rate = ledger.realisation_rate
    assert rate is not None and Decimal("0.30") < rate < Decimal("0.32")


# ═════════════════════════════════════════════════════════════ alerting ══════
def _rule(**overrides: object) -> AlertRule:
    base: dict[str, object] = {
        "id": "cost.spike",
        "name": "Daily spend spike",
        "metric_id": "cost.billed_credits",
        "condition": Condition.ABOVE,
        "threshold": Decimal("500"),
        "tier": AlertTier.P2,
        "runbook_url": "https://runbooks.example.com/spend-spike",
    }
    base.update(overrides)
    return AlertRule(**base)  # type: ignore[arg-type]


def test_a_rule_without_a_runbook_is_rejected() -> None:
    """§27.10: an alert nobody knows how to action is noise by construction."""
    with pytest.raises(AlertRuleError, match="runbook"):
        _rule(runbook_url="")
    with pytest.raises(AlertRuleError, match="runbook_url must be"):
        _rule(runbook_url="see the wiki")


def test_a_breach_fires_once_and_re_fires_are_suppressed() -> None:
    """Anti-fatigue: one open alert, not one per evaluation (§14)."""
    engine = AlertEngine([_rule()])
    first = engine.evaluate("cost.spike", Decimal("900"))
    assert first is not None
    assert engine.evaluate("cost.spike", Decimal("950")) is None
    assert engine.evaluate("cost.spike", Decimal("1000")) is None
    assert engine.ledger.suppressed(first.dedup_key) == 2
    assert engine.statistics["cost.spike"].fires == 1


def test_resolution_allows_the_alert_to_fire_again() -> None:
    engine = AlertEngine([_rule()])
    event = engine.evaluate("cost.spike", Decimal("900"))
    assert event is not None
    engine.evaluate("cost.spike", Decimal("100"))  # back under threshold → resolves
    assert engine.evaluate("cost.spike", Decimal("900")) is not None


def test_the_same_rule_on_different_scopes_is_different_alerts() -> None:
    engine = AlertEngine([_rule()])
    a = engine.evaluate("cost.spike", Decimal("900"), scope={"warehouse": "WH_A"})
    b = engine.evaluate("cost.spike", Decimal("900"), scope={"warehouse": "WH_B"})
    assert a is not None and b is not None
    assert a.dedup_key != b.dedup_key


def test_persistence_stops_a_single_breach_from_paging() -> None:
    engine = AlertEngine([_rule(persistence=3)])
    assert engine.evaluate("cost.spike", Decimal("900")) is None
    assert engine.evaluate("cost.spike", Decimal("900")) is None
    assert engine.evaluate("cost.spike", Decimal("900")) is not None


def test_an_unknown_value_never_fires() -> None:
    """R3: missing data is not a breach."""
    engine = AlertEngine([_rule()])
    assert engine.evaluate("cost.spike", None) is None


def test_payload_carries_the_runbook_and_never_query_text() -> None:
    engine = AlertEngine([_rule()])
    event = engine.evaluate("cost.spike", Decimal("900"), scope={"team": "TEAM_ML"})
    assert event is not None
    payload = event.payload()
    assert payload["runbook"].startswith("https://")
    assert payload["metric"] == "cost.billed_credits"
    assert payload["scope"] == "team=TEAM_ML"
    # §14: outbound payloads never carry query text.
    assert not any("SELECT" in str(value).upper() for value in payload.values())


def test_tiers_carry_their_routing_and_ack_expectations() -> None:
    assert AlertTier.P1.ack_minutes == 15
    assert "page" in AlertTier.P1.channels
    assert AlertTier.P4.ack_minutes is None
    assert AlertTier.P4.channels == ("digest",)


def test_a_rule_nobody_acts_on_is_proposed_for_pruning() -> None:
    """§14: required anti-fatigue behaviour."""
    engine = AlertEngine([_rule()])
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for day in range(0, 70, 10):
        moment = start + timedelta(days=day)
        event = engine.evaluate("cost.spike", Decimal("900"), now=moment)
        if event is not None:
            engine.acknowledge(event, "alice", actioned=False)
            engine.ledger.resolve(event.dedup_key, at=moment + timedelta(hours=1))

    proposals = engine.prune_proposals(now=start + timedelta(days=70))
    assert proposals
    assert "nobody has acted" in proposals[0]


def test_an_actioned_rule_is_not_proposed_for_pruning() -> None:
    engine = AlertEngine([_rule()])
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for day in range(0, 70, 10):
        moment = start + timedelta(days=day)
        event = engine.evaluate("cost.spike", Decimal("900"), now=moment)
        if event is not None:
            engine.acknowledge(event, "alice", actioned=True)
            engine.ledger.resolve(event.dedup_key, at=moment + timedelta(hours=1))
    assert engine.prune_proposals(now=start + timedelta(days=70)) == []


def test_backtest_reports_what_a_rule_would_have_done() -> None:
    """§14 OFFLINE: validate a rule against the uploaded window before enabling it."""
    series = [(DAY0 + timedelta(days=i), Decimal("100")) for i in range(30)]
    series[10] = (series[10][0], Decimal("900"))
    series[20] = (series[20][0], Decimal("800"))

    result = backtest(_rule(), series)
    assert result.would_have_fired == 2
    assert len(result.firing_days) == 2
    assert "would have fired 2 time(s)" in result.summary()


def test_backtest_reports_a_rule_that_would_never_fire() -> None:
    series = [(DAY0 + timedelta(days=i), Decimal("100")) for i in range(30)]
    result = backtest(_rule(threshold=Decimal("100000")), series)
    assert result.would_have_fired == 0
    assert "too loose to be useful" in result.summary()


def test_rule_exports_as_snowflake_alert_ddl() -> None:
    """OFFLINE mode cannot notify; a validated rule leaves as deployable DDL."""
    ddl = to_snowflake_alert_ddl(_rule(scope={"warehouse": "WH_A"}), warehouse="WH_SNOWOBS_APP")
    assert "CREATE OR REPLACE ALERT ALERT_COST_SPIKE" in ddl
    assert "WAREHOUSE = WH_SNOWOBS_APP" in ddl
    assert "runbooks.example.com" in ddl
    assert "warehouse = 'WH_A'" in ddl


# ══════════════════════════════════ detection against the fixture account ════
@pytest.fixture(scope="module")
def generated():  # type: ignore[no-untyped-def]
    from snowobs_fixtures.config import GeneratorConfig
    from snowobs_fixtures.generator import generate

    return generate(GeneratorConfig(days=120))


def test_planted_spend_spike_is_detected_and_attributed(generated) -> None:  # type: ignore[no-untyped-def]
    """§24 Phase 5 exit: planted phenomena are detected and correctly attributed."""
    phenomenon = generated.ground_truth.get("ph-spend-spike")
    daily: dict[date, Decimal] = defaultdict(Decimal)
    for row in generated.tables["metering_daily_history"]:
        if row["SERVICE_TYPE"] == "WAREHOUSE_METERING":
            daily[date.fromisoformat(row["USAGE_DATE"])] += Decimal(row["CREDITS_BILLED"])

    anomalies = detect([Point(day=day, value=value) for day, value in daily.items()])
    assert anomalies, "the planted 4x spike was not detected"
    assert anomalies[0].day == phenomenon.window_start

    # And it is attributable to the named warehouse.
    spike_day = phenomenon.window_start
    assert spike_day is not None
    on_day: dict[str, Decimal] = defaultdict(Decimal)
    baseline: dict[str, Decimal] = defaultdict(Decimal)
    for row in generated.tables["warehouse_metering_history"]:
        day = date.fromisoformat(str(row["START_TIME"])[:10])
        credits = Decimal(row["CREDITS_USED_COMPUTE"])
        if day == spike_day:
            on_day[str(row["WAREHOUSE_NAME"])] += credits
        elif abs((day - spike_day).days) <= 7:
            baseline[str(row["WAREHOUSE_NAME"])] += credits / Decimal(14)

    decomposed = decompose(anomalies[0], {"warehouse": on_day}, {"warehouse": baseline})
    assert decomposed.contributions
    assert decomposed.contributions[0].member == phenomenon.subjects[0]


def test_planted_oversized_warehouse_is_recommended_for_downsizing(generated) -> None:  # type: ignore[no-untyped-def]
    phenomenon = generated.ground_truth.get("ph-oversized-wh")
    warehouse = phenomenon.subjects[0]

    metered = sum(
        (
            Decimal(r["CREDITS_USED_COMPUTE"])
            for r in generated.tables["warehouse_metering_history"]
            if r["WAREHOUSE_NAME"] == warehouse
        ),
        Decimal(0),
    )
    attributed = sum(
        (
            Decimal(r["CREDITS_ATTRIBUTED_COMPUTE"])
            for r in generated.tables["query_attribution_history"]
            if r["WAREHOUSE_NAME"] == warehouse
        ),
        Decimal(0),
    )
    queries = [r for r in generated.tables["query_history"] if r["WAREHOUSE_NAME"] == warehouse]
    config = next(r for r in generated.tables["warehouses"] if r["NAME"] == warehouse)

    evidence = WarehouseEvidence(
        warehouse=warehouse,
        size=str(config["SIZE"]),
        auto_suspend_seconds=int(config["AUTO_SUSPEND"]),
        min_clusters=int(config["MIN_CLUSTER_COUNT"]),
        max_clusters=int(config["MAX_CLUSTER_COUNT"]),
        days_observed=120,
        metered_credits=metered,
        attributed_credits=attributed,
        query_count=len(queries),
        queued_overload_ms=sum(int(r["QUEUED_OVERLOAD_TIME"]) for r in queries),
        elapsed_ms=sum(int(r["TOTAL_ELAPSED_TIME"]) for r in queries),
        spill_remote_bytes=sum(int(r["BYTES_SPILLED_TO_REMOTE_STORAGE"]) for r in queries),
        workload_class="adhoc",
    )
    recommendations = rank([evidence])
    assert any(r.lever is LeverId.RIGHTSIZE for r in recommendations), (
        f"utilisation was {evidence.utilisation:.0%}; expected a downsizing recommendation"
    )


def test_planted_zombie_warehouse_is_flagged(generated) -> None:  # type: ignore[no-untyped-def]
    phenomenon = generated.ground_truth.get("ph-zombie-warehouse")
    warehouse = phenomenon.subjects[0]
    cutoff = phenomenon.window_start
    assert cutoff is not None

    metered = sum(
        (
            Decimal(r["CREDITS_USED_COMPUTE"])
            for r in generated.tables["warehouse_metering_history"]
            if r["WAREHOUSE_NAME"] == warehouse and str(r["START_TIME"])[:10] >= cutoff.isoformat()
        ),
        Decimal(0),
    )
    queries = [
        r
        for r in generated.tables["query_history"]
        if r["WAREHOUSE_NAME"] == warehouse and str(r["START_TIME"])[:10] >= cutoff.isoformat()
    ]
    evidence = WarehouseEvidence(
        warehouse=warehouse,
        size="Large",
        auto_suspend_seconds=3600,
        min_clusters=1,
        max_clusters=1,
        days_observed=30,
        metered_credits=metered,
        attributed_credits=Decimal(0),
        query_count=len(queries),
        queued_overload_ms=0,
        elapsed_ms=0,
    )
    assert any(r.lever is LeverId.ZOMBIE for r in rank([evidence]))


def test_planted_fingerprint_regression_is_flagged(generated) -> None:  # type: ignore[no-untyped-def]
    phenomenon = generated.ground_truth.get("ph-fingerprint-regression")
    fingerprint = phenomenon.subjects[0]
    cutoff = phenomenon.window_start
    assert cutoff is not None

    rows = [
        r
        for r in generated.tables["query_history"]
        if r["QUERY_PARAMETERIZED_HASH"] == fingerprint
        and str(r["START_TIME"])[:10] >= cutoff.isoformat()
    ]
    credits = sum(
        (
            Decimal(r["CREDITS_ATTRIBUTED_COMPUTE"])
            for r in generated.tables["query_attribution_history"]
            if r["QUERY_PARAMETERIZED_HASH"] == fingerprint
            and str(r["START_TIME"])[:10] >= cutoff.isoformat()
        ),
        Decimal(0),
    )
    evidence = FingerprintEvidence(
        fingerprint=fingerprint,
        warehouse=phenomenon.subjects[1],
        credits=credits,
        executions=len(rows),
        days_observed=60,
        partitions_scanned=sum(int(r["PARTITIONS_SCANNED"]) for r in rows),
        partitions_total=sum(int(r["PARTITIONS_TOTAL"]) for r in rows),
    )
    recommendations = rank([], [evidence])
    assert any(r.lever is LeverId.QUERY_OPTIMISATION for r in recommendations)


def test_planted_ai_spend_growth_is_visible_to_the_forecaster(generated) -> None:  # type: ignore[no-untyped-def]
    phenomenon = generated.ground_truth.get("ph-ai-spend-growth")
    daily: dict[date, Decimal] = defaultdict(Decimal)
    for row in generated.tables["cortex_functions_usage_history"]:
        daily[date.fromisoformat(str(row["START_TIME"])[:10])] += Decimal(row["TOKEN_CREDITS"])

    observations = [Observation(day=day, value=value) for day, value in sorted(daily.items())]
    forecast = forecast_series(observations, horizon_days=14)
    assert forecast.usable
    # A growing series must forecast upward.
    assert forecast.components.trend_slope > 0
    assert phenomenon.window_start is not None
