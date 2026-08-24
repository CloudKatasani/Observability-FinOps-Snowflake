"""The source registry is the single place source knowledge lives (§7.1)."""

from __future__ import annotations

import pytest
import yaml

from snowobs_common.errors import ConfigurationError
from snowobs_semantics import SOURCES_DIR
from snowobs_semantics.registry import Criticality, load_registry


@pytest.fixture(scope="module")
def registry():
    return load_registry()


def test_every_yaml_loads_and_ids_match_filenames(registry) -> None:
    assert len(registry) >= 50
    for source in registry:
        assert (SOURCES_DIR / f"{source.id}.yaml").exists()


def test_core_cost_sources_are_registered(registry) -> None:
    # These are what the allocation engine and reconciliation gate depend on.
    for source_id in (
        "query_history",
        "query_attribution_history",
        "warehouse_metering_history",
        "metering_daily_history",
        "usage_in_currency_daily",
        "warehouses",
    ):
        source = registry.get(source_id)
        assert source.columns
        assert source.csv.header_signature


def test_verified_latencies_match_documentation(registry) -> None:
    # Figures verified 2026-08-24 (docs/ASSUMPTIONS.md §1-2). A silent change
    # here would make every freshness banner wrong (R7).
    expected = {
        "query_history": 45,
        "task_history": 45,
        "query_attribution_history": 480,
        "metering_daily_history": 180,
        "warehouse_metering_history": 180,
        "login_history": 120,
        "usage_in_currency_daily": 4320,
        "remaining_balance_daily": 4320,
        "rate_sheet_daily": 1440,
    }
    for source_id, minutes in expected.items():
        assert registry.get(source_id).documented_latency_minutes == minutes, source_id


def test_unverified_latencies_are_flagged(registry) -> None:
    # ASSUMPTIONS U-1: anything not confirmed must say so rather than pretend.
    for source in registry:
        if not source.latency_verified:
            assert source.documented_latency_minutes >= 120


def test_access_history_requires_enterprise(registry) -> None:
    assert registry.get("access_history").edition_min.value == "enterprise"


def test_no_source_requests_blanket_privileges(registry) -> None:
    # R4 / §27.3: granular database roles only.
    for source in registry:
        role = source.required_db_role or ""
        assert "IMPORTED PRIVILEGES" not in role.upper()
        if role:
            assert role.startswith("SNOWFLAKE.")


def test_incremental_sources_declare_a_time_column_and_watermark(registry) -> None:
    for source in registry:
        if source.load_strategy.value == "incremental_watermark":
            assert source.time_column, source.id
            assert source.watermark is not None, source.id
            assert source.watermark.lookback_minutes >= source.documented_latency_minutes


def test_core_sources_declare_the_metrics_they_enable(registry) -> None:
    for source in registry:
        if source.criticality is Criticality.CORE:
            assert source.enables_metrics, source.id


def test_grain_columns_exist_in_the_column_list(registry) -> None:
    for source in registry:
        for column in source.grain:
            assert source.column(column) is not None, f"{source.id}.{column}"


def test_filename_matching(registry) -> None:
    assert registry.match_filename("query_history.csv").source_id == "query_history"
    assert registry.match_filename("QUERY_HISTORY.CSV.GZ").source_id == "query_history"
    assert registry.match_filename("query_history_export.parquet").source_id == "query_history"
    assert registry.match_filename("random_report.csv") is None


def test_header_matching_scores_signature_coverage(registry) -> None:
    source = registry.get("metering_daily_history")
    exact = registry.match_header([c.name for c in source.columns])
    assert exact[0].source_id == "metering_daily_history"
    assert exact[0].confidence == 1.0

    # A partial header falls below the threshold rather than guessing.
    assert not any(
        m.source_id == "metering_daily_history"
        for m in registry.match_header(["SERVICE_TYPE", "SOMETHING_ELSE"])
    )


def test_duplicate_ids_are_rejected(tmp_path) -> None:
    source = yaml.safe_load((SOURCES_DIR / "query_history.yaml").read_text())
    (tmp_path / "query_history.yaml").write_text(yaml.safe_dump(source))
    source["snowflake_object"] = "SNOWFLAKE.ACCOUNT_USAGE.OTHER"
    (tmp_path / "other.yaml").write_text(yaml.safe_dump(source))
    with pytest.raises(ConfigurationError, match="filename must match source id"):
        load_registry(tmp_path)


def test_missing_directory_is_a_configuration_error(tmp_path) -> None:
    with pytest.raises(ConfigurationError):
        load_registry(tmp_path / "nope")
