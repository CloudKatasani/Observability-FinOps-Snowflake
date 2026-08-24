"""Golden SQL snapshots: every metric's compiled SQL is pinned, per dialect.

Compilation is pure, so a change to any rendering shows up here as a diff in
review rather than as a silently different number in production (§8.3).

Regenerate deliberately with ``SNOWOBS_UPDATE_GOLDEN=1 pytest packages/engines``
and read the diff before committing it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from snowobs_semantics.compiler import MetricRequest, SemanticCompiler
from snowobs_semantics.dialect_shims import Dialect
from snowobs_semantics.model import default_model

GOLDEN_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "golden" / "sql"
UPDATE = os.environ.get("SNOWOBS_UPDATE_GOLDEN") == "1"

ALL_METRIC_IDS = default_model().metric_ids()


def _snapshot_path(metric_id: str, dialect: Dialect) -> Path:
    return GOLDEN_DIR / f"{metric_id}.{dialect.value}.sql"


@pytest.mark.parametrize("metric_id", ALL_METRIC_IDS)
@pytest.mark.parametrize("dialect", list(Dialect))
def test_compiled_sql_matches_the_snapshot(
    metric_id: str, dialect: Dialect, compiler: SemanticCompiler
) -> None:
    request = MetricRequest(metrics=[metric_id], limit=100)
    compiled = compiler.compile(request, dialect)
    path = _snapshot_path(metric_id, dialect)

    if UPDATE or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(compiled.sql + "\n", encoding="utf-8")
        if not UPDATE:
            pytest.skip(f"created missing snapshot {path.name}")
        return

    expected = path.read_text(encoding="utf-8").strip()
    assert compiled.sql.strip() == expected, (
        f"Compiled SQL for {metric_id} ({dialect.value}) changed. If intended, "
        f"regenerate with SNOWOBS_UPDATE_GOLDEN=1 and review the diff."
    )


def test_every_metric_has_snapshots_in_both_dialects() -> None:
    """A metric without pinned SQL is a metric whose rendering can drift unseen."""
    missing = [
        _snapshot_path(metric_id, dialect).name
        for metric_id in ALL_METRIC_IDS
        for dialect in Dialect
        if not _snapshot_path(metric_id, dialect).exists()
    ]
    assert not missing, f"missing golden SQL snapshots: {missing}"


def test_snapshots_do_not_outlive_their_metrics() -> None:
    """A stale snapshot for a deleted metric is dead weight; fail rather than rot."""
    if not GOLDEN_DIR.is_dir():
        pytest.skip("no snapshots yet")
    known = {
        _snapshot_path(metric_id, dialect).name
        for metric_id in ALL_METRIC_IDS
        for dialect in Dialect
    }
    orphans = [path.name for path in GOLDEN_DIR.glob("*.sql") if path.name not in known]
    assert not orphans, f"snapshots for metrics that no longer exist: {orphans}"
