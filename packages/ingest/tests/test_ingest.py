"""End-to-end offline ingestion, including the abuse and malformed-input cases.

The generated fixtures must travel the *real* pipeline (§7.5) — these tests are
what prove there is no back door into the catalog.
"""

from __future__ import annotations

import csv
import gzip
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from snowobs_fixtures.config import GeneratorConfig
from snowobs_fixtures.generator import generate, write_csv, write_parquet
from snowobs_ingest.catalog import DuckDBCatalog
from snowobs_ingest.coverage import (
    MetricAvailability,
    SourceStatus,
    build_coverage_matrix,
)
from snowobs_ingest.export_script_gen import generate_extract_kit
from snowobs_ingest.loader import IngestPipeline
from snowobs_ingest.mapper import MappingStatus, coerce_value, identify
from snowobs_ingest.profiler import UnreadableFileError, profile_file
from snowobs_semantics.registry import ColumnType, default_registry

CONFIG = GeneratorConfig(days=14, scale="small", queries_per_day=400)


@pytest.fixture(scope="module")
def fixture_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("extract")
    write_csv(generate(CONFIG), directory)
    return directory


@pytest.fixture(scope="module")
def ingested(tmp_path_factory: pytest.TempPathFactory, fixture_dir: Path):
    storage = tmp_path_factory.mktemp("lake")
    pipeline = IngestPipeline(storage)
    summary = pipeline.ingest_directory(fixture_dir)
    catalog = DuckDBCatalog(storage)
    catalog.register_all()
    yield summary, catalog, storage
    catalog.close()


# ------------------------------------------------------------------ profiling
def test_profiles_csv_with_header_and_sample(fixture_dir: Path) -> None:
    profile = profile_file(fixture_dir / "query_history.csv")
    assert profile.delimiter == ","
    assert "QUERY_ID" in profile.header
    assert profile.sample_rows
    assert profile.encoding.startswith("utf-8")


def test_profiles_gzip_and_utf16_and_tsv(tmp_path: Path) -> None:
    rows = "SERVICE_TYPE\tUSAGE_DATE\n WAREHOUSE_METERING\t2026-01-01\n"
    tsv = tmp_path / "tabbed.tsv"
    tsv.write_text(rows, encoding="utf-8")
    assert profile_file(tsv).delimiter == "\t"

    utf16 = tmp_path / "wide.csv"
    utf16.write_bytes("A,B\n1,2\n".encode("utf-16"))
    profile = profile_file(utf16)
    assert profile.encoding.startswith("utf-16")
    assert profile.header == ["A", "B"]

    gz = tmp_path / "packed.csv.gz"
    with gzip.open(gz, "wt", encoding="utf-8") as handle:
        handle.write("A,B\n1,2\n")
    assert profile_file(gz).compressed is True

    bom = tmp_path / "bom.csv"
    bom.write_bytes(b"\xef\xbb\xbfA,B\n1,2\n")
    assert profile_file(bom).header == ["A", "B"]


def test_empty_and_binary_files_are_rejected_not_crashed(tmp_path: Path) -> None:
    empty = tmp_path / "empty.csv"
    empty.write_text("")
    with pytest.raises(UnreadableFileError):
        profile_file(empty)

    fake_parquet = tmp_path / "broken.parquet"
    fake_parquet.write_bytes(b"not actually parquet")
    with pytest.raises(UnreadableFileError):
        profile_file(fake_parquet)

    missing = tmp_path / "nothing.csv"
    with pytest.raises(UnreadableFileError):
        profile_file(missing)


def test_single_enormous_line_does_not_exhaust_memory(tmp_path: Path) -> None:
    # Upload-abuse case: a 20 MB single line must profile from the head only.
    path = tmp_path / "huge_line.csv"
    path.write_text("A,B\n" + ("x" * 20_000_000) + ",1\n", encoding="utf-8")
    profile = profile_file(path)
    assert profile.header == ["A", "B"]


# ------------------------------------------------------------ identification
def test_generated_files_identify_confidently(fixture_dir: Path) -> None:
    registry = default_registry()
    for path in sorted(fixture_dir.glob("*.csv")):
        mapping = identify(profile_file(path), registry)
        assert mapping.status is MappingStatus.CONFIRMED, f"{path.name}: {mapping.reason}"
        assert mapping.confidence >= 0.7


