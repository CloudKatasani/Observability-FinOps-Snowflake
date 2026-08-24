"""Keyword→metric routing (BUILD_PROMPT §19).

Routing is what a deployment with no LLM key actually runs on, so it is held to
real questions rather than to the phrasings the catalogue happens to use. Each
test below corresponds to a way the ranking was wrong in practice.
"""

from __future__ import annotations

from datetime import date

import pytest

from snowobs_agents.runtime.routing import (
    comparison_windows,
    is_causal,
    route,
)
from snowobs_semantics.model import SemanticModel, default_model

TODAY = date(2026, 8, 24)


@pytest.fixture(scope="module")
def model() -> SemanticModel:
    return default_model()


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What were our total billed credits?", "cost.billed_credits"),
        ("How much spend is untagged?", "cost.unattributed_share"),
        ("Which warehouse costs the most?", "cost.by_warehouse_credits"),
        ("Credits by team this month", "cost.by_team_credits"),
        ("Which warehouses have a long auto-suspend?", "wh.autosuspend_seconds"),
        ("Which warehouses are queueing?", "wh.queue_overload_pct"),
        ("How many queries spill?", "wh.spill_query_share"),
        ("How many tokens have we used?", "ai.total_tokens"),
        # A generic question is about the bill, not about whichever domain
        # happens to own the shortest metric id. "Total credits" and "Cortex
        # credits" both contain the word "total" somewhere, and this one used
        # to be answered with the Cortex figure.
        ("How many credits did we consume in total?", "cost.total_credits"),
        # The word appears in three metrics' identities, but only one of them
        # is *named* for it — the others are describing what their credits are
        # attributed to.
        ("How many queries ran?", "q.volume"),
        # Nothing here narrows the subject beyond "spend", so a Cortex model
        # breakdown is the wrong shape of answer as well as the wrong subject.
        ("Why did spend change week over week?", "cost.spend_usd"),
    ],
)
def test_ordinary_questions_reach_the_metric_that_answers_them(
    model: SemanticModel, question: str, expected: str
) -> None:
    routed = route(question, model)
    assert routed is not None, f"nothing matched {question!r}"
    assert routed.metric_id == expected


def test_a_question_the_catalogue_cannot_answer_matches_nothing(
    model: SemanticModel,
) -> None:
    """Returning None is the honest outcome; guessing a metric is not."""
    assert route("what is the weather in Oslo", model) is None
    assert route("book me a flight to Berlin", model) is None


def test_a_single_word_synonym_does_not_match_inside_another_word(
    model: SemanticModel,
) -> None:
    """The bug: the synonym "spend" scored a phrase match on "auto-suspend"."""
    routed = route("Which warehouses have a long auto-suspend?", model)
    assert routed is not None
    assert routed.metric_id != "cost.spend_usd"


def test_a_snake_case_metric_name_is_matched_word_by_word(model: SemanticModel) -> None:
    """The bug: `total_tokens` was one opaque word, so "tokens" never matched."""
    routed = route("How many tokens have we used?", model)
    assert routed is not None
    assert routed.metric_id == "ai.total_tokens"


def test_a_distinctive_word_outweighs_a_common_one(model: SemanticModel) -> None:
    """The bug: "idle credits by warehouse" answered with credits-by-warehouse.

    Nearly every cost metric is named with "credits"; "idle" narrows it to a
    handful. Weighting them equally let the metric that matched the common
    words and dropped the qualifying one win.
    """
    routed = route("What are idle credits by warehouse?", model)
    assert routed is not None
    assert routed.metric_id == "cost.idle_credits"
    # The dimension is still picked up — as a slice, which is what it is.
    assert routed.dimensions == ["warehouse"]


def test_the_period_in_the_question_is_honoured(model: SemanticModel) -> None:
    assert route("billed credits last 7 days", model).last_days == 7
    assert route("billed credits last 90 days", model).last_days == 90
    assert route("billed credits", model).last_days == 30  # a stated default


# ------------------------------------------------------------ comparisons
@pytest.mark.parametrize(
    "question",
    [
        "Why did spend increase between July and August?",
        "What drove the change in credits by team last month versus the month before?",
        "Why did our query volume change week over week?",
        "Which warehouse is responsible for the cost increase?",
        "Compare billed credits for the last 7 days against the 7 before",
        "Is our failure rate better or worse than last week?",
    ],
)
def test_a_question_about_change_is_recognised_as_causal(question: str) -> None:
    assert is_causal(question)


@pytest.mark.parametrize(
    "question",
    [
        "What is attributed compute versus idle?",
        "What were our total billed credits last 30 days?",
        "Which warehouse costs the most?",
        "How many queries ran?",
    ],
)
def test_a_question_about_composition_is_not_treated_as_causal(question: str) -> None:
    """ "Attributed compute versus idle" compares two components of one total.

    It contains "versus" but names no period, and answering it with a
    period-over-period decomposition would answer a question nobody asked.
    """
    assert not is_causal(question)


def test_named_months_become_those_calendar_months() -> None:
    windows = comparison_windows("Why did spend increase between July and August?", today=TODAY)
    assert windows is not None
    assert (windows.period_a_start, windows.period_a_end) == (date(2026, 7, 1), date(2026, 7, 31))
    assert windows.period_b_start == date(2026, 8, 1)
    # The current month is truncated at today rather than running into the future.
    assert windows.period_b_end == TODAY


def test_week_over_week_compares_two_equal_seven_day_windows() -> None:
    windows = comparison_windows("Why did our query volume change week over week?", today=TODAY)
    assert windows is not None
    assert (windows.period_b_start, windows.period_b_end) == (date(2026, 8, 18), TODAY)
    assert (windows.period_a_start, windows.period_a_end) == (
        date(2026, 8, 11),
        date(2026, 8, 17),
    )


def test_an_unstated_period_gets_a_default_that_is_named_in_the_answer() -> None:
    """A silent default is the thing to avoid, not a default."""
    windows = comparison_windows(
        "Which warehouse is responsible for the cost increase?", today=TODAY
    )
    assert windows is not None
    assert (windows.period_b_end - windows.period_b_start).days == 29
    assert (windows.period_a_end - windows.period_a_start).days == 29
    assert windows.period_a_end < windows.period_b_start
    assert "30 days" in windows.basis
    assert str(windows.period_a_start) in windows.basis


def test_a_question_that_is_not_a_comparison_yields_no_windows() -> None:
    assert comparison_windows("What were our billed credits?", today=TODAY) is None


def test_a_breakdown_does_not_answer_a_question_that_asked_for_no_breakdown(
    model: SemanticModel,
) -> None:
    """ "What is it" and "how is it distributed" are different questions.

    A slice is only the right answer when the question asks how the figure
    splits. Without that, a metric that splits by model, team, or warehouse is
    answering something that was not asked.
    """
    plain = route("what did we spend", model)
    assert plain is not None
    assert "by_" not in plain.metric_id

    sliced = route("what did we spend by team", model)
    assert sliced is not None
    assert sliced.metric_id == "cost.by_team_credits"
    assert sliced.dimensions == ["team"]
