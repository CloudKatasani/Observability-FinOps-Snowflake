"""D10: the organization domain compiles, declares itself honestly, and stays slow.

The organization views are the slowest and most restated data the platform
reads, and they are the only ones that can name another account. Both of those
are properties a metric can quietly lose in an edit, so they are pinned here:
the value tests that execute these metrics against an ingested fleet live in
``packages/engines/tests/test_organization_fleet.py``.
"""

from __future__ import annotations

from datetime import date

import pytest

from snowobs_semantics.compiler import (
    ACCOUNT_DIMENSION,
    MetricRequest,
    SemanticCompiler,
    TimeRange,
)
from snowobs_semantics.dialect_shims import SHIMS, Dialect
from snowobs_semantics.model import Metric, SemanticModel, default_model
from snowobs_semantics.registry import SourceScope, default_registry

WINDOW = TimeRange(start=date(2026, 8, 1), end=date(2026, 8, 20))

DOMAIN = "organization"

#: The metrics that answer a question no single account can, and therefore the
#: reason the domain exists. Named individually because "16 metrics exist" is
#: not the requirement — these particular answers are.
REQUIRED_METRICS = {
    "org.spend_currency",
    "org.account_spend",
    "org.account_spend_share",
    "org.egress_cost",
    "org.control_total_credits",
    "org.compute_credits_by_account",
    "org.cloud_services_credits_by_account",
    "org.storage_bytes",
    "org.storage_credits",
    "org.data_transfer_bytes",
    "org.effective_credit_rate",
    "org.rate_premium",
    "org.contracted_amount",
    "org.commitment_remaining",
    "org.commitment_consumed_share",
    "org.commitment_runway_days",
}


@pytest.fixture(scope="module")
def model() -> SemanticModel:
    return default_model()


@pytest.fixture(scope="module")
def compiler(model: SemanticModel) -> SemanticCompiler:
    return SemanticCompiler(model)


@pytest.fixture(scope="module")
def organization_metrics(model: SemanticModel) -> list[Metric]:
    return model.metrics_for_domain(DOMAIN)


def test_the_domain_ships_the_metrics_it_was_defined_for(
    organization_metrics: list[Metric],
) -> None:
    assert {metric.id for metric in organization_metrics} == REQUIRED_METRICS


@pytest.mark.parametrize("dialect", list(Dialect))
def test_every_organization_metric_compiles_in_both_dialects(
    compiler: SemanticCompiler, organization_metrics: list[Metric], dialect: Dialect
) -> None:
    for metric in organization_metrics:
        compiled = compiler.compile(
            MetricRequest(metrics=[metric.id], time_range=WINDOW, limit=100), dialect
        )
        assert compiled.sql
        assert compiled.dialect is dialect
        # R1: nothing dialect-specific survives compilation.
        assert not any(f"{name}(" in compiled.sql for name in SHIMS), metric.id


@pytest.mark.parametrize("dialect", list(Dialect))
def test_every_organization_metric_compiles_sliced_by_each_of_its_dimensions(
    compiler: SemanticCompiler, organization_metrics: list[Metric], dialect: Dialect
) -> None:
    """A declared dimension that cannot be compiled is a broken promise to the UI."""
    for metric in organization_metrics:
        for dimension in metric.dimensions:
            compiled = compiler.compile(
                MetricRequest(metrics=[metric.id], dimensions=[dimension], time_range=WINDOW),
                dialect,
            )
            assert dimension.upper() in compiled.columns, (metric.id, dimension)


def test_every_organization_metric_reads_only_organization_scoped_sources(
    model: SemanticModel, organization_metrics: list[Metric]
) -> None:
    """The domain's premise: these figures come from the fleet-wide export.

    A D10 metric reading an ACCOUNT_USAGE view would be answerable for one
    account only, and would report an organization total that silently meant
    "the accounts whose extracts happen to be landed".
    """
    registry = default_registry()
    for metric in organization_metrics:
        for source_id in {*metric.requires_sources, *model.entity(metric.entity).sources}:
            assert registry.get(source_id).scope is SourceScope.ORGANIZATION, (
                metric.id,
                source_id,
            )


def test_no_organization_metric_claims_to_be_fresher_than_a_day(
    organization_metrics: list[Metric],
) -> None:
    """R7. ORGANIZATION_USAGE is a daily export; the fastest of these views is 24 h.

    A tile fed from here must never sit next to an account-level tile implying
    the same freshness, so the floor is asserted rather than trusted.
    """
    for metric in organization_metrics:
        assert metric.latency_floor_minutes >= 1440, metric.id


def test_the_restating_metrics_declare_a_provisional_window(
    model: SemanticModel, organization_metrics: list[Metric]
) -> None:
    """Spend and balances restate until month close; saying so is not optional (§9.3)."""
    restating = {"usage_in_currency_daily", "remaining_balance_daily"}
    for metric in organization_metrics:
        if restating & set(metric.requires_sources):
            assert metric.provisional_window_days >= 35, metric.id
            assert metric.latency_floor_minutes >= 4320, metric.id


def test_money_and_credit_metrics_never_use_an_averaging_aggregate(
    organization_metrics: list[Metric],
) -> None:
    """§27.7: AVG over a fixed-point column returns floating point on DuckDB.

    Every mean in this domain is therefore written as a ratio of sums through
    SAFE_RATIO, which casts back to fixed point on both engines.
    """
    for metric in organization_metrics:
        assert "AVG(" not in metric.expression.upper(), metric.id


def test_the_commitment_metrics_are_organization_only(
    model: SemanticModel, organization_metrics: list[Metric]
) -> None:
    """A contract has no account, and the catalogue must not pretend otherwise."""
    commitment = [m for m in organization_metrics if m.id.startswith("org.commitment")]
    assert commitment
    for metric in [*commitment, model.metric("org.contracted_amount")]:
        entity = model.entity(metric.entity)
        assert entity.dimension(ACCOUNT_DIMENSION) is None, metric.id
        assert ACCOUNT_DIMENSION not in metric.dimensions, metric.id


def test_the_per_account_metrics_are_all_sliceable_by_account(
    model: SemanticModel, organization_metrics: list[Metric]
) -> None:
    per_account = [
        m for m in organization_metrics if not m.id.startswith(("org.commitment", "org.contracted"))
    ]
    for metric in per_account:
        assert ACCOUNT_DIMENSION in metric.dimensions, metric.id
        assert model.entity(metric.entity).dimension(ACCOUNT_DIMENSION) is not None, metric.id


def test_the_share_metrics_divide_by_the_whole_answer_not_by_the_row(
    model: SemanticModel,
) -> None:
    """Their denominators are windows, which is what makes the shares add to 1."""
    for metric_id in ("org.account_spend_share", "org.rate_premium"):
        expression = " ".join(model.metric(metric_id).expression.split())
        assert "OVER ()" in expression, metric_id
        assert "SAFE_RATIO" in expression, metric_id


def test_every_organization_metric_documents_when_it_misleads(
    organization_metrics: list[Metric],
) -> None:
    """A number this slow and this restated needs its caveat written down (R3, R7)."""
    for metric in organization_metrics:
        assert len(metric.description.split()) >= 40, metric.id
        assert metric.synonyms, metric.id
        assert metric.verified_queries, metric.id
        assert metric.owner
