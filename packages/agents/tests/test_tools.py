"""The tool registry (BUILD_PROMPT §12.3), exercised against a real account.

The design rule these tests hold to: the agent chooses *metrics*, never SQL.
Everything that reaches the engine goes through the governed layer, and the one
escape hatch is role-gated and off unless a deployment turns it on.
"""

from __future__ import annotations

import json

from snowobs_agents.runtime.tools import ToolContext, build_registry, specs_for


def call(context: ToolContext, name: str, **arguments: object) -> object:
    return build_registry()[name].run(context, dict(arguments))


# ------------------------------------------------------------- query_metric
def test_query_metric_returns_figures_with_their_provenance(context: ToolContext) -> None:
    outcome = call(context, "query_metric", metrics=["cost.billed_credits"], last_days=30)
    assert not outcome.is_error
    payload = json.loads(outcome.content)
    assert payload["rows"]
    # R5/R7: a figure never travels without where it came from and how fresh it is.
    assert outcome.sql and "SELECT" in outcome.sql.upper()
    assert outcome.sources == ["metering_daily_history"]
    assert outcome.latency_floor_minutes == 180
    assert outcome.metrics == ["cost.billed_credits"]
    # §27.7: credits cross the tool boundary as strings, never as floats.
    value = payload["rows"][0]["COST_BILLED_CREDITS"]
    assert isinstance(value, str)


def test_query_metric_slices_by_a_governed_dimension(context: ToolContext) -> None:
    outcome = call(
        context,
        "query_metric",
        metrics=["cost.by_warehouse_credits"],
        dimensions=["warehouse"],
        last_days=30,
        by_time=False,
    )
    payload = json.loads(outcome.content)
    assert payload["rows"]
    assert "WAREHOUSE" in payload["rows"][0]


def test_an_unknown_metric_suggests_real_ones_instead_of_failing_blankly(
    context: ToolContext,
) -> None:
    """A misnamed metric is the most common agent mistake; it must be recoverable."""
    outcome = call(context, "query_metric", metrics=["cost.total_spend_dollars"])
    assert outcome.is_error
    assert "Unknown metric" in outcome.content
    suggestion_line = outcome.content.split("Closest matches:")[1]
    assert any(name in suggestion_line for name in context.model.metrics)


def test_calling_query_metric_with_no_metric_says_what_to_do_next(
    context: ToolContext,
) -> None:
    outcome = call(context, "query_metric", metrics=[])
    assert outcome.is_error
    assert "list_metrics" in outcome.content or "describe_metric" in outcome.content


def test_row_count_reaching_the_agent_is_capped(context: ToolContext) -> None:
    """Context is a budget: an agent asking for 100k rows gets a workable slice."""
    outcome = call(
        context,
        "query_metric",
        metrics=["q.volume"],
        dimensions=["user"],
        limit=100_000,
        last_days=30,
        by_time=False,
    )
    payload = json.loads(outcome.content)
    assert len(payload["rows"]) <= 200


# ------------------------------------------------- catalogue-facing tools
def test_list_metrics_describes_the_catalogue(context: ToolContext) -> None:
    outcome = call(context, "list_metrics")
    assert not outcome.is_error
    assert "cost." in outcome.content


def test_describe_metric_explains_a_metric_before_it_is_run(context: ToolContext) -> None:
    outcome = call(context, "describe_metric", metric_id="cost.billed_credits")
    assert not outcome.is_error
    lowered = outcome.content.lower()
    assert "credit" in lowered
    # R7: the description carries the latency honestly.
    assert "180" in outcome.content


def test_get_coverage_reports_what_is_answerable(context: ToolContext) -> None:
    outcome = call(context, "get_coverage")
    assert not outcome.is_error
    assert "metering_daily_history" in outcome.content


# ------------------------------------------------------------ the escape hatch
def test_adhoc_sql_is_refused_when_the_deployment_disables_it(context: ToolContext) -> None:
    outcome = call(context, "run_sql_guarded", sql="SELECT 1")
    assert outcome.is_error
    assert "query_metric" in outcome.content  # points at the governed route


def test_adhoc_sql_is_not_even_offered_to_a_caller_without_the_role(
    context: ToolContext,
) -> None:
    """Role gating happens at the schema level, so the model never sees the tool."""
    offered = {spec.name for spec in specs_for(build_registry(), context.roles)}
    assert "run_sql_guarded" not in offered
    assert "query_metric" in offered

    admin_offered = {
        spec.name for spec in specs_for(build_registry(), frozenset({"platform_admin"}))
    }
    assert "run_sql_guarded" in admin_offered


def test_adhoc_sql_still_goes_through_the_guard_for_an_admin(
    admin_context: ToolContext,
) -> None:
    """R9: enabling the hatch does not disable the guard behind it."""
    rejected = call(admin_context, "run_sql_guarded", sql="DROP TABLE QUERY_HISTORY")
    assert rejected.is_error
    assert "guard" in rejected.content.lower()

    allowed = call(
        admin_context, "run_sql_guarded", sql="SELECT COUNT(*) FROM metering_daily_history"
    )
    assert not allowed.is_error, allowed.content
    assert json.loads(allowed.content)["rows"]
