"""Compiler behaviour: determinism, RLS, guards on identifiers, and errors."""

from __future__ import annotations

from datetime import date

import pytest

from snowobs_common.errors import ConfigurationError
from snowobs_semantics.compiler import (
    CompilationError,
    Filter,
    FilterOperator,
    MetricRequest,
    Order,
    SemanticCompiler,
    TimeRange,
)
from snowobs_semantics.dialect_shims import Dialect, ShimError, apply_shims
from snowobs_semantics.model import TimeGrain, default_model

WINDOW = TimeRange(start=date(2026, 8, 1), end=date(2026, 8, 20))


@pytest.fixture(scope="module")
def compiler() -> SemanticCompiler:
    return SemanticCompiler()


# ------------------------------------------------------------------- model
def test_model_loads_and_cross_validates() -> None:
    model = default_model()
    assert len(model.metrics) >= 39
    assert len(model.entities) >= 5
    for metric in model.metrics.values():
        assert metric.requires_sources
        assert metric.description, f"{metric.id} has no description"


def test_every_metric_declares_a_latency_floor_at_least_as_slow_as_its_sources() -> None:
    # R7 is validated at load time; this asserts the rule is actually in force.
    from snowobs_semantics.registry import default_registry

    registry = default_registry()
    for metric in default_model().metrics.values():
        slowest = max(registry.get(s).documented_latency_minutes for s in metric.requires_sources)
        assert metric.latency_floor_minutes >= slowest, metric.id


def test_every_metric_declares_the_sources_its_expression_actually_reads() -> None:
    """R7's other half: the declared source list must also be *complete*.

    A floor that is consistent with an incomplete declaration is still a false
    promise. ``fact_query_execution`` left-joins the 8-hour attribution view
    into the same row set as the 45-minute query history, so a metric summing
    ``CREDITS_ATTRIBUTED`` while declaring only ``query_history`` would pass
    the floor check and still tell a user their credit figure is three-quarters
    of an hour old.
    """
    from snowobs_semantics.model import _sources_behind_expression

    model = default_model()
    for metric in model.metrics.values():
        implied = _sources_behind_expression(metric, model.entity(metric.entity))
        assert implied <= set(metric.requires_sources), (
            f"{metric.id} reads {sorted(implied - set(metric.requires_sources))} "
            "without declaring it"
        )


def test_column_provenance_separates_a_view_s_fast_and_slow_sources() -> None:
    """The inference the check above depends on, asserted directly."""
    from snowobs_semantics.model import _sources_behind_expression

    model = default_model()
    entity = model.entity("fact_query_execution")

    class _Expression:
        def __init__(self, expression: str) -> None:
            self.expression = expression

    # A row count is final when query history lands; the credit column is not.
    assert _sources_behind_expression(_Expression("COUNT(*)"), entity) == set()
    assert _sources_behind_expression(_Expression("SUM(BYTES_SCANNED)"), entity) == {
        "query_history"
    }
    assert _sources_behind_expression(_Expression("SUM(CREDITS_ATTRIBUTED)"), entity) == {
        "query_attribution_history"
    }


def test_reported_columns_match_the_aliases_the_sql_emits(compiler: SemanticCompiler) -> None:
    """`columns` is a contract about the result set, so it must describe it.

    Dimensions were reported in the request's casing while the SQL aliased them
    uppercase, so a caller matching a returned column against this list matched
    nothing and had no error to go on.
    """
    import re

    compiled = compiler.compile(
        MetricRequest(
            metrics=["cost.by_warehouse_credits"],
            dimensions=["warehouse"],
            limit=5,
        ),
        Dialect.DUCKDB,
    )
    assert compiled.columns == ["TIME_BUCKET", "WAREHOUSE", "COST_BY_WAREHOUSE_CREDITS"]
    # Every reported column is genuinely an alias in the emitted statement.
    aliased = set(re.findall(r'AS "([A-Z_]+)"', compiled.sql))
    assert set(compiled.columns) <= aliased


