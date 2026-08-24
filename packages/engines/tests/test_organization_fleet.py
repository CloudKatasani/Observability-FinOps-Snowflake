"""D10 against a real fleet: four accounts ingested, numbers checked to the cent.

Compiling is not evidence. These tests generate an organization, land every
account's ``ACCOUNT_USAGE`` extract and the organization account's
``ORGANIZATION_USAGE`` extract through the real ingest pipeline, and then check
the organization metrics against the fixture's own arithmetic — org spend
against the sum of the accounts, each account's share against 1, the effective
rate against the contracted rate the fixture priced with, and the commitment
runway against the drawdown it was built from.

The other half of what is checked here is that the account dimension did not
break the account-scoped facts. Every account in the fleet runs a warehouse
called TRANSFORM_WH at the same hours, so an entity that joins on the warehouse
name without the account multiplies one account's credits by the number of
accounts that share the name — silently, and only in a multi-account lake,
which is why it cannot be caught by the single-account fixture the rest of the
suite runs on.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from snowobs_engines.cache import ResultCache
from snowobs_engines.duckdb_engine import DuckDBEngine
from snowobs_fixtures.organization import (
    DEFAULT_ACCOUNT_PROFILES,
    GeneratedOrganization,
    OrganizationConfig,
    generate_organization,
    sum_column,
    write_organization_csv,
)
from snowobs_ingest.catalog import DuckDBCatalog
from snowobs_ingest.loader import IngestPipeline
from snowobs_semantics.compiler import MetricRequest, SemanticCompiler, TimeRange
from snowobs_semantics.dialect_shims import Dialect

ORGANIZATION_CONFIG = OrganizationConfig(
    days=14,
    accounts=tuple(replace(profile, queries_per_day=60) for profile in DEFAULT_ACCOUNT_PROFILES),
)
#: The whole generated window. The end is the day *after* the last generated
#: day, because the time filter compares an hourly entity's timestamp against a
#: date: an end of 2026-08-20 excludes every hour of 2026-08-20 after midnight,
#: which would silently drop a fourteenth of the credits being reconciled.
WINDOW = TimeRange(
    start=ORGANIZATION_CONFIG.start_date,
    end=ORGANIZATION_CONFIG.end_date + timedelta(days=1),
)

#: Shares are exact divisions rounded to the ratio type's fifteen places, so a
#: sum of four of them lands within a few units of the last place of 1.
ROUNDING = Decimal("0.000000001")


@pytest.fixture(scope="module")
def organization() -> GeneratedOrganization:
    return generate_organization(ORGANIZATION_CONFIG)


@pytest.fixture(scope="module")
def fleet(
    tmp_path_factory: pytest.TempPathFactory, organization: GeneratedOrganization
) -> Iterator[DuckDBEngine]:
    """The whole organization, landed through the real ingest pipeline."""
    extracts = write_organization_csv(organization, tmp_path_factory.mktemp("fleet-extract"))
    storage = tmp_path_factory.mktemp("fleet-lake")
    pipeline = IngestPipeline(storage)
    for account, directory in extracts.account_dirs.items():
        pipeline.ingest_directory(directory, account=account)
    pipeline.ingest_directory(extracts.organization_dir, account=organization.organization_name)

    with DuckDBCatalog(storage) as catalog:
        catalog.register_all()
        yield DuckDBEngine(catalog, cache=ResultCache())


@pytest.fixture(scope="module")
def compiler() -> SemanticCompiler:
    return SemanticCompiler()


def _total(engine: DuckDBEngine, compiler: SemanticCompiler, metric_id: str) -> Decimal | None:
    request = MetricRequest(metrics=[metric_id], time_range=WINDOW, bucket_time=False, limit=5000)
    value = engine.execute(compiler.compile(request, Dialect.DUCKDB)).scalar()
    assert value is None or isinstance(value, Decimal | int), metric_id
    return None if value is None else Decimal(str(value))


def _by(
    engine: DuckDBEngine,
    compiler: SemanticCompiler,
    metric_id: str,
    dimension: str = "account",
) -> dict[str, Decimal]:
    """One figure per value of a dimension, over the whole window."""
    request = MetricRequest(
        metrics=[metric_id],
        dimensions=[dimension],
        time_range=WINDOW,
        bucket_time=False,
        limit=5000,
    )
    result = engine.execute(compiler.compile(request, Dialect.DUCKDB))
    assert not result.truncated, metric_id
    key = result.columns.index(dimension.upper())
    value = result.columns.index(metric_id.replace(".", "_").upper())
    return {row[key]: Decimal(str(row[value])) for row in result.rows if row[value] is not None}


def _org_rows(organization: GeneratedOrganization, source_id: str) -> list[dict[str, Any]]:
    return organization.org_tables[source_id]


# ------------------------------------------------------------------- the money
def test_organization_spend_is_the_sum_of_its_accounts(
    fleet: DuckDBEngine, compiler: SemanticCompiler, organization: GeneratedOrganization
) -> None:
    """The control the whole domain rests on, to the cent (§27.7 — never a float)."""
    expected = sum_column(_org_rows(organization, "usage_in_currency_daily"), "USAGE_IN_CURRENCY")

    total = _total(fleet, compiler, "org.spend_currency")
    per_account = _by(fleet, compiler, "org.account_spend")

    assert total == expected
    assert sum(per_account.values(), Decimal(0)) == expected
    assert set(per_account) == set(organization.accounts)

    for account in organization.accounts:
        landed = sum_column(
            [
                row
                for row in _org_rows(organization, "usage_in_currency_daily")
                if row["ACCOUNT_NAME"] == account
            ],
            "USAGE_IN_CURRENCY",
        )
        assert per_account[account] == landed, account


def test_every_account_share_of_spend_adds_to_one(
    fleet: DuckDBEngine, compiler: SemanticCompiler, organization: GeneratedOrganization
) -> None:
    shares = _by(fleet, compiler, "org.account_spend_share")
    spend = _by(fleet, compiler, "org.account_spend")
    total = sum(spend.values(), Decimal(0))

    assert set(shares) == set(organization.accounts)
    assert all(0 < share < 1 for share in shares.values()), shares
    assert abs(sum(shares.values(), Decimal(0)) - 1) < ROUNDING

    for account, share in shares.items():
        assert abs(share - (spend[account] / total)) < ROUNDING, account


def test_egress_cost_is_charged_to_the_accounts_that_replicate(
    fleet: DuckDBEngine, compiler: SemanticCompiler, organization: GeneratedOrganization
) -> None:
    cost = _by(fleet, compiler, "org.egress_cost")
    bytes_out = _by(fleet, compiler, "org.data_transfer_bytes")
    expected = sum_column(
        [
            row
            for row in _org_rows(organization, "usage_in_currency_daily")
            if row["USAGE_TYPE"] == "data transfer"
        ],
        "USAGE_IN_CURRENCY",
    )

    assert sum(cost.values(), Decimal(0)) == expected
    assert expected > 0
    # The account replicating across a cloud boundary pays for it; an account
    # that egresses nothing is charged nothing, and that zero is a real zero.
    dearest = max(cost, key=lambda account: cost[account])
    assert cost[dearest] > 0
    assert bytes_out[dearest] > 0
    assert sum(bytes_out.values(), Decimal(0)) == sum_column(
        _org_rows(organization, "data_transfer_daily_history"), "BYTES_TRANSFERRED"
    )


# ---------------------------------------------------------------- the credits
def test_the_org_rollup_reconciles_to_the_accounts_own_metering(
    fleet: DuckDBEngine, compiler: SemanticCompiler, organization: GeneratedOrganization
) -> None:
    """R6 in miniature: two independent paths to one number must agree exactly.

    ``org.compute_credits_by_account`` comes from ORGANIZATION_USAGE;
    ``cost.by_warehouse_credits`` comes from each account's own
    ACCOUNT_USAGE. They are the same metering published twice, so any
    difference is a landing fault rather than a rounding one.
    """
    rollup = _by(fleet, compiler, "org.compute_credits_by_account")
    accounts = _by(fleet, compiler, "cost.by_warehouse_credits")

    assert set(rollup) == set(organization.accounts)
    for account in organization.accounts:
        expected = sum_column(
            organization.accounts[account].tables["warehouse_metering_history"],
            "CREDITS_USED_COMPUTE",
        )
        assert rollup[account] == expected, account
        assert accounts[account] == expected, account


def test_the_control_total_matches_the_landed_rollup(
    fleet: DuckDBEngine, compiler: SemanticCompiler, organization: GeneratedOrganization
) -> None:
    total = _total(fleet, compiler, "org.control_total_credits")
    assert total == sum_column(
        _org_rows(organization, "org_warehouse_metering_history"), "CREDITS_USED"
    )


def test_account_scoped_facts_do_not_fan_out_across_the_fleet(
    fleet: DuckDBEngine, compiler: SemanticCompiler, organization: GeneratedOrganization
) -> None:
    """Every account runs a TRANSFORM_WH; only the account keeps them apart.

    Without the account in the join and window keys of
    ``fact_warehouse_metering_hourly``, one account's metering row matches
    every account's attribution row for the same warehouse and hour, and the
    credits multiply by the size of the fleet.
    """
    per_account = _by(fleet, compiler, "cost.by_warehouse_credits")
    expected = {
        account: sum_column(generated.tables["warehouse_metering_history"], "CREDITS_USED_COMPUTE")
        for account, generated in organization.accounts.items()
    }
    assert per_account == expected

    # And the same figure asked for one account at a time.
    for account, credits in expected.items():
        request = MetricRequest(
            metrics=["cost.by_warehouse_credits"],
            time_range=WINDOW,
            bucket_time=False,
            account=account,
            limit=5000,
        )
        scoped = fleet.execute(compiler.compile(request, Dialect.DUCKDB)).scalar()
        assert scoped == credits, account


def test_storage_is_reported_per_account_and_for_the_fleet(
    fleet: DuckDBEngine, compiler: SemanticCompiler, organization: GeneratedOrganization
) -> None:
    rows = _org_rows(organization, "storage_daily_history")
    assert _total(fleet, compiler, "org.storage_bytes") == sum_column(rows, "AVERAGE_BYTES")
    assert _total(fleet, compiler, "org.storage_credits") == sum_column(rows, "CREDITS")

    per_account = _by(fleet, compiler, "org.storage_credits")
    assert set(per_account) == set(organization.accounts)
    assert all(credits > 0 for credits in per_account.values())


# ------------------------------------------------------------------ the rates
def test_the_effective_rate_is_the_rate_the_account_is_contracted_at(
    fleet: DuckDBEngine, compiler: SemanticCompiler
) -> None:
    """The metric that finds an account paying more for exactly the same credit."""
    rates = _by(fleet, compiler, "org.effective_credit_rate")
    expected = {profile.name: profile.credit_rate for profile in ORGANIZATION_CONFIG.accounts}

    assert set(rates) == set(expected)
    for account, rate in rates.items():
        assert rate.quantize(Decimal("0.0001")) == expected[account], account

    dearest = max(rates, key=lambda account: rates[account])
    cheapest = min(rates, key=lambda account: rates[account])
    assert rates[dearest] > rates[cheapest]


def test_the_rate_premium_finds_the_outlier_and_averages_to_one(
    fleet: DuckDBEngine, compiler: SemanticCompiler
) -> None:
    premium = _by(fleet, compiler, "org.rate_premium")
    rates = _by(fleet, compiler, "org.effective_credit_rate")
    mean = sum(rates.values(), Decimal(0)) / len(rates)

    for account, rate in rates.items():
        assert abs(premium[account] - (rate / mean)) < ROUNDING, account
    assert max(premium.values()) > 1
    assert min(premium.values()) < 1


# ------------------------------------------------------------- the commitment
def test_the_commitment_is_drawn_down_from_the_contracted_total(
    fleet: DuckDBEngine, compiler: SemanticCompiler, organization: GeneratedOrganization
) -> None:
    balances = _org_rows(organization, "remaining_balance_daily")
    last = balances[-1]
    remaining = (
        Decimal(str(last["FREE_USAGE_BALANCE"]))
        + Decimal(str(last["ROLLOVER_BALANCE"]))
        + Decimal(str(last["CAPACITY_BALANCE"]))
    )
    contracted = sum_column(_org_rows(organization, "contract_items"), "AMOUNT")

    assert _total(fleet, compiler, "org.contracted_amount") == contracted
    assert _total(fleet, compiler, "org.commitment_remaining") == remaining

    consumed_share = _total(fleet, compiler, "org.commitment_consumed_share")
    assert consumed_share is not None
    assert abs(consumed_share - ((contracted - remaining) / contracted)) < ROUNDING
    assert 0 < consumed_share < 1


def test_the_commitment_runway_is_a_sensible_number_of_days(
    fleet: DuckDBEngine, compiler: SemanticCompiler, organization: GeneratedOrganization
) -> None:
    """Balance at the end of the window over the mean daily drawdown within it."""
    balances = _org_rows(organization, "remaining_balance_daily")

    def remaining_on(row: dict[str, Any]) -> Decimal:
        return (
            Decimal(str(row["FREE_USAGE_BALANCE"]))
            + Decimal(str(row["ROLLOVER_BALANCE"]))
            + Decimal(str(row["CAPACITY_BALANCE"]))
        )

    first, last = remaining_on(balances[0]), remaining_on(balances[-1])
    draws = len(balances) - 1
    expected = last / ((first - last) / draws)

    runway = _total(fleet, compiler, "org.commitment_runway_days")
    assert runway is not None
    assert runway > 0
    assert abs(runway - expected) < Decimal("0.001")
    # The fixture's commitment is deliberately sized above the window's run
    # rate, so the contract strands rather than running out mid-term.
    assert runway > ORGANIZATION_CONFIG.days


def test_the_commitment_metrics_refuse_to_be_filtered_to_one_account(
    fleet: DuckDBEngine, compiler: SemanticCompiler
) -> None:
    """R3: an account-scoped request returns the organization figure unchanged.

    The service layer reports the metric as unavailable at account scope; what
    matters here is that the compiler never invents an account filter for a
    figure that has no account, which would return a wrong number rather than
    no number.
    """
    unscoped = _total(fleet, compiler, "org.commitment_remaining")
    request = MetricRequest(
        metrics=["org.commitment_remaining"],
        time_range=WINDOW,
        bucket_time=False,
        account="ACME_SANDBOX",
        limit=100,
    )
    assert fleet.execute(compiler.compile(request, Dialect.DUCKDB)).scalar() == unscoped
