"""The account dimension: every entity carries it, except where it would lie.

An enterprise runs a fleet of Snowflake accounts, so every figure the platform
states has to be answerable twice — once for the organization and once for one
account in it. That is only true if the dimension is on *every* entity: one
entity without it turns "filter the dashboard to ACME_APAC" into a page where
some tiles are filtered and some are not, with nothing on screen saying which.

The exceptions are the entities where an account genuinely does not exist. A
contract and its commitment balance belong to the organization; inventing an
account for them would put a label on a figure that the figure does not have.
The compiler skips the account predicate for those rather than mis-filtering
them (see ``SemanticCompiler._where``), and this file pins that behaviour.
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
from snowobs_semantics.dialect_shims import ACCOUNT_STAMP_COLUMN, Dialect
from snowobs_semantics.model import SemanticModel, default_model
from snowobs_semantics.registry import SourceScope, default_registry

WINDOW = TimeRange(start=date(2026, 8, 1), end=date(2026, 8, 20))

#: The entities that describe the organization rather than an account, and so
#: carry no account dimension at all. Listed here rather than derived, because
#: the whole point is that adding a fifteenth entity without an account must
#: fail this test and be argued for.
ORGANIZATION_ONLY_ENTITIES = {"dim_contract_item", "fact_commitment_balance_daily"}


@pytest.fixture(scope="module")
def model() -> SemanticModel:
    return default_model()


@pytest.fixture(scope="module")
def compiler(model: SemanticModel) -> SemanticCompiler:
    return SemanticCompiler(model)


# --------------------------------------------------------- every entity has it
def test_every_entity_exposes_the_account_dimension(model: SemanticModel) -> None:
    without = {
        entity.id
        for entity in model.entities.values()
        if entity.dimension(ACCOUNT_DIMENSION) is None
    }
    assert without == ORGANIZATION_ONLY_ENTITIES, (
        "every entity must be sliceable by account except the organization-only "
        f"ones; unexpected difference: {without ^ ORGANIZATION_ONLY_ENTITIES}"
    )


def test_the_account_dimension_always_resolves_to_the_same_column(model: SemanticModel) -> None:
    """One name, one column — an entity that spelt it differently would not join."""
    for entity in model.entities.values():
        dimension = entity.dimension(ACCOUNT_DIMENSION)
        if dimension is None:
            continue
        assert dimension.expression == "ACCOUNT_NAME", entity.id
        assert dimension.description, entity.id


def test_account_scoped_entities_read_the_stamp_and_organization_ones_read_the_column(
    model: SemanticModel,
) -> None:
    """Where the account comes from is decided by the source's scope, not by taste.

    An ``ACCOUNT_USAGE`` view has no account column — a real extract cannot,
    since it is taken from inside one account — so those entities call
    ``ACCOUNT_OF()``. An ``ORGANIZATION_USAGE`` view names the account in its
    own schema, and using the stamp there would replace four real account names
    with the single account the export was taken from.
    """
    registry = default_registry()
    for entity in model.entities.values():
        if entity.dimension(ACCOUNT_DIMENSION) is None:
            continue
        scopes = {registry.get(source).scope for source in entity.sources}
        if scopes == {SourceScope.ORGANIZATION}:
            assert "ACCOUNT_OF" not in entity.sql, (
                f"{entity.id} reads organization-scoped views, which carry a real "
                "ACCOUNT_NAME; the ingest stamp would overwrite it with the "
                "exporting account"
            )
            assert '"ACCOUNT_NAME"' in entity.sql, entity.id
        else:
            assert "ACCOUNT_OF" in entity.sql, (
                f"{entity.id} reads account-scoped views, which carry no account "
                "column; it must take the account from the ACCOUNT_OF shim"
            )


# ------------------------------------------------------------- filtering by it
@pytest.mark.parametrize("dialect", list(Dialect))
def test_an_account_filter_compiles_into_a_predicate(
    compiler: SemanticCompiler, dialect: Dialect
) -> None:
    """Both an account-scoped and an organization-scoped metric are narrowed."""
    for metric_id in ("cost.total_credits", "org.spend_currency"):
        request = MetricRequest(metrics=[metric_id], account="ACME_APAC", time_range=WINDOW)
        sql = compiler.compile(request, dialect).sql
        assert "ACCOUNT_NAME = 'ACME_APAC'" in sql, (metric_id, dialect)


def test_the_account_predicate_is_absent_when_no_account_is_asked_for(
    compiler: SemanticCompiler,
) -> None:
    sql = compiler.compile(
        MetricRequest(metrics=["cost.total_credits"], time_range=WINDOW), Dialect.DUCKDB
    ).sql
    assert "ACCOUNT_NAME =" not in sql


def test_an_organization_only_metric_is_never_filtered_by_account(
    compiler: SemanticCompiler,
) -> None:
    """R3: the honest answer is "this figure has no account", not a wrong number.

    Filtering a commitment balance by account would return either the whole
    organization's figure under an account's label, or nothing at all. The
    compiler compiles it unfiltered and the API reports it unavailable at
    account scope.
    """
    request = MetricRequest(metrics=["org.commitment_remaining"], account="ACME_APAC")
    sql = compiler.compile(request, Dialect.DUCKDB).sql
    assert "ACME_APAC" not in sql


def test_the_account_stamp_is_read_offline_and_the_connection_is_read_live(
    compiler: SemanticCompiler,
) -> None:
    """The same entity, two engines, two entirely different sources of truth."""
    request = MetricRequest(metrics=["cost.total_credits"], time_range=WINDOW)
    assert ACCOUNT_STAMP_COLUMN in compiler.compile(request, Dialect.DUCKDB).sql

    live = MetricRequest(
        metrics=["cost.total_credits"], time_range=WINDOW, account_context="ACME_PROD"
    )
    snowflake_sql = compiler.compile(live, Dialect.SNOWFLAKE).sql
    assert "'ACME_PROD'" in snowflake_sql
    assert ACCOUNT_STAMP_COLUMN not in snowflake_sql
    # Without a connected account LIVE cannot know, and says so with NULL
    # rather than attributing every row to one account.
    assert "NULL) AS ACCOUNT_NAME" in compiler.compile(request, Dialect.SNOWFLAKE).sql


# -------------------------------------------------------------- grouping by it
@pytest.mark.parametrize("dialect", list(Dialect))
def test_every_account_capable_metric_can_be_grouped_by_account(
    model: SemanticModel, compiler: SemanticCompiler, dialect: Dialect
) -> None:
    for metric in model.metrics.values():
        if model.entity(metric.entity).dimension(ACCOUNT_DIMENSION) is None:
            continue
        compiled = compiler.compile(
            MetricRequest(metrics=[metric.id], dimensions=[ACCOUNT_DIMENSION], time_range=WINDOW),
            dialect,
        )
        assert 'AS "ACCOUNT"' in compiled.sql, metric.id
        assert "ACCOUNT" in compiled.columns, metric.id


def test_grouping_and_filtering_by_account_compose(compiler: SemanticCompiler) -> None:
    compiled = compiler.compile(
        MetricRequest(
            metrics=["org.account_spend"],
            dimensions=[ACCOUNT_DIMENSION],
            account="ACME_SANDBOX",
            time_range=WINDOW,
        ),
        Dialect.DUCKDB,
    )
    assert "ACCOUNT_NAME = 'ACME_SANDBOX'" in compiled.sql
    assert compiled.sql.count("GROUP BY") == 1


def test_two_entities_join_on_the_account_they_share(compiler: SemanticCompiler) -> None:
    """The dimension is only worth having if it composes across facts.

    A cost figure from the account's own metering and the organization's
    roll-up of the same metering are the two sides of the reconciliation; they
    can only be put in one answer because both entities name the account the
    same way.
    """
    compiled = compiler.compile(
        MetricRequest(
            metrics=["cost.total_credits", "org.control_total_credits"],
            dimensions=[ACCOUNT_DIMENSION],
            time_range=WINDOW,
        ),
        Dialect.DUCKDB,
    )
    assert compiled.sql.startswith("WITH ")
    assert compiled.sql.count("GROUP BY") >= 2  # each fact aggregated before the join
    assert 'agg_0."ACCOUNT" = agg_1."ACCOUNT"' in compiled.sql