def test_gating_sources_are_narrower_than_sources_used(compiler: SemanticCompiler) -> None:
    """A query that never selects the slow column must not be reported as slow."""
    compiled = compiler.compile(MetricRequest(metrics=["q.volume"]), Dialect.DUCKDB)
    # The entity view joins attribution, so the SQL genuinely reads it …
    assert "query_attribution_history" in compiled.sources_used
    # … but a count of queries is complete without it.
    assert compiled.gating_sources == ["query_history"]
    assert compiled.latency_floor_minutes == 45

    credits = compiler.compile(MetricRequest(metrics=["q.offender_credits"]), Dialect.DUCKDB)
    assert "query_attribution_history" in credits.gating_sources
    assert credits.latency_floor_minutes == 480


def test_unknown_metric_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="Unknown metric"):
        default_model().metric("cost.does_not_exist")


# --------------------------------------------------------------- compilation
def test_compilation_is_deterministic(compiler: SemanticCompiler) -> None:
    request = MetricRequest(metrics=["cost.total_credits"], dimensions=["service_type"])
    first = compiler.compile(request, Dialect.DUCKDB)
    second = compiler.compile(request, Dialect.DUCKDB)
    assert first.sql == second.sql
    assert first.fingerprint == second.fingerprint


def test_dialects_produce_different_sql_from_one_definition(
    compiler: SemanticCompiler,
) -> None:
    # The team dimension reads the query tag through the JSON_GET shim, which
    # renders as a different function per engine from one YAML definition.
    request = MetricRequest(metrics=["cost.by_team_credits"], dimensions=["team"])
    snowflake = compiler.compile(request, Dialect.SNOWFLAKE).sql.upper()
    duckdb = compiler.compile(request, Dialect.DUCKDB).sql.upper()
    assert snowflake != duckdb
    assert "JSON_EXTRACT_PATH_TEXT" in snowflake
    assert "JSON_EXTRACT_PATH_TEXT" not in duckdb
    assert "JSON_EXTRACT_STRING" in duckdb or "->>" in duckdb


def test_limit_is_always_applied(compiler: SemanticCompiler) -> None:
    compiled = compiler.compile(MetricRequest(metrics=["q.volume"], limit=25), Dialect.DUCKDB)
    assert "LIMIT 25" in compiled.sql
    assert compiled.limit == 25


def test_limit_is_bounded(compiler: SemanticCompiler) -> None:
    with pytest.raises(ValueError, match="limit must be"):
        MetricRequest(metrics=["q.volume"], limit=10_000_000)


def test_time_filter_is_injected_on_the_entity_time_column(
    compiler: SemanticCompiler,
) -> None:
    compiled = compiler.compile(
        MetricRequest(metrics=["cost.total_credits"], time_range=WINDOW), Dialect.DUCKDB
    )
    assert "2026-08-01" in compiled.sql
    assert "2026-08-20" in compiled.sql


def test_unknown_dimension_is_rejected_with_a_useful_message(
    compiler: SemanticCompiler,
) -> None:
    with pytest.raises(CompilationError, match="not available on entity"):
        compiler.compile(
            MetricRequest(metrics=["cost.total_credits"], dimensions=["nonsense"]),
            Dialect.DUCKDB,
        )


def test_ordering_is_applied(compiler: SemanticCompiler) -> None:
    compiled = compiler.compile(
        MetricRequest(
            metrics=["q.volume"],
            dimensions=["warehouse"],
            order=[Order(field="q.volume", descending=False)],
        ),
        Dialect.DUCKDB,
    )
    assert 'ORDER BY "Q_VOLUME" ASC' in compiled.sql


def test_coarsest_grain_wins_when_metrics_disagree(compiler: SemanticCompiler) -> None:
    # Aggregating a daily metric to hourly would invent precision.
    request = MetricRequest(
        metrics=["cost.by_warehouse_credits", "q.volume"], dimensions=["warehouse"]
    )
    compiled = compiler.compile(request, Dialect.DUCKDB)
    assert "'DAY'" in compiled.sql.upper()


