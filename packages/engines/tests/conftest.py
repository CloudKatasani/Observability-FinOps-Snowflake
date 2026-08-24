"""Shared fixtures: a fully ingested synthetic account behind both engines.

The lake holds one account's ``ACCOUNT_USAGE`` extract plus the organization
account's ``ORGANIZATION_USAGE`` extract, which is how a real deployment exports
them: the org-scoped views are published once, from the organization account,
and name every account in their own schema. Both halves are needed here because
the metric catalogue spans both — a D10 metric has nothing to execute against
without the organization export.

``USAGE_IN_CURRENCY_DAILY`` is deliberately taken from the organization export
only. The single-account generator emits its own copy, but that view is
organization-scoped, and landing both would count one account's spend twice.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from snowobs_engines.cache import ResultCache
from snowobs_engines.duckdb_engine import DuckDBEngine
from snowobs_engines.snowflake_compat import install as install_snowflake_compat
from snowobs_fixtures.config import GeneratorConfig
from snowobs_fixtures.generator import generate, write_csv
from snowobs_fixtures.organization import (
    DEFAULT_ACCOUNT_PROFILES,
    OrganizationConfig,
    generate_organization,
    write_organization_csv,
)
from snowobs_ingest.catalog import DuckDBCatalog
from snowobs_ingest.loader import IngestPipeline
from snowobs_semantics.compiler import SemanticCompiler

FIXTURE_CONFIG = GeneratorConfig(days=21, queries_per_day=600)

#: The account whose ACCOUNT_USAGE extract the lake holds. It is the base
#: profile of the organization fixture, so the two halves describe the same
#: fleet rather than two unrelated ones.
FIXTURE_ACCOUNT = "ACME_PROD"

#: Only the organization-scoped views are taken from this, so the per-account
#: workload is generated as small as it can be while still producing spend,
#: storage, transfer, and balance rows across the same window.
ORGANIZATION_CONFIG = OrganizationConfig(
    days=FIXTURE_CONFIG.days,
    end_date=FIXTURE_CONFIG.end_date,
    accounts=tuple(replace(p, queries_per_day=40) for p in DEFAULT_ACCOUNT_PROFILES),
)


@pytest.fixture(scope="session")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate fixtures and ingest them through the real pipeline."""
    extract_dir = tmp_path_factory.mktemp("extract")
    storage = tmp_path_factory.mktemp("lake")
    write_csv(generate(FIXTURE_CONFIG), extract_dir)
    for organization_scoped in extract_dir.glob("usage_in_currency_daily.*"):
        organization_scoped.unlink()
    pipeline = IngestPipeline(storage)
    pipeline.ingest_directory(extract_dir, account=FIXTURE_ACCOUNT)

    organization = generate_organization(ORGANIZATION_CONFIG)
    layout = write_organization_csv(organization, tmp_path_factory.mktemp("org-extract"))
    pipeline.ingest_directory(layout.organization_dir, account=organization.organization_name)
    return storage


@pytest.fixture(scope="session")
def catalog(lake: Path):  # type: ignore[no-untyped-def]
    with DuckDBCatalog(lake) as catalog:
        catalog.register_all()
        # The parity suite executes real Snowflake-dialect SQL against this
        # data; these macros give it Snowflake function semantics. The engine
        # itself never relies on them (see test_engine_does_not_depend_on_compat).
        install_snowflake_compat(catalog.connection)
        yield catalog


@pytest.fixture(scope="session")
def engine(catalog):  # type: ignore[no-untyped-def]
    return DuckDBEngine(catalog, cache=ResultCache())


@pytest.fixture(scope="session")
def compiler() -> SemanticCompiler:
    return SemanticCompiler()
