"""Shared fixtures: a fully ingested synthetic account behind both engines."""

from __future__ import annotations

from pathlib import Path

import pytest

from snowobs_engines.cache import ResultCache
from snowobs_engines.duckdb_engine import DuckDBEngine
from snowobs_engines.snowflake_compat import install as install_snowflake_compat
from snowobs_fixtures.config import GeneratorConfig
from snowobs_fixtures.generator import generate, write_csv
from snowobs_ingest.catalog import DuckDBCatalog
from snowobs_ingest.loader import IngestPipeline
from snowobs_semantics.compiler import SemanticCompiler

FIXTURE_CONFIG = GeneratorConfig(days=21, queries_per_day=600)


@pytest.fixture(scope="session")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate fixtures and ingest them through the real pipeline."""
    extract_dir = tmp_path_factory.mktemp("extract")
    storage = tmp_path_factory.mktemp("lake")
    write_csv(generate(FIXTURE_CONFIG), extract_dir)
    IngestPipeline(storage).ingest_directory(extract_dir)
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