def test_renamed_file_with_wrong_schema_is_not_trusted(tmp_path: Path) -> None:
    # A file named like a cost source but carrying something else must not load.
    path = tmp_path / "metering_daily_history.csv"
    path.write_text("FOO,BAR\n1,2\n", encoding="utf-8")
    mapping = identify(profile_file(path), default_registry())
    assert mapping.status is MappingStatus.NEEDS_CONFIRMATION
    assert "header does not match" in mapping.reason


def test_unknown_file_is_unrecognised_rather_than_guessed(tmp_path: Path) -> None:
    path = tmp_path / "sales_report.csv"
    path.write_text("CUSTOMER,REVENUE\nacme,10\n", encoding="utf-8")
    mapping = identify(profile_file(path), default_registry())
    assert mapping.status is MappingStatus.UNRECOGNISED
    assert mapping.source_id is None


def test_unnamed_file_with_a_perfect_header_match_is_identified(tmp_path: Path) -> None:
    # An arbitrary filename is fine when the header signature is unambiguous.
    path = tmp_path / "mystery.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "SERVICE_TYPE",
                "USAGE_DATE",
                "CREDITS_USED_COMPUTE",
                "CREDITS_USED_CLOUD_SERVICES",
                "CREDITS_USED",
                "CREDITS_ADJUSTMENT_CLOUD_SERVICES",
                "CREDITS_BILLED",
            ]
        )
        writer.writerow(["WAREHOUSE_METERING", "2026-05-01", "10", "1", "11", "-1", "10"])

    result = IngestPipeline(tmp_path / "lake").ingest_file(path)
    assert result.mapping.source_id == "metering_daily_history"
    assert result.landed


def test_operator_confirmation_lands_a_file_identification_would_not(tmp_path: Path) -> None:
    # A redacted export: QUERY_TEXT withheld by the client, so the header covers
    # only 4 of the 5 signature columns — plausible, but not enough to auto-confirm.
    storage = tmp_path / "lake"
    path = tmp_path / "redacted_export.csv"
    path.write_text(
        "QUERY_ID,WAREHOUSE_NAME,START_TIME,TOTAL_ELAPSED_TIME,USER_NAME,EXECUTION_STATUS\n"
        "01a-b-c-d-e,WH_A,2026-05-01 00:00:00.000 -07:00,1200,ALICE,SUCCESS\n",
        encoding="utf-8",
    )
    pipeline = IngestPipeline(storage)
    undecided = pipeline.ingest_file(path)
    assert undecided.needs_confirmation
    assert not undecided.landed
    assert undecided.mapping.candidates  # the shortlist the UI shows the operator
    assert 0.7 <= undecided.mapping.confidence < 0.9

    confirmed = pipeline.ingest_file(path, confirmed_source_id="query_history")
    assert confirmed.landed
    assert confirmed.version is not None
    assert confirmed.version.rows == 1


# --------------------------------------------------------------- type coercion
@pytest.mark.parametrize(
    ("raw", "target", "expected"),
    [
        ("2026-05-01 12:30:00.123 -07:00", ColumnType.TIMESTAMP_LTZ, "2026-05-01 12:30:00.123"),
        ("2026-05-01T12:30:00Z", ColumnType.TIMESTAMP_NTZ, "2026-05-01 12:30:00.000"),
        ("1746102600", ColumnType.TIMESTAMP_LTZ, "2025-05-01 12:30:00.000"),
        ("2026-05-01", ColumnType.DATE, "2026-05-01"),
        ("01/05/2026", ColumnType.DATE, "2026-05-01"),
        ("YES", ColumnType.BOOLEAN, True),
        ("no", ColumnType.BOOLEAN, False),
        ("", ColumnType.NUMBER, None),
        ("NULL", ColumnType.STRING, None),
    ],
)
def test_coercion_handles_snowflake_forms(raw: str, target: ColumnType, expected: object) -> None:
    assert coerce_value(raw, target) == expected


def test_credits_coerce_to_decimal_never_float() -> None:
    value = coerce_value("0.123456789", ColumnType.NUMBER)
    assert isinstance(value, Decimal)
    assert value == Decimal("0.123456789")
    assert not isinstance(value, float)


def test_unparseable_values_raise_rather_than_defaulting_to_zero() -> None:
    from snowobs_ingest.mapper import CoercionError

    with pytest.raises(CoercionError):
        coerce_value("not-a-number", ColumnType.NUMBER)
    with pytest.raises(CoercionError):
        coerce_value("nonsense", ColumnType.TIMESTAMP_LTZ)


