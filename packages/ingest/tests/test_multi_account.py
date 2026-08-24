"""Account-aware ingestion: many accounts, one lake, no bleed between them.

Real ``ACCOUNT_USAGE`` views carry no account column, so two accounts' extracts
are indistinguishable once they are files on disk. The platform records the
provenance itself (``_ACCOUNT``), and everything downstream — dedup, coverage,
roll-up — has to honour it. The failure this file exists to catch is the quiet
one: account B's upload silently replacing account A's history because both
name the same warehouse at the same hour.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from snowobs_fixtures.config import GeneratorConfig
from snowobs_fixtures.generator import generate, write_csv
from snowobs_fixtures.organization import (
    DEFAULT_ACCOUNT_PROFILES,
    ORGANIZATION_SOURCE_IDS,
    GeneratedOrganization,
    OrganizationConfig,
    OrganizationLayout,
    generate_organization,
    write_organization_csv,
)
from snowobs_ingest.catalog import ACCOUNT_COLUMN, DuckDBCatalog
from snowobs_ingest.coverage import SourceStatus, build_coverage_matrix
from snowobs_ingest.loader import INGEST_COLUMNS, IngestPipeline, LakeWriter
from snowobs_semantics.registry import SourceScope, default_registry

ORG_CONFIG = OrganizationConfig(
    days=14, accounts=tuple(replace(p, queries_per_day=80) for p in DEFAULT_ACCOUNT_PROFILES)
)
#: The window the fixtures are generated for; coverage freshness is judged
#: against it rather than against wall-clock time.
AS_OF = datetime(2026, 8, 21, 12, 0)  # noqa: DTZ001 — naive, matches source stamps


@pytest.fixture(scope="module")
def organization() -> GeneratedOrganization:
    return generate_organization(ORG_CONFIG)


@pytest.fixture(scope="module")
def extracts(
    tmp_path_factory: pytest.TempPathFactory, organization: GeneratedOrganization
) -> OrganizationLayout:
    return write_organization_csv(organization, tmp_path_factory.mktemp("org-extract"))


@pytest.fixture(scope="module")
def lake(tmp_path_factory: pytest.TempPathFactory, extracts: OrganizationLayout, organization):
    storage = tmp_path_factory.mktemp("org-lake")
    pipeline = IngestPipeline(storage)
    for name, directory in extracts.account_dirs.items():
        pipeline.ingest_directory(directory, account=name)
    pipeline.ingest_directory(extracts.organization_dir, account=organization.organization_name)
    catalog = DuckDBCatalog(storage)
    catalog.register_all()
    yield catalog, storage, pipeline
    catalog.close()


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


# --------------------------------------------------------------- the stamp
def test_account_is_an_ingest_metadata_column_not_a_source_column() -> None:
    assert ACCOUNT_COLUMN in INGEST_COLUMNS
    registry = default_registry()
    for source in registry:
        assert source.column(ACCOUNT_COLUMN) is None, source.id


def test_account_stamp_is_populated_and_queryable(lake) -> None:
    catalog, _storage, _pipeline = lake
    assert ACCOUNT_COLUMN in catalog.columns_of("query_history")
    rows = catalog.query(
        f'SELECT DISTINCT "{ACCOUNT_COLUMN}" FROM "query_history" ORDER BY 1'  # noqa: S608
    )
    assert [r[0] for r in rows] == [
        "ACME_ANALYTICS",
        "ACME_APAC",
        "ACME_PROD",
        "ACME_SANDBOX",
    ]
    nulls = catalog.query(
        f'SELECT COUNT(*) FROM "query_history" WHERE "{ACCOUNT_COLUMN}" IS NULL'  # noqa: S608
    )[0][0]
    assert nulls == 0


def test_catalog_exposes_the_accounts_in_the_lake(lake, organization) -> None:
    catalog, _storage, _pipeline = lake
    # The organization is not one of its own accounts. Its extracts are stamped
    # — ORGANIZATION_USAGE is exported once, from whichever account holds the
    # grant — but that stamp names the organization, and offering it in the
    # account picker beside its own members would invite a per-account view of
    # something that has no per-account meaning.
    assert catalog.accounts() == sorted(organization.accounts)
    assert organization.organization_name not in catalog.accounts()
    # Account-scoped views come from the accounts; org-scoped views come from
    # the organization account, and the two sets do not overlap.
    assert catalog.accounts_for("query_history") == sorted(organization.accounts)
    assert catalog.accounts_for("contract_items") == [organization.organization_name]


def test_ingest_records_the_account_on_every_batch(lake) -> None:
    _catalog, _storage, pipeline = lake
    assert all(version.account for version in pipeline.versions)
    accounts = {version.account for version in pipeline.versions}
    assert accounts == {"ACME_PROD", "ACME_ANALYTICS", "ACME_SANDBOX", "ACME_APAC", "ACME_GROUP"}


def test_the_account_is_never_inferred_when_the_uploader_did_not_say(tmp_path: Path) -> None:
    # R3/R12: an unrecorded account is NULL, never a guess from the file name.
    extract = tmp_path / "extract"
    write_csv(generate(GeneratorConfig(days=3, queries_per_day=20)), extract)
    storage = tmp_path / "lake"
    summary = IngestPipeline(storage).ingest_directory(extract)
    assert summary.accounts() == set()
    assert all(
        result.version is not None and result.version.account is None for result in summary.landed
    )
    with DuckDBCatalog(storage) as catalog:
        catalog.register_all()
        assert catalog.accounts() == []
        nulls = catalog.query(
            f'SELECT COUNT(*) FROM "query_history" WHERE "{ACCOUNT_COLUMN}" IS NULL'  # noqa: S608
        )[0][0]
        assert nulls > 0


def test_pipeline_default_account_applies_to_every_file(tmp_path: Path) -> None:
    extract = tmp_path / "extract"
    write_csv(generate(GeneratorConfig(days=3, queries_per_day=20)), extract)
    storage = tmp_path / "lake"
    pipeline = IngestPipeline(storage, account="ACME_PROD")
    summary = pipeline.ingest_directory(extract)
    assert summary.accounts() == {"ACME_PROD"}
    # A per-call account overrides the pipeline default for that file only.
    result = pipeline.ingest_file(extract / "warehouses.csv", account="ACME_APAC")
    assert result.version is not None
    assert result.version.account == "ACME_APAC"


def test_lake_writer_takes_an_account(tmp_path: Path) -> None:
    writer = LakeWriter(tmp_path, "default", "ACME_PROD")
    assert writer.account == "ACME_PROD"
    assert LakeWriter(tmp_path).account is None


# ------------------------------------------------------- accounts stay apart
def test_a_multi_account_lake_keeps_accounts_separate(lake, organization) -> None:
    """The regression this file exists for.

    ``WAREHOUSE_METERING_HISTORY`` is grained on (WAREHOUSE_ID, START_TIME) and
    every account in the fleet runs the same warehouse names at the same hours.
    Without the account in the dedup key, three of the four accounts' metering
    would vanish behind last-write-wins and the org total would be a quarter of
    the truth — with no error anywhere.
    """
    catalog, _storage, _pipeline = lake
    for name, account in organization.accounts.items():
        landed = catalog.query(
            'SELECT COUNT(*), SUM("CREDITS_USED_COMPUTE") FROM "warehouse_metering_history" '  # noqa: S608
            f'WHERE "{ACCOUNT_COLUMN}" = ?',
            [name],
        )[0]
        expected_rows = account.row_count("warehouse_metering_history")
        expected_credits = sum(
            (
                _decimal(r["CREDITS_USED_COMPUTE"])
                for r in account.tables["warehouse_metering_history"]
            ),
            Decimal(0),
        )
        assert landed[0] == expected_rows, name
        assert landed[1] == expected_credits, name


def test_org_rollup_reconciles_to_the_accounts_through_the_catalog(lake) -> None:
    catalog, _storage, _pipeline = lake
    org_total = catalog.query(
        'SELECT SUM("CREDITS_USED_COMPUTE") FROM "org_warehouse_metering_history"'
    )[0][0]
    account_total = catalog.query(
        'SELECT SUM("CREDITS_USED_COMPUTE") FROM "warehouse_metering_history"'
    )[0][0]
    assert isinstance(org_total, Decimal)
    assert org_total - account_total == Decimal(0)  # exactly zero, not "close"


def test_org_scoped_rows_keep_their_own_account_name_column(lake, organization) -> None:
    catalog, _storage, _pipeline = lake
    rows = catalog.query(
        'SELECT DISTINCT "ACCOUNT_NAME" FROM "org_warehouse_metering_history" ORDER BY 1'
    )
    assert [r[0] for r in rows] == sorted(organization.accounts)
    # ...while _ACCOUNT records where the extract itself came from.
    stamps = catalog.query(
        f'SELECT DISTINCT "{ACCOUNT_COLUMN}" FROM "org_warehouse_metering_history"'  # noqa: S608
    )
    assert [r[0] for r in stamps] == [organization.organization_name]


def test_account_extracts_carry_no_organization_scoped_files(extracts) -> None:
    registry = default_registry()
    for directory in extracts.account_dirs.values():
        landed = {path.stem for path in directory.glob("*.csv")}
        assert landed.isdisjoint(ORGANIZATION_SOURCE_IDS)
        assert all(registry.get(s).scope is SourceScope.ACCOUNT for s in landed)


# ------------------------------------------------------------ per-account coverage
def test_coverage_reports_status_per_account(lake, organization) -> None:
    catalog, _storage, _pipeline = lake
    matrix = build_coverage_matrix(catalog, mode="offline", as_of=AS_OF)

    # The organization is not an account: the picker and this matrix agree on
    # the fleet, and neither offers ACME_GROUP as somewhere to look.
    assert matrix.accounts == sorted(organization.accounts)
    query_history = matrix.source("query_history")
    assert query_history.scope == SourceScope.ACCOUNT.value
    by_account = {entry.account: entry for entry in query_history.accounts}
    assert set(by_account) == set(matrix.accounts)
    for name, account in organization.accounts.items():
        assert by_account[name].status is SourceStatus.AVAILABLE
        assert by_account[name].rows == account.row_count("query_history")

    # The aggregate row keeps its original, whole-tenant meaning.
    assert query_history.rows == sum(
        account.row_count("query_history") for account in organization.accounts.values()
    )


def test_organization_scoped_sources_report_against_the_organization(lake, organization) -> None:
    catalog, _storage, _pipeline = lake
    matrix = build_coverage_matrix(catalog, mode="offline", as_of=AS_OF)
    contract = matrix.source("contract_items")
    assert contract.scope == SourceScope.ORGANIZATION.value
    assert contract.status is SourceStatus.AVAILABLE
    assert contract.rows > 0
    # One export covers every account, so there is no per-account breakdown to
    # report. Listing each account against it would read as "every account is
    # missing this" for a source no account owns.
    assert contract.accounts == []
    assert organization.organization_name not in matrix.accounts


def test_account_matrix_answers_what_one_account_is_missing(lake, organization) -> None:
    catalog, _storage, _pipeline = lake
    matrix = build_coverage_matrix(catalog, mode="offline", as_of=AS_OF)
    rows = matrix.account_matrix("ACME_PROD")
    assert len(rows) == len(matrix.account_scoped_sources)
    assert all(row.account == "ACME_PROD" for row in rows)
    landed = {row.source_id for row in rows if row.status is not SourceStatus.MISSING}
    assert "query_history" in landed
    # Organization-scoped sources are not an account's to land, so they are
    # absent from an account's matrix rather than red in it.
    assert "contract_items" not in {row.source_id for row in rows}


def test_partial_account_is_reported_as_partial_not_absent(
    tmp_path: Path, extracts: OrganizationLayout, organization: GeneratedOrganization
) -> None:
    """Account X has query history landed; account Y has only billing.

    The enterprise question. The tenant-wide row says "query_history is
    available" — true, and useless. The per-account rows say which account it
    is available *for*, and name the one that is missing it.
    """
    storage = tmp_path / "lake"
    pipeline = IngestPipeline(storage)
    pipeline.ingest_directory(extracts.account_dirs["ACME_PROD"], account="ACME_PROD")

    partial = extracts.account_dirs["ACME_APAC"]
    for name in ("metering_daily_history.csv", "warehouse_metering_history.csv"):
        pipeline.ingest_file(partial / name, account="ACME_APAC")

    with DuckDBCatalog(storage) as catalog:
        catalog.register_all()
        matrix = build_coverage_matrix(catalog, mode="offline", as_of=AS_OF)

        assert matrix.accounts == ["ACME_APAC", "ACME_PROD"]
        query_history = matrix.source("query_history")
        assert query_history.status is SourceStatus.AVAILABLE  # tenant-wide: present
        prod = query_history.for_account("ACME_PROD")
        apac = query_history.for_account("ACME_APAC")
        assert prod is not None and prod.status is SourceStatus.AVAILABLE
        assert prod.rows == organization.accounts["ACME_PROD"].row_count("query_history")
        assert apac is not None and apac.status is SourceStatus.MISSING
        assert apac.rows == 0

        # Both accounts landed billing — it is a daily-grain source, so at this
        # as-of it reads stale rather than fresh; what matters is that neither
        # account is reported as missing it.
        metering = matrix.source("metering_daily_history")
        for account in ("ACME_PROD", "ACME_APAC"):
            entry = metering.for_account(account)
            assert entry is not None and entry.status is not SourceStatus.MISSING
            assert entry.rows > 0

        blocked = [
            row for row in matrix.account_matrix("ACME_APAC") if row.status is SourceStatus.MISSING
        ]
        assert len(blocked) > 1  # APAC is missing plenty; the matrix says which


# ------------------------------------------- single-account regression guard
def test_single_account_ingestion_is_unchanged(tmp_path: Path) -> None:
    """A deployment that never mentions an account must behave exactly as before."""
    config = GeneratorConfig(days=7, queries_per_day=100)
    generated = generate(config)
    extract = tmp_path / "extract"
    write_csv(generated, extract)

    storage = tmp_path / "lake"
    summary = IngestPipeline(storage).ingest_directory(extract)
    assert summary.unrecognised == []
    assert summary.pending_confirmation == []

    with DuckDBCatalog(storage) as catalog:
        catalog.register_all()
        for source_id in summary.source_ids():
            stats = catalog.stats(source_id)
            assert stats is not None
            assert stats.rows == len(generated.tables[source_id]), source_id
        # Lineage columns unchanged apart from the additive account stamp.
        columns = set(catalog.columns_of("query_history"))
        assert {"_LOADED_AT", "_SOURCE_VIEW", "_BATCH_ID", ACCOUNT_COLUMN} <= columns

        matrix = build_coverage_matrix(catalog, mode="offline", as_of=AS_OF)
        assert matrix.accounts == []
        assert all(source.accounts == [] for source in matrix.sources)
        assert matrix.source("query_history").rows == len(generated.tables["query_history"])


def test_reuploading_the_same_account_still_deduplicates(tmp_path: Path) -> None:
    generated = generate(GeneratorConfig(days=5, queries_per_day=60))
    first, second = tmp_path / "one", tmp_path / "two"
    write_csv(generated, first)
    write_csv(generated, second)
    storage = tmp_path / "lake"

    pipeline = IngestPipeline(storage)
    pipeline.ingest_directory(first, account="ACME_PROD")
    pipeline.ingest_directory(second, account="ACME_PROD")

    with DuckDBCatalog(storage) as catalog:
        catalog.register_all()
        stats = catalog.stats("metering_daily_history")
        assert stats is not None
        assert stats.rows == len(generated.tables["metering_daily_history"])  # merged, not doubled
        total = catalog.query('SELECT SUM("CREDITS_BILLED") FROM "metering_daily_history"')[0][0]
        expected = sum(
            (_decimal(r["CREDITS_BILLED"]) for r in generated.tables["metering_daily_history"]),
            Decimal(0),
        )
        assert total == expected


def test_the_same_extract_under_two_accounts_does_not_deduplicate(tmp_path: Path) -> None:
    """Two accounts that happen to look alike are still two accounts.

    Identical rows tagged with different accounts must both survive: an
    ACCOUNT_USAGE row is only unique *within* its account.
    """
    generated = generate(GeneratorConfig(days=5, queries_per_day=60))
    extract = tmp_path / "extract"
    write_csv(generated, extract)
    storage = tmp_path / "lake"

    pipeline = IngestPipeline(storage)
    pipeline.ingest_directory(extract, account="ACME_PROD")
    pipeline.ingest_directory(extract, account="ACME_APAC")

    with DuckDBCatalog(storage) as catalog:
        catalog.register_all()
        stats = catalog.stats("metering_daily_history")
        assert stats is not None
        assert stats.rows == 2 * len(generated.tables["metering_daily_history"])
        per_account = catalog.stats("metering_daily_history", "ACME_APAC")
        assert per_account is not None
        assert per_account.rows == len(generated.tables["metering_daily_history"])
        assert per_account.account == "ACME_APAC"
