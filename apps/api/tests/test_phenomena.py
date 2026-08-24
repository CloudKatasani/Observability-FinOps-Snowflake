"""Every planted phenomenon is detected by the platform (BUILD_PROMPT §26).

The generator's own suite proves each phenomenon was *planted*. This proves the
platform *finds* it: the fixture account is ingested through the real OFFLINE
path and queried through the governed metric layer, and each assertion is made
against `ground_truth.py` rather than against a number copied out of a previous
run. A metric that quietly stopped surfacing its phenomenon would still return a
plausible figure, so nothing here checks that a query merely succeeded.

Detection through the analytics engines — forecasting, anomaly decomposition,
lever ranking — is asserted in `packages/analytics/tests/test_analytics.py`.
This file covers the phenomena whose detection route is a KPI.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from snowobs_engines.duckdb_engine import DuckDBEngine
from snowobs_fixtures.config import GeneratorConfig
from snowobs_fixtures.generator import generate, write_csv
from snowobs_fixtures.ground_truth import GroundTruth
from snowobs_ingest.catalog import DuckDBCatalog
from snowobs_ingest.loader import IngestPipeline
from snowobs_semantics.compiler import MetricRequest, SemanticCompiler, TimeRange
from snowobs_semantics.dialect_shims import Dialect

# 120 days: several phenomena are defined over a window (AI spend growth, the
# dormant cohort) and cannot be seen in a fortnight.
FIXTURE = GeneratorConfig(days=120)


@pytest.fixture(scope="module")
def account(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, GroundTruth]]:
    lake: Path = tmp_path_factory.mktemp("phenomena-lake")
    extract: Path = tmp_path_factory.mktemp("phenomena-extract")
    generated = generate(FIXTURE)
    write_csv(generated, extract)
    IngestPipeline(lake).ingest_directory(extract)
    yield lake, generated.ground_truth


@pytest.fixture(scope="module")
def query(account: tuple[Path, GroundTruth]):  # type: ignore[no-untyped-def]
    """Run a governed metric over the landed account, as the app would."""
    lake, ground_truth = account
    catalog = DuckDBCatalog(lake, tenant="default")
    catalog.register_all()
    engine = DuckDBEngine(catalog)
    compiler = SemanticCompiler()
    window = TimeRange(start=ground_truth.start_date, end=ground_truth.end_date)

    def run(
        metric: str,
        *,
        dimensions: list[str] | None = None,
        by_time: bool = False,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        request = MetricRequest(
            metrics=[metric],
            dimensions=dimensions or [],
            time_range=window,
            bucket_time=by_time,
            limit=limit,
        )
        return engine.execute(compiler.compile(request, Dialect.DUCKDB)).dicts()

    try:
        yield run
    finally:
        catalog.close()


def _column(rows: list[dict[str, Any]], metric: str) -> str:
    return metric.replace(".", "_").upper()


def _scalar(rows: list[dict[str, Any]], metric: str) -> Decimal:
    assert rows, f"{metric} returned no rows at all"
    value = rows[0][_column(rows, metric)]
    assert value is not None, f"{metric} returned NULL — unknown, not detected"
    return Decimal(str(value))


def _ranked(rows: list[dict[str, Any]], metric: str, dimension: str) -> list[tuple[str, Decimal]]:
    column = _column(rows, metric)
    ranked = [
        (str(row[dimension.upper()]), Decimal(str(row[column] or 0)))
        for row in rows
        if row.get(dimension.upper()) is not None
    ]
    return sorted(ranked, key=lambda pair: -pair[1])


# ────────────────────────────────────────────────────────── cost & chargeback
def test_untagged_spend_surfaces_as_the_unattributed_share(
    query, account: tuple[Path, GroundTruth]
) -> None:  # type: ignore[no-untyped-def]
    """The phenomenon a chargeback programme exists to eliminate."""
    _, ground_truth = account
    phenomenon = ground_truth.get("ph-untagged-spend")

    share = _scalar(query("cost.unattributed_share"), "cost.unattributed_share")
    # Material enough to be worth a conversation, and not the whole account.
    assert Decimal("0.05") < share < Decimal("0.60"), share

    # And it is concentrated on the warehouses that were planted untagged.
    ranked = _ranked(
        query("cost.by_warehouse_credits", dimensions=["warehouse"]),
        "cost.by_warehouse_credits",
        "warehouse",
    )
    top_names = [name for name, _ in ranked[:4]]
    assert any(subject in top_names for subject in phenomenon.subjects), (
        f"none of {phenomenon.subjects} is among the top spenders {top_names}"
    )


def test_the_unattributed_bucket_is_reported_rather_than_dropped(query) -> None:  # type: ignore[no-untyped-def]
    """R3 at the chargeback layer: unallocated cost is a figure, not a gap."""
    credits = _scalar(query("chargeback.unattributed_credits"), "chargeback.unattributed_credits")
    assert credits > 0


# ──────────────────────────────────────────────────────────────── warehouse
def test_the_queueing_warehouse_shows_sustained_overload(
    query, account: tuple[Path, GroundTruth]
) -> None:  # type: ignore[no-untyped-def]
    _, ground_truth = account
    phenomenon = ground_truth.get("ph-queueing")

    ranked = _ranked(
        query("wh.queue_overload_pct", dimensions=["warehouse"]),
        "wh.queue_overload_pct",
        "warehouse",
    )
    assert ranked, "queue overload returned nothing"
    worst, share = ranked[0]
    assert worst == phenomenon.subjects[0], f"worst queueing warehouse was {worst}"
    assert share > 0, "the planted saturation reads as zero queueing"


def test_the_zombie_warehouse_burns_credits_with_no_queries(
    query, account: tuple[Path, GroundTruth]
) -> None:  # type: ignore[no-untyped-def]
    """Idle credit with no work behind it — invisible on a per-query view."""
    _, ground_truth = account
    warehouse = ground_truth.get("ph-zombie-warehouse").subjects[0]

    rows = query("cost.idle_credits", dimensions=["warehouse"])
    idle = dict(_ranked(rows, "cost.idle_credits", "warehouse"))
    assert warehouse in idle, f"{warehouse} has no idle credits recorded"
    assert idle[warehouse] > 0


# ──────────────────────────────────────────────────────────────────── query
def test_remote_spill_is_concentrated_on_the_planted_fingerprint(
    query, account: tuple[Path, GroundTruth]
) -> None:  # type: ignore[no-untyped-def]
    _, ground_truth = account
    phenomenon = ground_truth.get("ph-remote-spill")

    ranked = _ranked(
        query("q.spill_remote_bytes", dimensions=["fingerprint"]),
        "q.spill_remote_bytes",
        "fingerprint",
    )
    assert ranked, "no remote spill detected at all"
    assert ranked[0][0] == phenomenon.subjects[0]
    assert ranked[0][1] > 0


# ───────────────────────────────────────────────────────────────── pipelines
def test_the_root_task_failure_and_its_downstream_fan_out_are_both_counted(
    query, account: tuple[Path, GroundTruth]
) -> None:  # type: ignore[no-untyped-def]
    """A root failure that skips its children is one incident, not thirteen.

    Both figures matter: the root count tells an operator what to fix, the
    skipped count tells them how much did not run because of it.
    """
    _, ground_truth = account
    ground_truth.get("ph-task-root-failure")

    assert _scalar(query("pipe.root_failures"), "pipe.root_failures") >= 1
    assert _scalar(query("pipe.skipped_downstream"), "pipe.skipped_downstream") >= 1


def test_the_dynamic_table_lag_breach_is_detected(query, account: tuple[Path, GroundTruth]) -> None:  # type: ignore[no-untyped-def]
    _, ground_truth = account
    ground_truth.get("ph-dt-lag")
    assert _scalar(query("pipe.dt_lag_breaches"), "pipe.dt_lag_breaches") >= 1


# ────────────────────────────────────────────────────────────────── security
def test_the_dormant_cohort_is_detected(query, account: tuple[Path, GroundTruth]) -> None:  # type: ignore[no-untyped-def]
    _, ground_truth = account
    phenomenon = ground_truth.get("ph-dormant-users")
    dormant = _scalar(query("sec.dormant_users"), "sec.dormant_users")
    assert dormant >= len(phenomenon.subjects), (
        f"{dormant} dormant users found, expected at least {len(phenomenon.subjects)}"
    )


def test_the_privilege_drift_grant_is_detected(query, account: tuple[Path, GroundTruth]) -> None:  # type: ignore[no-untyped-def]
    """One over-privileged service account among fifty ordinary ones."""
    _, ground_truth = account
    ground_truth.get("ph-privilege-drift")
    assert _scalar(query("sec.privileged_grants"), "sec.privileged_grants") >= 1


# ─────────────────────────────────────────────────────────────────── storage
def test_clone_retained_storage_is_detected(query, account: tuple[Path, GroundTruth]) -> None:  # type: ignore[no-untyped-def]
    """Storage kept alive by a clone, which reads as free until someone looks."""
    _, ground_truth = account
    ground_truth.get("ph-clone-growth")
    assert _scalar(query("storage.clone_retained_bytes"), "storage.clone_retained_bytes") > 0


def test_excess_time_travel_is_detected(query, account: tuple[Path, GroundTruth]) -> None:  # type: ignore[no-untyped-def]
    _, ground_truth = account
    ground_truth.get("ph-time-travel-excess")
    assert _scalar(query("storage.time_travel_bytes"), "storage.time_travel_bytes") > 0
    ratio = _scalar(query("storage.time_travel_ratio"), "storage.time_travel_ratio")
    assert ratio > 0


# ──────────────────────────────────────────────────────────────────────── AI
def test_ai_spend_is_present_and_material(query, account: tuple[Path, GroundTruth]) -> None:  # type: ignore[no-untyped-def]
    """Planted to start late and grow — the shape a new Cortex workload has."""
    _, ground_truth = account
    ground_truth.get("ph-ai-spend-growth")
    assert _scalar(query("ai.total_credits"), "ai.total_credits") > 0
    assert _scalar(query("ai.share_of_credits"), "ai.share_of_credits") > 0


# ───────────────────────────────────────────────────── the completeness gate
def test_every_planted_phenomenon_has_a_detection_test(
    account: tuple[Path, GroundTruth],
) -> None:
    """The gate that stops this file rotting as phenomena are added.

    The Definition of Done requires *all* planted phenomena to be detected. A
    new phenomenon added to the generator without a detection test would
    otherwise leave that claim quietly false, so the claim is checked here
    rather than trusted.
    """
    import pathlib

    _, ground_truth = account
    covered = set()
    for path in (
        pathlib.Path(__file__),
        pathlib.Path(__file__).parents[3] / "packages/analytics/tests/test_analytics.py",
    ):
        text = path.read_text(encoding="utf-8")
        covered |= {p.id for p in ground_truth.phenomena if f'"{p.id}"' in text}

    missing = sorted({p.id for p in ground_truth.phenomena} - covered)
    assert not missing, (
        f"planted but never asserted as detected: {missing}. Add a detection "
        "test here (KPI route) or in test_analytics.py (analytics route)."
    )