# ------------------------------------------------------------------- pipeline
def test_full_fixture_upload_lands_every_source(ingested) -> None:
    summary, _catalog, _storage = ingested
    assert summary.unrecognised == []
    assert summary.pending_confirmation == []
    assert summary.total_rows > 0
    assert {"query_history", "metering_daily_history", "warehouse_metering_history"} <= (
        summary.source_ids()
    )


def test_landed_rows_survive_the_round_trip(ingested) -> None:
    summary, catalog, _storage = ingested
    generated = generate(CONFIG)
    for result in summary.landed:
        assert result.version is not None
        source_id = result.version.source_id
        stats = catalog.stats(source_id)
        assert stats is not None
        assert stats.rows == len(generated.tables[source_id]), source_id


def test_credits_are_decimal_in_the_catalog(ingested) -> None:
    _summary, catalog, _storage = ingested
    rows = catalog.query('SELECT SUM("CREDITS_USED_COMPUTE") FROM "metering_daily_history"')
    total = rows[0][0]
    assert isinstance(total, Decimal)
    assert total > 0


def test_lineage_columns_are_present(ingested) -> None:
    _summary, catalog, _storage = ingested
    columns = catalog.columns_of("query_history")
    assert {"_LOADED_AT", "_SOURCE_VIEW", "_BATCH_ID"} <= set(columns)


def test_parquet_uploads_produce_identical_row_counts(tmp_path: Path) -> None:
    generated = generate(CONFIG)
    parquet_dir = tmp_path / "parquet"
    write_parquet(generated, parquet_dir)

    pipeline = IngestPipeline(tmp_path / "lake")
    summary = pipeline.ingest_directory(parquet_dir)
    assert summary.unrecognised == []
    with DuckDBCatalog(tmp_path / "lake") as catalog:
        catalog.register_all()
        stats = catalog.stats("metering_daily_history")
        assert stats is not None
        assert stats.rows == len(generated.tables["metering_daily_history"])


# ---------------------------------------------------------------- incremental
def test_second_upload_extends_the_window_rather_than_replacing_it(tmp_path: Path) -> None:
    # Two consecutive weeks uploaded separately — the app must end up covering
    # both, not just the most recent one ("data covers X → Y across 2 uploads").
    early = generate(GeneratorConfig(days=7, queries_per_day=100, end_date=date(2026, 8, 6)))
    late = generate(GeneratorConfig(days=7, queries_per_day=100, end_date=date(2026, 8, 13)))

    storage = tmp_path / "lake"
    first_dir, second_dir = tmp_path / "one", tmp_path / "two"
    write_csv(early, first_dir)
    write_csv(late, second_dir)

    pipeline = IngestPipeline(storage)
    pipeline.ingest_directory(first_dir)
    pipeline.ingest_directory(second_dir)

    with DuckDBCatalog(storage) as catalog:
        catalog.register_all()
        stats = catalog.stats("metering_daily_history")
        assert stats is not None
        assert stats.batches == 2
        assert stats.rows == len(early.tables["metering_daily_history"]) + len(
            late.tables["metering_daily_history"]
        )
        assert stats.window == (early.ground_truth.start_date, late.ground_truth.end_date)


def test_overlapping_upload_deduplicates_last_write_wins(tmp_path: Path) -> None:
    # Re-uploading an overlapping window must merge, never double-count credits.
    generated = generate(GeneratorConfig(days=7, queries_per_day=100))
    storage = tmp_path / "lake"
    first_dir, second_dir = tmp_path / "one", tmp_path / "two"
    write_csv(generated, first_dir)
    write_csv(generated, second_dir)

    pipeline = IngestPipeline(storage)
    pipeline.ingest_directory(first_dir)
    pipeline.ingest_directory(second_dir)

    with DuckDBCatalog(storage) as catalog:
        catalog.register_all()
        stats = catalog.stats("metering_daily_history")
        assert stats is not None
        assert stats.rows == len(generated.tables["metering_daily_history"])
        total = catalog.query('SELECT SUM("CREDITS_BILLED") FROM "metering_daily_history"')[0][0]
        expected = sum(
            Decimal(str(r["CREDITS_BILLED"])) for r in generated.tables["metering_daily_history"]
        )
        assert total == expected  # not doubled


