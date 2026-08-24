"""Dual-engine parity — the suite that makes R1 enforceable.

A Snowflake account is not available in CI, so equivalence is established two
ways, both of which run on every commit:

1. **Executed parity.** The Snowflake-dialect SQL is transpiled back with
   SQLGlot and executed against the same DuckDB fixture data. If the two
   renderings of a metric disagree on the same rows, the numbers differ and the
   test fails. This catches a shim that means something different per engine.
2. **Golden SQL snapshots.** Both dialects' compiled SQL is pinned per metric,
   so an accidental change to a rendering is visible in review.

The live Snowflake comparison runs nightly behind ``pytest -m snowflake``
(§22.2.5); it is skipped by default and never gates a local run.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import sqlglot

from snowobs_engines.base import QueryResult
from snowobs_engines.duckdb_engine import DuckDBEngine
from snowobs_engines.parity import PARITY_EXCEPTIONS, compare_results
from snowobs_semantics.compiler import (
    Filter,
    FilterOperator,
    MetricRequest,
    Order,
    SemanticCompiler,
    TimeRange,
)
from snowobs_semantics.dialect_shims import Dialect
from snowobs_semantics.model import TimeGrain, default_model

WINDOW = TimeRange(start=date(2026, 7, 31), end=date(2026, 8, 20))


def _execute_snowflake_rendering(
    engine: DuckDBEngine, compiler: SemanticCompiler, request: MetricRequest
) -> QueryResult:
    """Execute the *Snowflake* rendering of a request against the fixture data.

    This is the crux of the offline parity check: the SQL under test is the one
    the Snowflake engine would actually send. Snowflake-specific functions are
    satisfied by the compatibility macros (see ``snowflake_compat``); syntax is
    normalised by SQLGlot without changing the expression tree.
    """
    from dataclasses import replace

    snowflake_query = compiler.compile(request, Dialect.SNOWFLAKE)
    executable = sqlglot.transpile(
        snowflake_query.sql, read="snowflake", write="duckdb", pretty=False
    )[0]
    duckdb_equivalent = compiler.compile(request, Dialect.DUCKDB)
    # Keep all provenance identical; only the SQL text under test differs. The
    # fingerprint must stay unique per request shape or the result cache serves
    # one request's rows for another.
    return engine.execute(
        replace(
            duckdb_equivalent,
            sql=executable,
            fingerprint=f"parity-{snowflake_query.fingerprint}",
        )
    )


def _requests_for(metric_id: str) -> list[MetricRequest]:
    """The shapes each metric is checked in: bare, and sliced by a dimension."""
    metric = default_model().metric(metric_id)
    requests = [MetricRequest(metrics=[metric_id], time_range=WINDOW, limit=500)]

    # Percentile metrics are compared on the whole population only. Snowflake
    # estimates percentiles from a t-digest, and that estimate is unstable on a
    # small group: a slice with a handful of queries can differ from the exact
    # value by more than any tolerance worth having, without the metric being
    # wrong. Comparing per-slice would therefore test sample size, not parity.
    # Recorded in docs/PARITY_EXCEPTIONS.md.
    if metric_id in PARITY_EXCEPTIONS:
        return requests

    sliceable = [d for d in metric.dimensions if not d.endswith(("_hour", "_day"))]
    if sliceable:
        requests.append(
            MetricRequest(
                metrics=[metric_id],
                dimensions=[sliceable[0]],
                time_range=WINDOW,
                limit=500,
            )
        )
    return requests


ALL_METRIC_IDS = default_model().metric_ids()


@pytest.mark.parametrize("metric_id", ALL_METRIC_IDS)
def test_every_metric_matches_across_engines(
    metric_id: str, engine: DuckDBEngine, compiler: SemanticCompiler
) -> None:
    """Both dialect renderings of every metric produce the same numbers."""
    for request in _requests_for(metric_id):
        native = engine.execute(compiler.compile(request, Dialect.DUCKDB))
        rendered = _execute_snowflake_rendering(engine, compiler, request)
        report = compare_results(native, rendered, [metric_id])
        assert report.matched, (
            f"{metric_id} diverged between engines ({report.summary}); "
            f"first difference: {report.differences[0] if report.differences else None}"
        )


@pytest.mark.parametrize("metric_id", ALL_METRIC_IDS)
def test_every_metric_compiles_deterministically_in_both_dialects(
    metric_id: str, compiler: SemanticCompiler
) -> None:
    """Same request → byte-identical SQL, which is what snapshots depend on."""
    request = MetricRequest(metrics=[metric_id], time_range=WINDOW, limit=100)
    for dialect in Dialect:
        first = compiler.compile(request, dialect)
        second = compiler.compile(request, dialect)
        assert first.sql == second.sql
        assert first.fingerprint == second.fingerprint


@pytest.mark.parametrize("metric_id", ALL_METRIC_IDS)
def test_every_metric_returns_a_result_on_fixture_data(
    metric_id: str, engine: DuckDBEngine, compiler: SemanticCompiler
) -> None:
    """A metric that cannot execute is not shippable, whatever its SQL looks like."""
    request = MetricRequest(metrics=[metric_id], time_range=WINDOW, limit=100)
    result = engine.execute(compiler.compile(request, Dialect.DUCKDB))
    assert result.columns
    assert result.latency_floor_minutes >= 0
    assert result.sources


def test_parity_exceptions_are_documented() -> None:
    """Every tolerance must name a metric that exists and give a reason (§22.2.4)."""
    known = set(default_model().metric_ids())
    for metric_id, (tolerance, reason) in PARITY_EXCEPTIONS.items():
        assert metric_id in known, f"tolerance declared for unknown metric {metric_id}"
        assert tolerance > 0
        assert len(reason) > 20, f"{metric_id} tolerance needs a real justification"


def test_metrics_without_a_declared_tolerance_match_exactly(
    engine: DuckDBEngine, compiler: SemanticCompiler
) -> None:
    """Spot-check the strictest case: money must agree to the last digit."""
    request = MetricRequest(
        metrics=["cost.billed_credits", "cost.total_credits"],
        dimensions=["service_type"],
        time_range=WINDOW,
        limit=500,
    )
    native = engine.execute(compiler.compile(request, Dialect.DUCKDB))
    rendered = _execute_snowflake_rendering(engine, compiler, request)
    report = compare_results(native, rendered, request.metrics)
    assert report.tolerance_applied is None
    assert report.matched
    assert report.rows_compared > 0


# ------------------------------------------------------------- fan-out safety
def test_mixing_facts_at_different_grains_does_not_double_count(
    engine: DuckDBEngine, compiler: SemanticCompiler
) -> None:
    """The classic silent-doubling bug the compiler exists to prevent (§8.3)."""
    # Both requests are pinned to the same grain and a limit that cannot
    # truncate either result — otherwise the comparison would measure
    # truncation rather than double-counting.
    warehouse_only = MetricRequest(
        metrics=["cost.by_warehouse_credits"],
        dimensions=["warehouse"],
        grain=TimeGrain.DAY,
        time_range=WINDOW,
        limit=5000,
    )
    mixed = MetricRequest(
        metrics=["cost.by_warehouse_credits", "q.volume"],
        dimensions=["warehouse"],
        grain=TimeGrain.DAY,
        time_range=WINDOW,
        limit=5000,
    )

    alone = engine.execute(compiler.compile(warehouse_only, Dialect.DUCKDB))
    combined = engine.execute(compiler.compile(mixed, Dialect.DUCKDB))
    assert not alone.truncated and not combined.truncated

    def totals(result: QueryResult) -> dict[str, Decimal]:
        # Totals per warehouse across every bucket: the two requests resolve to
        # different time grains, so only the totals are comparable — and the
        # totals are exactly what a double-counting join would inflate.
        warehouse_index = result.columns.index("WAREHOUSE")
        credit_index = result.columns.index("COST_BY_WAREHOUSE_CREDITS")
        summed: dict[str, Decimal] = {}
        for row in result.rows:
            warehouse, credits = row[warehouse_index], row[credit_index]
            if warehouse is None or credits is None:
                continue
            summed[warehouse] = summed.get(warehouse, Decimal(0)) + credits
        return summed

    alone_totals, combined_totals = totals(alone), totals(combined)
    assert combined_totals, "the mixed request returned no rows"
    for warehouse, credits in combined_totals.items():
        assert credits == alone_totals[warehouse], (
            f"{warehouse}: credits changed from {alone_totals[warehouse]} to {credits} "
            f"merely by requesting another metric alongside it"
        )


def test_multi_fact_request_produces_independent_aggregate_ctes(
    compiler: SemanticCompiler,
) -> None:
    request = MetricRequest(
        metrics=["cost.by_warehouse_credits", "q.volume"],
        dimensions=["warehouse"],
        time_range=WINDOW,
    )
    sql = compiler.compile(request, Dialect.DUCKDB).sql.upper()
    assert sql.startswith("WITH ")
    assert sql.count("GROUP BY") >= 2  # each fact aggregated before the join


# ---------------------------------------------------------------- provenance
def test_results_carry_freshness_and_sources(
    engine: DuckDBEngine, compiler: SemanticCompiler
) -> None:
    """R5/R7: no figure travels without its provenance."""
    request = MetricRequest(metrics=["cost.attributed_credits"], time_range=WINDOW)
    result = engine.execute(compiler.compile(request, Dialect.DUCKDB))
    assert "query_attribution_history" in result.sources
    # The floor is the slowest source: QUERY_ATTRIBUTION_HISTORY at 8 hours.
    assert result.latency_floor_minutes == 480
    assert result.as_of is not None
    assert result.executed_sql


def test_currency_metric_is_flagged_provisional_inside_the_restatement_window(
    compiler: SemanticCompiler,
) -> None:
    recent = MetricRequest(
        metrics=["cost.spend_usd"],
        time_range=TimeRange(start=date(2026, 8, 1), end=date(2026, 8, 20)),
    )
    assert compiler.compile(recent, Dialect.DUCKDB).provisional is True

    settled = MetricRequest(
        metrics=["cost.spend_usd"],
        time_range=TimeRange(start=date(2026, 1, 1), end=date(2026, 1, 31)),
    )
    # Well outside the window, the figure is final.
    assert compiler.compile(settled, Dialect.DUCKDB).provisional is False


# ------------------------------------------------------------------ numbers
def test_credits_come_back_as_decimal_not_float(
    engine: DuckDBEngine, compiler: SemanticCompiler
) -> None:
    request = MetricRequest(metrics=["cost.billed_credits"], time_range=WINDOW)
    result = engine.execute(compiler.compile(request, Dialect.DUCKDB))
    value = result.scalar()
    assert isinstance(value, Decimal), f"got {type(value).__name__}"


def test_safe_divide_returns_null_not_zero_for_an_empty_slice(
    engine: DuckDBEngine, compiler: SemanticCompiler
) -> None:
    """R3: an unknown ratio is unknown, never 0%."""
    request = MetricRequest(
        metrics=["q.failure_rate"],
        filters=[Filter(dimension="warehouse", operator=FilterOperator.EQ, value="NO_SUCH_WH")],
        time_range=WINDOW,
    )
    result = engine.execute(compiler.compile(request, Dialect.DUCKDB))
    assert result.scalar() is None


def test_ordering_and_limit_are_applied(engine: DuckDBEngine, compiler: SemanticCompiler) -> None:
    request = MetricRequest(
        metrics=["q.offender_credits"],
        dimensions=["fingerprint"],
        time_range=WINDOW,
        order=[Order(field="q.offender_credits", descending=True)],
        limit=5,
    )
    result = engine.execute(compiler.compile(request, Dialect.DUCKDB))
    assert len(result.rows) <= 5
    values = [row[-1] for row in result.rows if row[-1] is not None]
    assert values == sorted(values, reverse=True)