def test_explicit_grain_overrides_the_default(compiler: SemanticCompiler) -> None:
    compiled = compiler.compile(
        MetricRequest(metrics=["q.volume"], grain=TimeGrain.MONTH), Dialect.DUCKDB
    )
    assert "'MONTH'" in compiled.sql.upper()


# ------------------------------------------------------------------ security
def test_rls_predicates_are_injected_and_cannot_be_removed_by_a_filter(
    compiler: SemanticCompiler,
) -> None:
    """§17: row-level security is applied server-side, never in the browser."""
    request = MetricRequest(
        metrics=["cost.by_team_credits"],
        dimensions=["team"],
        rls_filters=[Filter(dimension="team", operator=FilterOperator.IN, value=["TEAM_FINANCE"])],
        # A caller trying to widen their own scope:
        filters=[Filter(dimension="team", operator=FilterOperator.IN, value=["TEAM_OPS"])],
    )
    sql = compiler.compile(request, Dialect.DUCKDB).sql
    assert "TEAM_FINANCE" in sql
    # Both predicates are ANDed, so the caller's filter can only narrow further.
    assert sql.count("AND") >= 1
    assert "TEAM_OPS" in sql


def test_empty_rls_allowlist_selects_nothing_not_everything(
    compiler: SemanticCompiler,
) -> None:
    """The RLS failure mode that leaks another team's costs."""
    request = MetricRequest(
        metrics=["cost.by_team_credits"],
        rls_filters=[Filter(dimension="team", operator=FilterOperator.IN, value=[])],
    )
    sql = compiler.compile(request, Dialect.DUCKDB).sql.upper()
    assert "FALSE" in sql


def test_string_literals_are_escaped(compiler: SemanticCompiler) -> None:
    request = MetricRequest(
        metrics=["q.volume"],
        filters=[
            Filter(
                dimension="warehouse",
                operator=FilterOperator.EQ,
                value="WH'; DROP TABLE query_history; --",
            )
        ],
    )
    sql = compiler.compile(request, Dialect.DUCKDB).sql
    # The payload survives as *data* inside one quoted literal: its closing
    # quote was doubled, so it cannot terminate the string and start a new
    # statement. Parsing back proves it — the tree is a single SELECT whose
    # predicate compares against the payload as a literal value.
    import sqlglot
    from sqlglot import exp

    parsed = sqlglot.parse(sql, read="duckdb")
    assert len(parsed) == 1
    assert isinstance(parsed[0], exp.Select)
    literals = [node.this for node in parsed[0].find_all(exp.Literal) if node.is_string]
    assert "WH'; DROP TABLE query_history; --" in literals
    assert not list(parsed[0].find_all(exp.Drop))


def test_unsafe_identifiers_are_rejected() -> None:
    from snowobs_semantics.compiler import _quote

    with pytest.raises(CompilationError, match="Unsafe identifier"):
        _quote('team"; DROP TABLE x; --')


# ---------------------------------------------------------------- provenance
def test_compiled_query_carries_provenance(compiler: SemanticCompiler) -> None:
    compiled = compiler.compile(MetricRequest(metrics=["cost.attributed_credits"]), Dialect.DUCKDB)
    assert compiled.sources_used
    assert compiled.latency_floor_minutes == 480
    assert compiled.metrics == ["cost.attributed_credits"]
    assert compiled.entities_used == ["fact_warehouse_metering_hourly"]


# --------------------------------------------------------------------- shims
def test_no_shim_name_is_claimed_by_sqlglot() -> None:
    """A claimed name would bypass its shim silently — see SAFE_RATIO's history."""
    from snowobs_semantics.dialect_shims import assert_shim_names_are_unclaimed

    assert_shim_names_are_unclaimed()