def test_duplicate_grain_rows_are_counted_and_deduplicated(tmp_path: Path) -> None:
    header = [
        "SERVICE_TYPE",
        "USAGE_DATE",
        "CREDITS_USED_COMPUTE",
        "CREDITS_USED_CLOUD_SERVICES",
        "CREDITS_USED",
        "CREDITS_ADJUSTMENT_CLOUD_SERVICES",
        "CREDITS_BILLED",
    ]
    row = ["WAREHOUSE_METERING", "2026-05-01", "10", "1", "11", "-1", "10"]
    path = tmp_path / "metering_daily_history.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerow(row)
        writer.writerow(row)  # exact duplicate on the grain

    pipeline = IngestPipeline(tmp_path / "lake")
    result = pipeline.ingest_file(path)
    assert result.report is not None
    assert result.report.duplicate_grain_rows == 1
    assert result.version is not None
    assert result.version.rows == 1


# ---------------------------------------------------------------- validation
def test_bad_rows_are_quarantined_with_a_reason(tmp_path: Path) -> None:
    header = [
        "SERVICE_TYPE",
        "USAGE_DATE",
        "CREDITS_USED_COMPUTE",
        "CREDITS_USED_CLOUD_SERVICES",
        "CREDITS_USED",
        "CREDITS_ADJUSTMENT_CLOUD_SERVICES",
        "CREDITS_BILLED",
    ]
    path = tmp_path / "metering_daily_history.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerow(["WAREHOUSE_METERING", "2026-05-01", "10", "1", "11", "-1", "10"])
        writer.writerow(["WAREHOUSE_METERING", "2026-05-02", "oops", "1", "11", "-1", "10"])
        writer.writerow(["WAREHOUSE_METERING", "", "10", "1", "11", "-1", "10"])

    result = IngestPipeline(tmp_path / "lake").ingest_file(path)
    assert result.report is not None
    assert result.report.rows_read == 3
    assert result.report.rows_accepted == 1
    assert result.report.rows_rejected == 2
    reasons = {r.reason for r in result.report.rejects}
    assert any("not a number" in reason for reason in reasons)
    assert any("null" in reason for reason in reasons)


def test_missing_required_column_disables_the_source_with_an_explanation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "metering_daily_history.csv"
    # CREDITS_BILLED (required) omitted entirely.
    path.write_text(
        "SERVICE_TYPE,USAGE_DATE,CREDITS_USED_COMPUTE,CREDITS_USED_CLOUD_SERVICES,"
        "CREDITS_USED,CREDITS_ADJUSTMENT_CLOUD_SERVICES\n"
        "WAREHOUSE_METERING,2026-05-01,10,1,11,-1\n",
        encoding="utf-8",
    )
    result = IngestPipeline(tmp_path / "lake").ingest_file(path)
    assert result.report is not None
    assert "CREDITS_BILLED" in result.report.missing_required_columns
    assert not result.report.usable
    assert not result.landed  # never lands a half-usable cost source


def test_missing_optional_column_is_backfilled_from_the_registry(tmp_path: Path) -> None:
    path = tmp_path / "warehouse_metering_history.csv"
    # CREDITS_USED_CLOUD_SERVICES is optional with default 0.
    path.write_text(
        "START_TIME,END_TIME,WAREHOUSE_ID,WAREHOUSE_NAME,CREDITS_USED,CREDITS_USED_COMPUTE\n"
        "2026-05-01 00:00:00.000 -07:00,2026-05-01 01:00:00.000 -07:00,1,WH_A,4,4\n",
        encoding="utf-8",
    )
    storage = tmp_path / "lake"
    result = IngestPipeline(storage).ingest_file(path)
    assert result.landed
    assert result.report is not None
    assert "CREDITS_USED_CLOUD_SERVICES" in result.report.missing_optional_columns
    with DuckDBCatalog(storage) as catalog:
        catalog.register_all()
        value = catalog.query(
            'SELECT "CREDITS_USED_CLOUD_SERVICES" FROM "warehouse_metering_history"'
        )[0][0]
        assert value == Decimal("0.000000000")


def test_new_columns_are_absorbed_additively_and_logged_as_drift(tmp_path: Path) -> None:
    path = tmp_path / "metering_daily_history.csv"
    path.write_text(
        "SERVICE_TYPE,USAGE_DATE,CREDITS_USED_COMPUTE,CREDITS_USED_CLOUD_SERVICES,"
        "CREDITS_USED,CREDITS_ADJUSTMENT_CLOUD_SERVICES,CREDITS_BILLED,BRAND_NEW_COLUMN\n"
        "WAREHOUSE_METERING,2026-05-01,10,1,11,-1,10,surprise\n",
        encoding="utf-8",
    )
    storage = tmp_path / "lake"
    result = IngestPipeline(storage).ingest_file(path)
    assert result.landed
    assert result.report is not None
    assert result.report.drift_new_columns == ["BRAND_NEW_COLUMN"]
    with DuckDBCatalog(storage) as catalog:
        catalog.register_all()
        assert "BRAND_NEW_COLUMN" in catalog.columns_of("metering_daily_history")


