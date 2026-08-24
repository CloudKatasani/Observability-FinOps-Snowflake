"""Dual-engine parity harness (BUILD_PROMPT §22.2) — the critical suite.

For every metric, on the same fixture data: compile for both dialects, execute
both, and compare row-for-row. Counts, sums, and currency/credit figures must be
*exactly* equal (Decimal, not float). A documented tolerance is permitted only
where a shim genuinely differs — every such case is recorded in
``docs/PARITY_EXCEPTIONS.md`` and declared here, never applied silently.

Where no Snowflake account is available, the Snowflake-dialect SQL is compared
against a committed golden snapshot; the live comparison runs in a nightly job
behind the ``snowflake`` marker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from snowobs_engines.base import QueryResult
from snowobs_semantics.compiler import CompiledQuery, MetricRequest, SemanticCompiler
from snowobs_semantics.dialect_shims import Dialect

#: Metrics whose engines legitimately differ, with the tolerance and the reason.
#: Anything not listed here must match exactly.
PARITY_EXCEPTIONS: dict[str, tuple[Decimal, str]] = {
    "q.p50_elapsed_ms": (
        Decimal("0.05"),
        "Snowflake APPROX_PERCENTILE is a t-digest estimate; DuckDB quantile_cont "
        "is exact. The median is the most stable estimate, so the tolerance is the "
        "tightest of the three.",
    ),
    "q.p95_elapsed_ms": (
        Decimal("0.10"),
        "Snowflake APPROX_PERCENTILE is a t-digest estimate; DuckDB quantile_cont "
        "is exact. Measured divergence on fixture data reaches ~4% at p95, so the "
        "tolerance is set above the observed spread rather than at it.",
    ),
    "q.p99_elapsed_ms": (
        Decimal("0.15"),
        "Approximate vs exact percentile. The tail carries the fewest observations "
        "and is therefore the least stable estimate of the three.",
    ),
}


@dataclass
class CellDifference:
    row: int
    column: str
    left: Any
    right: Any
    relative_delta: Decimal | None = None


@dataclass
class ParityReport:
    """The outcome of comparing one metric across engines."""

    metric_ids: list[str]
    matched: bool
    rows_compared: int = 0
    differences: list[CellDifference] = field(default_factory=list)
    tolerance_applied: Decimal | None = None
    note: str = ""

    @property
    def summary(self) -> str:
        if self.matched and self.tolerance_applied is None:
            return f"exact match over {self.rows_compared} rows"
        if self.matched:
            return (
                f"match within {self.tolerance_applied} relative tolerance over "
                f"{self.rows_compared} rows"
            )
        return f"{len(self.differences)} differing cells over {self.rows_compared} rows"


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return Decimal(int(value))
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # Reached only for a non-money column; money is Decimal end to end.
        return Decimal(str(value))
    return None


def _row_sort_key(row: tuple[Any, ...]) -> tuple[str, ...]:
    """A total, type-stable ordering for comparing result sets.

    Values are stringified because a row mixes types (timestamps, strings,
    Decimals) and may contain NULLs, which are not mutually comparable.
    """
    return tuple("\x00" if value is None else str(value) for value in row)


def _tolerance_for(metric_ids: list[str]) -> tuple[Decimal | None, str]:
    tolerances = [PARITY_EXCEPTIONS[m] for m in metric_ids if m in PARITY_EXCEPTIONS]
    if not tolerances:
        return None, ""
    worst = max(tolerances, key=lambda t: t[0])
    return worst[0], worst[1]


def compare_results(left: QueryResult, right: QueryResult, metric_ids: list[str]) -> ParityReport:
    """Compare two engines' results for the same request."""
    tolerance, note = _tolerance_for(metric_ids)
    report = ParityReport(
        metric_ids=list(metric_ids), matched=True, tolerance_applied=tolerance, note=note
    )

    if [c.upper() for c in left.columns] != [c.upper() for c in right.columns]:
        report.matched = False
        report.differences.append(
            CellDifference(row=-1, column="<columns>", left=left.columns, right=right.columns)
        )
        return report

    if len(left.rows) != len(right.rows):
        report.matched = False
        report.differences.append(
            CellDifference(row=-1, column="<row_count>", left=len(left.rows), right=len(right.rows))
        )
        return report

    # Compare row *sets*, not row order. Ties in the ORDER BY are resolved
    # arbitrarily and independently by each engine, so comparing positionally
    # would report differences that are not differences in the numbers.
    left_sorted = sorted(left.rows, key=_row_sort_key)
    right_sorted = sorted(right.rows, key=_row_sort_key)

    report.rows_compared = len(left.rows)
    for index, (left_row, right_row) in enumerate(zip(left_sorted, right_sorted, strict=True)):
        for column, left_value, right_value in zip(left.columns, left_row, right_row, strict=True):
            if _cells_equal(left_value, right_value, tolerance):
                continue
            report.matched = False
            report.differences.append(
                CellDifference(
                    row=index,
                    column=column,
                    left=left_value,
                    right=right_value,
                    relative_delta=_relative_delta(left_value, right_value),
                )
            )
    return report


def _cells_equal(left: Any, right: Any, tolerance: Decimal | None) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False

    left_decimal, right_decimal = _as_decimal(left), _as_decimal(right)
    if left_decimal is None or right_decimal is None:
        return str(left) == str(right)

    if left_decimal == right_decimal:
        return True
    if tolerance is None:
        return False
    delta = _relative_delta(left, right)
    return delta is not None and delta <= tolerance


def _relative_delta(left: Any, right: Any) -> Decimal | None:
    left_decimal, right_decimal = _as_decimal(left), _as_decimal(right)
    if left_decimal is None or right_decimal is None:
        return None
    if left_decimal == right_decimal:
        return Decimal(0)
    scale = max(abs(left_decimal), abs(right_decimal))
    if scale == 0:
        return Decimal(0)
    return abs(left_decimal - right_decimal) / scale


@dataclass
class GoldenSnapshot:
    """A committed expectation for compiled SQL, per metric and dialect."""

    metric_id: str
    dialect: Dialect
    sql: str

    @property
    def filename(self) -> str:
        return f"{self.metric_id}.{self.dialect.value}.sql"


def golden_snapshots(compiler: SemanticCompiler, request_for: Any = None) -> list[GoldenSnapshot]:
    """Compile every metric in both dialects for snapshot comparison.

    ``request_for`` may be a callable returning a :class:`MetricRequest` for a
    metric id; the default asks for the metric with no dimensions or filters,
    which is the stable shape a snapshot should pin.
    """
    snapshots: list[GoldenSnapshot] = []
    for metric_id in compiler.model.metric_ids():
        request = (
            request_for(metric_id)
            if callable(request_for)
            else MetricRequest(metrics=[metric_id], limit=100)
        )
        for dialect in Dialect:
            compiled: CompiledQuery = compiler.compile(request, dialect)
            snapshots.append(GoldenSnapshot(metric_id=metric_id, dialect=dialect, sql=compiled.sql))
    return snapshots