def test_no_shim_rendering_is_transposed_when_re_parsed() -> None:
    """The other silent-bypass shape: a rendering the target dialect re-reads.

    ``DATE_DIFF_DAYS`` rendered ``DATE_DIFF('day', start, end)``, which
    SQLGlot's Snowflake reader took for the ``(end, start, unit)`` positional
    form and emitted as ``DATEDIFF(end, start, 'day')`` — a day count computed
    from the wrong pair of arguments, with nothing raised anywhere.
    """
    from snowobs_semantics.dialect_shims import assert_shim_renderings_survive_round_trip

    assert_shim_renderings_survive_round_trip()


def test_date_diff_days_counts_forwards_in_both_dialects() -> None:
    """The regression itself: a later date minus an earlier one is positive."""
    import duckdb

    sql = "SELECT DATE_DIFF_DAYS(TIMESTAMP '2026-01-01', TIMESTAMP '2026-01-08') AS d"
    for dialect in Dialect:
        rendered = apply_shims(sql, dialect)
        if dialect is Dialect.DUCKDB:
            assert duckdb.sql(rendered).fetchone() == (7,)
        else:
            # The Snowflake spelling cannot execute here, but the transposition
            # was plain in the text: the unit must lead and the earlier
            # timestamp must be the second argument, not the last.
            assert "DATEDIFF(DAY, CAST('2026-01-01' AS TIMESTAMPNTZ)" in rendered


def test_safe_ratio_is_fixed_point_and_null_safe_in_both_dialects() -> None:
    """§27.7 and R3 in one construct: never float, never a zero for unknown."""
    sql = "SELECT SAFE_RATIO(a, b) AS r FROM t"
    for dialect in Dialect:
        rendered = apply_shims(sql, dialect).upper().replace(" ", "")
        assert "CASEWHEN" in rendered
        assert "THENNULL" in rendered  # unknown ratio stays unknown, not 0
        assert "DECIMAL(38,15)" in rendered  # fixed point on both engines


def test_percentile_renders_differently_per_dialect() -> None:
    sql = "SELECT PERCENTILE(0.95, elapsed) AS p FROM t"
    snowflake = apply_shims(sql, Dialect.SNOWFLAKE).upper()
    duckdb = apply_shims(sql, Dialect.DUCKDB).upper()
    assert "APPROX_PERCENTILE" in snowflake
    assert "QUANTILE_CONT" in duckdb


def test_nested_shims_are_fully_rewritten() -> None:
    """The bug this guards: an outer shim stringifying an unrewritten inner one."""
    sql = "SELECT TS_TRUNC('hour', TS_PARSE(ts)) AS h FROM t"
    for dialect in Dialect:
        rendered = apply_shims(sql, dialect)
        assert "TS_PARSE" not in rendered.upper()
        assert "TS_TRUNC" not in rendered.upper()


def test_shim_arity_is_enforced() -> None:
    with pytest.raises(ShimError, match="takes"):
        apply_shims("SELECT SAFE_RATIO(a) FROM t", Dialect.DUCKDB)


def test_shim_name_inside_a_string_literal_is_not_rewritten() -> None:
    rendered = apply_shims("SELECT 'SAFE_RATIO(a, b)' AS note FROM t", Dialect.DUCKDB)
    assert "'SAFE_RATIO(a, b)'" in rendered


def test_unparseable_sql_raises_a_shim_error() -> None:
    with pytest.raises(ShimError, match="Could not parse"):
        apply_shims("SELECT FROM ((((", Dialect.DUCKDB)


# -------------------------------------------------------------------- docgen
def test_catalog_renders_every_metric() -> None:
    from snowobs_semantics.docgen import render_catalog

    catalog = render_catalog()
    for metric_id in default_model().metric_ids():
        assert f"`{metric_id}`" in catalog
    assert "Portability shims" in catalog
    assert "PARITY_EXCEPTIONS.md" in catalog