# ------------------------------------------------------------------ coverage
def test_coverage_matrix_reports_present_and_missing_sources(ingested) -> None:
    summary, catalog, _storage = ingested
    matrix = build_coverage_matrix(catalog, mode="offline")
    landed = summary.source_ids()

    for source_id in landed:
        assert matrix.source(source_id).status is not SourceStatus.MISSING
        assert matrix.source(source_id).rows > 0

    missing = [s for s in matrix.sources if s.status is SourceStatus.MISSING]
    assert missing  # not every registered view is in the fixture
    for entry in missing:
        # R3: never a bare "no data" — always a remediation.
        assert entry.remediation
        assert entry.source_id in entry.remediation or entry.snowflake_object in entry.remediation


def test_coverage_matrix_reports_data_window(ingested) -> None:
    _summary, catalog, _storage = ingested
    matrix = build_coverage_matrix(catalog, mode="offline")
    window = matrix.data_window()
    assert window is not None
    assert (window[1] - window[0]).days >= CONFIG.days - 2


def test_live_mode_remediation_is_a_grant_statement(ingested) -> None:
    _summary, catalog, _storage = ingested
    matrix = build_coverage_matrix(catalog, mode="live")
    missing = [s for s in matrix.sources if s.blocking and s.remediation]
    grants = [s for s in missing if s.remediation and "GRANT DATABASE ROLE" in s.remediation]
    assert grants
    for entry in grants:
        assert "IMPORTED PRIVILEGES" not in (entry.remediation or "")


def test_metric_availability_names_its_blocker(ingested) -> None:
    _summary, catalog, _storage = ingested
    matrix = build_coverage_matrix(
        catalog,
        metric_requirements={
            "cost.total_credits": ["metering_daily_history"],
            "sec.sensitive_reads": ["access_history"],  # not in the fixture
        },
        mode="offline",
    )
    by_id = {m.metric_id: m for m in matrix.metrics}
    assert by_id["cost.total_credits"].availability is MetricAvailability.ENABLED
    blocked = by_id["sec.sensitive_reads"]
    assert blocked.availability is MetricAvailability.UNAVAILABLE
    assert blocked.missing_sources == ["access_history"]
    assert "access_history" in blocked.explanation


def test_stale_source_is_reported_as_stale_not_missing(ingested) -> None:
    _summary, catalog, _storage = ingested
    # Assess as though it were far in the future: the data is old, not absent.
    matrix = build_coverage_matrix(
        catalog,
        mode="offline",
        as_of=datetime(2027, 1, 1),  # noqa: DTZ001
    )
    query_history = matrix.source("query_history")
    assert query_history.status is SourceStatus.STALE
    assert query_history.rows > 0


# --------------------------------------------------------------- extract kit
def test_extract_kit_covers_the_registry_and_is_read_only() -> None:
    kit = generate_extract_kit(days=90, file_format="PARQUET")
    assert set(kit.names) == {
        "01_extract.sql",
        "02_download.sh",
        "02_download.ps1",
        "03_manifest.json",
        "README.md",
    }
    sql = kit["01_extract.sql"]
    registry = default_registry()
    for source in registry:
        assert source.id in sql
    # Read-only: no DDL/DML against customer objects, and no blanket grants.
    upper = sql.upper()
    for forbidden in ("DROP ", "DELETE ", "INSERT ", "UPDATE ", "IMPORTED PRIVILEGES"):
        assert forbidden not in upper
    assert "COPY INTO @~/snowobs" in sql
    assert "DATEADD(day, -90" in sql


def test_extract_kit_manifest_lists_required_roles() -> None:
    import json

    manifest = json.loads(generate_extract_kit()["03_manifest.json"])
    entries = {f["source_id"]: f for f in manifest["files"]}
    assert entries["query_history"]["required_db_role"] == "SNOWFLAKE.USAGE_VIEWER"
    assert entries["access_history"]["required_db_role"] == "SNOWFLAKE.GOVERNANCE_VIEWER"
