"""Latency budgets for the dashboard surface (BUILD_PROMPT §22.3).

Two kinds of assertion live here, and the difference matters.

The *deterministic* ones — a warm read is a cache hit, a tile is one statement
— hold on any machine and are the real regression guards. The *timing* ones run
against a moderate fixture with deliberately generous bounds, because a shared
CI runner is not a performance lab: they are here to catch an order-of-magnitude
regression, not to certify a p95.

The large profile in §22.3 (90 days, 50M queries) is not generated here — it
would take longer to build than the rest of the suite takes to run. It belongs
in the nightly job alongside the live parity run, and is recorded as such in
docs/ASSUMPTIONS.md rather than quietly skipped.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from snowobs_api.services.metrics import MetricService
from snowobs_common.config import Settings
from snowobs_fixtures.config import GeneratorConfig
from snowobs_fixtures.generator import generate, write_csv
from snowobs_ingest.loader import IngestPipeline
from snowobs_semantics.compiler import MetricRequest

#: A month of a busy account: ~60k queries. Big enough that a quadratic
#: mistake shows up, small enough to generate inside a test run.
PROFILE = GeneratorConfig(days=30, queries_per_day=2_000)

#: §22.3 targets, relaxed for a shared runner. A regression that matters —
#: a missing cache, a lost index, a fan-out — moves these by 10x, not 20%.
WARM_TILE_BUDGET_MS = 300.0
COLD_TILE_BUDGET_MS = 3_000.0
CI_TOLERANCE = 4.0


@pytest.fixture(scope="module")
def settings(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Settings]:
    lake: Path = tmp_path_factory.mktemp("perf-lake")
    extract: Path = tmp_path_factory.mktemp("perf-extract")
    write_csv(generate(PROFILE), extract)
    IngestPipeline(lake).ingest_directory(extract)
    yield Settings(_env_file=None, storage={"provider": "local", "bucket": str(lake)})


def tile(service: MetricService, metric_id: str) -> tuple[object, float]:
    started = time.perf_counter()
    value = service.tile(metric_id, MetricRequest(metrics=[metric_id], bucket_time=False))
    return value, (time.perf_counter() - started) * 1000


def test_a_warm_tile_is_served_from_the_cache(settings: Settings) -> None:
    """The deterministic half: a repeat read must not recompute.

    This is what makes the warm budget achievable at all, and unlike a timing
    assertion it cannot pass by accident on a fast machine.
    """
    service = MetricService(settings)
    tile(service, "cost.billed_credits")
    # Reaching into the private cache is deliberate: the cache *is* the thing
    # under test, and a timing assertion cannot distinguish a hit from a fast miss.
    before = service._cache.hits
    tile(service, "cost.billed_credits")
    assert service._cache.hits == before + 1


def test_a_warm_tile_meets_its_latency_budget(settings: Settings) -> None:
    service = MetricService(settings)
    tile(service, "cost.billed_credits")  # prime
    timings = [tile(service, "cost.billed_credits")[1] for _ in range(5)]
    worst = max(timings)
    assert worst < WARM_TILE_BUDGET_MS * CI_TOLERANCE, (
        f"warm tile took {worst:.0f} ms against a {WARM_TILE_BUDGET_MS:.0f} ms target"
    )


@pytest.mark.parametrize(
    "metric_id",
    [
        "cost.billed_credits",  # a small daily fact
        "q.volume",  # 60k rows
        "cost.by_warehouse_credits",  # a join across two facts
        "cost.unattributed_share",  # a ratio over the attribution join
    ],
)
def test_a_cold_tile_meets_its_latency_budget(settings: Settings, metric_id: str) -> None:
    """Cold means a fresh service, and so a fresh cache and a fresh catalog."""
    _, elapsed = tile(MetricService(settings), metric_id)
    assert elapsed < COLD_TILE_BUDGET_MS * CI_TOLERANCE, (
        f"cold tile for {metric_id} took {elapsed:.0f} ms against a "
        f"{COLD_TILE_BUDGET_MS:.0f} ms target"
    )


def test_a_tile_runs_one_statement_not_one_per_day(settings: Settings) -> None:
    """A tile is a single aggregate. Per-day fan-out is the shape to catch.

    Asserted structurally rather than by clock: the compiled statement must not
    group by time when the tile asks for a period total.
    """
    from snowobs_semantics.compiler import SemanticCompiler
    from snowobs_semantics.dialect_shims import Dialect

    compiled = SemanticCompiler().compile(
        MetricRequest(metrics=["cost.billed_credits"], bucket_time=False),
        Dialect.DUCKDB,
    )
    assert "GROUP BY" not in compiled.sql.upper()
    assert compiled.sql.upper().count("SELECT") <= 2  # the aggregate, plus its CTE
