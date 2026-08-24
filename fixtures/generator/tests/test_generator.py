"""The generator must be deterministic, schema-faithful, and plant every phenomenon.

These assertions are the contract between the generator and every downstream
engine: if a phenomenon stops being detectable here, the analytics tests that
depend on it are testing nothing.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from snowobs_fixtures.account import (
    REGRESSION_FINGERPRINT,
    SPILL_FINGERPRINT,
    WH_OVERSIZED,
    WH_QUEUED,
    WH_SPILL,
    WH_ZOMBIE,
)
from snowobs_fixtures.config import GeneratorConfig
from snowobs_fixtures.generator import GeneratedAccount, generate, write_csv
from snowobs_semantics.registry import default_registry

CONFIG = GeneratorConfig(days=120)


@pytest.fixture(scope="module")
def generated() -> GeneratedAccount:
    return generate(CONFIG)


def _sum(rows: list[dict[str, object]], column: str) -> Decimal:
    return sum((Decimal(str(r[column])) for r in rows), Decimal(0))


def _daily(rows: list[dict[str, object]], date_col: str, value_col: str) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for row in rows:
        totals[str(row[date_col])[:10]] += Decimal(str(row[value_col]))
    return totals


# --------------------------------------------------------------- determinism
def test_generation_is_deterministic() -> None:
    first = generate(GeneratorConfig(days=10))
    second = generate(GeneratorConfig(days=10))
    assert first.tables.keys() == second.tables.keys()
    for source_id in first.tables:
        assert first.tables[source_id] == second.tables[source_id], source_id


def test_different_seeds_produce_different_data() -> None:
    a = generate(GeneratorConfig(days=10, seed=1))
    b = generate(GeneratorConfig(days=10, seed=2))
    assert a.tables["query_history"] != b.tables["query_history"]


# ------------------------------------------------------------ schema fidelity
def test_every_generated_source_is_registered_and_column_faithful(
    generated: GeneratedAccount,
) -> None:
    registry = default_registry()
    for source_id, rows in generated.tables.items():
        source = registry.get(source_id)  # raises if unregistered
        if not rows:
            continue
        produced = {c.upper() for c in rows[0]}
        declared = {c.name.upper() for c in source.columns}
        assert produced <= declared, f"{source_id} emits undeclared columns: {produced - declared}"
        required = {c.name.upper() for c in source.required_columns}
        assert required <= produced, f"{source_id} missing required: {required - produced}"


def test_generated_files_identify_against_the_registry(
    generated: GeneratedAccount, tmp_path: Path
) -> None:
    written = write_csv(generated, tmp_path)
    registry = default_registry()
    for source_id, path in written.items():
        match = registry.match_filename(path.name)
        assert match is not None and match.source_id == source_id


def test_credit_columns_are_decimal_strings(generated: GeneratedAccount) -> None:
    # §27.7: no floating point anywhere in credits or currency.
    for row in generated.tables["query_attribution_history"][:200]:
        value = row["CREDITS_ATTRIBUTED_COMPUTE"]
        assert isinstance(value, str)
        assert "e" not in value.lower() and Decimal(value) >= 0


# ------------------------------------------------------------- cost integrity
def test_hourly_metering_reconciles_to_daily_metering(generated: GeneratedAccount) -> None:
    hourly = _sum(generated.tables["warehouse_metering_history"], "CREDITS_USED_COMPUTE")
    daily = _sum(
        [
            r
            for r in generated.tables["metering_daily_history"]
            if r["SERVICE_TYPE"] == "WAREHOUSE_METERING"
        ],
        "CREDITS_USED_COMPUTE",
    )
    assert hourly == daily  # exact, not approximate


def test_cloud_services_adjustment_follows_the_ten_percent_rule(
    generated: GeneratedAccount,
) -> None:
    rows = [
        r
        for r in generated.tables["metering_daily_history"]
        if r["SERVICE_TYPE"] == "WAREHOUSE_METERING"
    ]
    assert rows
    for row in rows:
        compute = Decimal(str(row["CREDITS_USED_COMPUTE"]))
        cloud = Decimal(str(row["CREDITS_USED_CLOUD_SERVICES"]))
        adjustment = Decimal(str(row["CREDITS_ADJUSTMENT_CLOUD_SERVICES"]))
        billed = Decimal(str(row["CREDITS_BILLED"]))
        assert adjustment <= 0
        assert -adjustment <= cloud  # rebate never exceeds actual usage
        assert -adjustment <= compute * Decimal("0.10")
        assert billed == compute + cloud + adjustment


def test_attributed_credits_never_exceed_metered_credits(generated: GeneratedAccount) -> None:
    metered = _sum(
        [
            r
            for r in generated.tables["metering_daily_history"]
            if r["SERVICE_TYPE"] == "WAREHOUSE_METERING"
        ],
        "CREDITS_USED_COMPUTE",
    )
    attributed = _sum(generated.tables["query_attribution_history"], "CREDITS_ATTRIBUTED_COMPUTE")
    assert attributed < metered
    idle_share = (metered - attributed) / metered
    # A realistic account has material but not dominant idle.
    assert Decimal("0.15") < idle_share < Decimal("0.50")


# ---------------------------------------------------------- planted phenomena
def test_oversized_warehouse_has_low_utilisation(generated: GeneratedAccount) -> None:
    expectation = generated.ground_truth.get("ph-oversized-wh")
    metered = _sum(
        [
            r
            for r in generated.tables["warehouse_metering_history"]
            if r["WAREHOUSE_NAME"] == WH_OVERSIZED
        ],
        "CREDITS_USED_COMPUTE",
    )
    attributed = _sum(
        [
            r
            for r in generated.tables["query_attribution_history"]
            if r["WAREHOUSE_NAME"] == WH_OVERSIZED
        ],
        "CREDITS_ATTRIBUTED_COMPUTE",
    )
    utilisation = attributed / metered * 100
    assert utilisation <= Decimal(str(expectation.expectations["max_utilisation_pct"]))


def test_queueing_warehouse_shows_sustained_overload(generated: GeneratedAccount) -> None:
    expectation = generated.ground_truth.get("ph-queueing")
    rows = [r for r in generated.tables["query_history"] if r["WAREHOUSE_NAME"] == WH_QUEUED]
    queued = sum(int(r["QUEUED_OVERLOAD_TIME"]) for r in rows)
    elapsed = sum(int(r["TOTAL_ELAPSED_TIME"]) for r in rows)
    assert queued / elapsed * 100 >= expectation.expectations["min_queue_time_pct"]


def test_fingerprint_regression_becomes_the_top_offender(generated: GeneratedAccount) -> None:
    phenomenon = generated.ground_truth.get("ph-fingerprint-regression")
    assert phenomenon.window_start is not None
    cutoff = phenomenon.window_start.isoformat()

    before = after = Decimal(0)
    for row in generated.tables["query_attribution_history"]:
        if row["QUERY_PARAMETERIZED_HASH"] != REGRESSION_FINGERPRINT:
            continue
        credits = Decimal(str(row["CREDITS_ATTRIBUTED_COMPUTE"]))
        if str(row["START_TIME"])[:10] >= cutoff:
            after += credits
        else:
            before += credits

    days_before = (phenomenon.window_start - generated.ground_truth.start_date).days
    days_after = (generated.ground_truth.end_date - phenomenon.window_start).days + 1
    ratio = (after / days_after) / (before / days_before)
    assert ratio >= Decimal(str(phenomenon.expectations["min_cost_increase_ratio"]))

    # And the cause is visible: pruning collapses on the regressed rows.
    regressed = [
        r
        for r in generated.tables["query_history"]
        if r["QUERY_PARAMETERIZED_HASH"] == REGRESSION_FINGERPRINT
        and str(r["START_TIME"])[:10] >= cutoff
    ]
    worst = min(int(r["PARTITIONS_SCANNED"]) / int(r["PARTITIONS_TOTAL"]) for r in regressed)
    assert worst >= 0.9


def test_remote_spill_is_concentrated_on_one_fingerprint(generated: GeneratedAccount) -> None:
    expectation = generated.ground_truth.get("ph-remote-spill")
    spilling = [
        r
        for r in generated.tables["query_history"]
        if int(r["BYTES_SPILLED_TO_REMOTE_STORAGE"]) > 0
    ]
    assert spilling
    assert {r["QUERY_PARAMETERIZED_HASH"] for r in spilling} == {SPILL_FINGERPRINT}
    assert {r["WAREHOUSE_NAME"] for r in spilling} == {WH_SPILL}
    total = sum(int(r["BYTES_SPILLED_TO_REMOTE_STORAGE"]) for r in spilling)
    assert total >= expectation.expectations["min_remote_spill_bytes"]


def test_single_day_spend_spike_is_attributable(generated: GeneratedAccount) -> None:
    phenomenon = generated.ground_truth.get("ph-spend-spike")
    assert phenomenon.window_start is not None
    spike_day = phenomenon.window_start.isoformat()

    daily = _daily(
        [
            r
            for r in generated.tables["metering_daily_history"]
            if r["SERVICE_TYPE"] == "WAREHOUSE_METERING"
        ],
        "USAGE_DATE",
        "CREDITS_BILLED",
    )
    ordered = sorted(daily.values())
    median = ordered[len(ordered) // 2]
    assert daily[spike_day] / median >= Decimal(str(phenomenon.expectations["min_spike_ratio"]))

    # Attributable to the named warehouse.
    warehouse = phenomenon.subjects[0]
    on_day = [
        r
        for r in generated.tables["warehouse_metering_history"]
        if str(r["START_TIME"])[:10] == spike_day
    ]
    by_wh: dict[str, Decimal] = defaultdict(Decimal)
    for row in on_day:
        by_wh[str(row["WAREHOUSE_NAME"])] += Decimal(str(row["CREDITS_USED_COMPUTE"]))
    assert max(by_wh, key=lambda k: by_wh[k]) == warehouse


def test_task_root_failure_fans_out_to_skipped_children(generated: GeneratedAccount) -> None:
    phenomenon = generated.ground_truth.get("ph-task-root-failure")
    assert phenomenon.window_start is not None
    day = phenomenon.window_start.isoformat()
    same_day = [r for r in generated.tables["task_history"] if str(r["SCHEDULED_TIME"])[:10] == day]
    failed = [r for r in same_day if r["STATE"] == "FAILED"]
    skipped = [r for r in same_day if r["STATE"] == "SKIPPED"]

    assert [r["NAME"] for r in failed] == [phenomenon.subjects[0]]
    assert len(skipped) == phenomenon.expectations["downstream_failures"]
    assert {r["GRAPH_ROOT_TASK_ID"] for r in skipped} == {phenomenon.subjects[0]}


def test_dynamic_table_breaches_target_lag_for_three_days(generated: GeneratedAccount) -> None:
    phenomenon = generated.ground_truth.get("ph-dt-lag")
    table = phenomenon.subjects[0]
    breaching_days: set[str] = set()
    for row in generated.tables["dynamic_table_refresh_history"]:
        if row["QUALIFIED_NAME"] != table:
            continue
        # Actual lag = refresh start minus the data timestamp it caught up to.
        start = str(row["REFRESH_START_TIME"])[:19]
        data_ts = str(row["DATA_TIMESTAMP"])[:19]
        lag_seconds = (
            _seconds(start) - _seconds(data_ts)
            if start[:10] == data_ts[:10]
            else int(row["TARGET_LAG_SEC"]) * 4
        )
        if lag_seconds > int(row["TARGET_LAG_SEC"]):
            breaching_days.add(start[:10])
    assert len(breaching_days) >= int(phenomenon.expectations["consecutive_days"])


def _seconds(timestamp: str) -> int:
    hh, mm, ss = timestamp[11:19].split(":")
    return int(hh) * 3600 + int(mm) * 60 + int(ss)


def test_untagged_spend_is_material_and_concentrated(generated: GeneratedAccount) -> None:
    phenomenon = generated.ground_truth.get("ph-untagged-spend")
    by_warehouse: dict[str, Decimal] = defaultdict(Decimal)
    for row in generated.tables["warehouse_metering_history"]:
        by_warehouse[str(row["WAREHOUSE_NAME"])] += Decimal(str(row["CREDITS_USED_COMPUTE"]))
    total = sum(by_warehouse.values(), Decimal(0))
    untagged = sum((by_warehouse[name] for name in phenomenon.subjects), Decimal(0))
    pct = untagged / total * 100
    assert Decimal(str(phenomenon.expectations["min_untagged_pct"])) <= pct
    assert pct <= Decimal(str(phenomenon.expectations["max_untagged_pct"]))

    # And those queries genuinely carry no team tag.
    untagged_queries = [
        r for r in generated.tables["query_history"] if r["WAREHOUSE_NAME"] in phenomenon.subjects
    ]
    assert untagged_queries
    assert all(r["QUERY_TAG"] == "" for r in untagged_queries)


def test_dormant_cohort_stops_logging_in(generated: GeneratedAccount) -> None:
    phenomenon = generated.ground_truth.get("ph-dormant-users")
    cutoff = (generated.ground_truth.end_date - timedelta(days=90)).isoformat()
    recent_logins = {
        str(r["USER_NAME"])
        for r in generated.tables["login_history"]
        if str(r["EVENT_TIMESTAMP"])[:10] >= cutoff
    }
    dormant = set(phenomenon.subjects)
    assert len(dormant) == phenomenon.expectations["cohort_size"]
    assert not (dormant & recent_logins)


def test_privilege_drift_grant_appears(generated: GeneratedAccount) -> None:
    phenomenon = generated.ground_truth.get("ph-privilege-drift")
    grants = [
        r
        for r in generated.tables["grants_to_users"]
        if r["ROLE"] == phenomenon.expectations["granted_role"]
    ]
    assert len(grants) == 1
    assert grants[0]["GRANTEE_NAME"] == phenomenon.subjects[0]
    assert grants[0]["GRANTED_BY"] == "ACCOUNTADMIN"


def test_clone_and_time_travel_storage_phenomena(generated: GeneratedAccount) -> None:
    clone = generated.ground_truth.get("ph-clone-growth")
    rows = generated.tables["table_storage_metrics"]
    clone_rows = [r for r in rows if r["TABLE_NAME"] == clone.subjects[0]]
    assert len(clone_rows) == 1
    assert (
        float(clone_rows[0]["RETAINED_FOR_CLONE_BYTES"])
        >= clone.expectations["min_retained_for_clone_bytes"]
    )

    tt = generated.ground_truth.get("ph-time-travel-excess")
    for database in tt.subjects:
        db_rows = [r for r in rows if r["TABLE_CATALOG"] == database and r["ACTIVE_BYTES"]]
        assert db_rows
        ratio = sum(float(r["TIME_TRAVEL_BYTES"]) for r in db_rows) / sum(
            float(r["ACTIVE_BYTES"]) for r in db_rows
        )
        assert ratio >= tt.expectations["min_time_travel_ratio"]


def test_ai_spend_appears_late_and_grows(generated: GeneratedAccount) -> None:
    phenomenon = generated.ground_truth.get("ph-ai-spend-growth")
    daily = _daily(
        generated.tables["cortex_functions_usage_history"], "START_TIME", "TOKEN_CREDITS"
    )
    days = sorted(daily)
    assert phenomenon.window_start is not None
    assert days[0] == phenomenon.window_start.isoformat()  # nothing before week 10
    growth = daily[days[-1]] / daily[days[0]]
    assert growth >= Decimal(str(phenomenon.expectations["min_growth_ratio"]))


def test_zombie_warehouse_burns_credits_without_queries(generated: GeneratedAccount) -> None:
    phenomenon = generated.ground_truth.get("ph-zombie-warehouse")
    assert phenomenon.window_start is not None
    cutoff = phenomenon.window_start.isoformat()
    credits = _sum(
        [
            r
            for r in generated.tables["warehouse_metering_history"]
            if r["WAREHOUSE_NAME"] == WH_ZOMBIE and str(r["START_TIME"])[:10] >= cutoff
        ],
        "CREDITS_USED_COMPUTE",
    )
    queries = [
        r
        for r in generated.tables["query_history"]
        if r["WAREHOUSE_NAME"] == WH_ZOMBIE and str(r["START_TIME"])[:10] >= cutoff
    ]
    assert credits > 0
    assert queries == []


def test_ground_truth_is_written_alongside_the_data(
    generated: GeneratedAccount, tmp_path: Path
) -> None:
    write_csv(generated, tmp_path)
    assert (tmp_path / "ground_truth.json").exists()
    assert (tmp_path / "03_manifest.json").exists()
