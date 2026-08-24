"""Shared fixtures: a real ingested account and a tool context over it.

The agent tests run against the same synthetic account the rest of the suite
uses, so a tool result in a test is a genuine metric answer rather than a
hand-written string. That matters most for the grounding tests: a fabrication
check is only meaningful if the figures it is checked against are real.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from snowobs_agents.runtime.tools import ToolContext
from snowobs_engines.duckdb_engine import DuckDBEngine
from snowobs_fixtures.config import GeneratorConfig
from snowobs_fixtures.generator import generate, write_csv
from snowobs_ingest.catalog import DuckDBCatalog
from snowobs_ingest.coverage import build_coverage_matrix
from snowobs_ingest.loader import IngestPipeline
from snowobs_semantics.compiler import SemanticCompiler
from snowobs_semantics.model import default_model

FIXTURE = GeneratorConfig(days=14, queries_per_day=400)


@pytest.fixture(scope="session")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root: Path = tmp_path_factory.mktemp("agent-lake")
    extract: Path = tmp_path_factory.mktemp("agent-extract")
    write_csv(generate(FIXTURE), extract)
    IngestPipeline(root).ingest_directory(extract)
    return root


@pytest.fixture
def context(lake: Path) -> Iterator[ToolContext]:
    catalog = DuckDBCatalog(lake, tenant="default")
    catalog.register_all()
    try:
        model = default_model()
        yield ToolContext(
            engine=DuckDBEngine(catalog),
            compiler=SemanticCompiler(),
            model=model,
            actor="tester@example.com",
            # An agent that cannot see coverage cannot honour R3 — it would
            # have to guess whether a gap is a zero or an absence.
            coverage=build_coverage_matrix(catalog, metric_requirements=model.requirements()),
        )
    finally:
        catalog.close()


@pytest.fixture
def admin_context(lake: Path) -> Iterator[ToolContext]:
    """A caller holding the admin role, with the ad-hoc escape hatch enabled."""
    catalog = DuckDBCatalog(lake, tenant="default")
    catalog.register_all()
    try:
        yield ToolContext(
            engine=DuckDBEngine(catalog),
            compiler=SemanticCompiler(),
            model=default_model(),
            actor="admin@example.com",
            roles=frozenset({"platform_admin"}),
            allow_adhoc_sql=True,
        )
    finally:
        catalog.close()
