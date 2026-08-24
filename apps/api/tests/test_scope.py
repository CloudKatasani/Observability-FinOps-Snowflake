"""Viewing the catalogue at organization or account scope (§9, R3).

The filter is only useful if it is honest about what each scope can answer.
Three things can go wrong, and all three are silent:

* an organization figure presented as an account's, because the metric could
  not be narrowed and the platform widened the question instead;
* an organization roll-up over the accounts that happen to be landed,
  presented as the whole organization;
* the organization itself offered as though it were one of its accounts.

Each has a test here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from snowobs_api.main import create_app
from snowobs_api.services.metrics import MetricService
from snowobs_api.services.scope import Scope, ScopeRequest, assess
from snowobs_common.config import Settings
from snowobs_fixtures.organization import (
    OrganizationConfig,
    generate_organization,
    write_organization_csv,
)
from snowobs_ingest.loader import IngestPipeline
from snowobs_semantics.model import default_model
from snowobs_semantics.registry import default_registry

ORG = OrganizationConfig(days=7)


@pytest.fixture(scope="module")
def organization_lake(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, str]]:
    """A lake holding four accounts' extracts plus the organization's own."""
    lake: Path = tmp_path_factory.mktemp("org-lake")
    extract: Path = tmp_path_factory.mktemp("org-extract")
    organization = generate_organization(ORG)
    layout = write_organization_csv(organization, extract)

    pipeline = IngestPipeline(lake)
    for account, directory in layout.account_dirs.items():
        pipeline.ingest_directory(directory, account=account)
    pipeline.ingest_directory(layout.organization_dir, account=organization.organization_name)
    yield lake, organization.organization_name


@pytest.fixture(scope="module")
def settings(organization_lake: tuple[Path, str]) -> Settings:
    lake, _organization = organization_lake
    return Settings(
        _env_file=None,
        storage={"provider": "local", "bucket": str(lake)},
        finops={"credit_price_usd": "3.00"},
    )


@asynccontextmanager
async def client_for(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


# ─────────────────────────────────────────────────────── what can be selected
def test_the_organization_is_not_offered_as_one_of_its_accounts(
    settings: Settings, organization_lake: tuple[Path, str]
) -> None:
    """`ORGANIZATION_USAGE` is exported once, from whichever account holds the
    grant, so the organization's name is stamped on those rows too. Offering it
    beside its four accounts would invite a per-account view of something with
    no per-account meaning.
    """
    _lake, organization = organization_lake
    accounts = MetricService(settings).landed_accounts()

    assert organization not in accounts
    assert accounts == ["ACME_ANALYTICS", "ACME_APAC", "ACME_PROD", "ACME_SANDBOX"]


@pytest.mark.asyncio
async def test_the_scope_selector_says_how_much_each_scope_can_answer(
    settings: Settings,
) -> None:
    async with client_for(settings) as client:
        body = (await client.get("/api/v1/metrics/scopes")).json()

    assert body["mode"] == "offline"
    options = {option["label"]: option for option in body["options"]}
    assert "Organization" in options
    assert options["Organization"]["scope"] == "organization"

    total = len(default_model().metrics)
    for option in body["options"]:
        assert option["total_metrics"] == total
        # The count is what makes the selector honest rather than decorative:
        # an account that has only had billing uploaded should visibly narrow
        # the catalogue rather than returning blanks.
        assert 0 <= option["answerable_metrics"] <= total


# ────────────────────────────────────────────────────────── figures per scope
@pytest.mark.asyncio
async def test_an_account_scoped_figure_is_that_account_alone(settings: Settings) -> None:
    async with client_for(settings) as client:
        org = (await client.get("/api/v1/metrics/q.volume/tile?scope=organization")).json()
        per_account = {
            account: (
                await client.get(f"/api/v1/metrics/q.volume/tile?scope=account&account={account}")
            ).json()
            for account in ("ACME_PROD", "ACME_ANALYTICS", "ACME_SANDBOX", "ACME_APAC")
        }

    # Each account reports only itself …
    for account, tile in per_account.items():
        assert tile["scope"] == "account"
        assert tile["scope_account"] == account
        assert tile["contributing_accounts"] == [account]
        assert Decimal(str(tile["value"])) > 0

    # … and the organization is exactly their sum, not one account's figure
    # relabelled. An additive metric is the case where that can be checked.
    assert Decimal(str(org["value"])) == sum(
        Decimal(str(tile["value"])) for tile in per_account.values()
    )
    assert org["scope"] == "organization"
    assert sorted(org["contributing_accounts"]) == sorted(per_account)


@pytest.mark.asyncio
async def test_accounts_differ_from_each_other(settings: Settings) -> None:
    """A filter that returns the same number for every account is not filtering."""
    async with client_for(settings) as client:
        values = [
            Decimal(
                str(
                    (
                        await client.get(
                            f"/api/v1/metrics/q.volume/tile?scope=account&account={account}"
                        )
                    ).json()["value"]
                )
            )
            for account in ("ACME_PROD", "ACME_SANDBOX")
        ]
    assert values[0] != values[1]


@pytest.mark.asyncio
async def test_a_query_at_account_scope_reports_the_scope_it_used(settings: Settings) -> None:
    async with client_for(settings) as client:
        body = (
            await client.post(
                "/api/v1/metrics/query",
                json={
                    "metrics": ["q.volume"],
                    "scope": "account",
                    "account": "ACME_PROD",
                    "limit": 50,
                },
            )
        ).json()
    assert body["scope"] == "account"
    assert body["scope_account"] == "ACME_PROD"
    assert body["contributing_accounts"] == ["ACME_PROD"]


@pytest.mark.asyncio
async def test_account_scope_without_an_account_is_rejected(settings: Settings) -> None:
    async with client_for(settings) as client:
        response = await client.post(
            "/api/v1/metrics/query", json={"metrics": ["q.volume"], "scope": "account"}
        )
    assert response.status_code == 422


# ──────────────────────────────────────────── a roll-up that misses an account
@pytest.fixture(scope="module")
def incomplete_lake(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Settings]:
    """Three accounts uploaded; the fourth is billed but never sent its detail.

    The realistic enterprise state, and the one most easily misread: billing
    knows about ACME_APAC, so it appears in every organization spend figure,
    while its queries are simply absent — which looks exactly like a quiet
    account unless the platform says otherwise.
    """
    lake: Path = tmp_path_factory.mktemp("partial-lake")
    extract: Path = tmp_path_factory.mktemp("partial-extract")
    organization = generate_organization(ORG)
    layout = write_organization_csv(organization, extract)

    pipeline = IngestPipeline(lake)
    for account, directory in sorted(layout.account_dirs.items()):
        if account == "ACME_APAC":
            continue
        pipeline.ingest_directory(directory, account=account)
    pipeline.ingest_directory(layout.organization_dir, account=organization.organization_name)
    yield Settings(_env_file=None, storage={"provider": "local", "bucket": str(lake)})


def test_the_organization_roster_comes_from_billing_not_from_what_landed(
    incomplete_lake: Settings,
) -> None:
    """`ORGANIZATION_USAGE` names every account, uploaded or not."""
    service = MetricService(incomplete_lake)
    assert "ACME_APAC" in service.organization_roster()
    assert "ACME_APAC" not in service.landed_accounts()


@pytest.mark.asyncio
async def test_a_roll_up_missing_an_account_says_which_one(
    incomplete_lake: Settings,
) -> None:
    async with client_for(incomplete_lake) as client:
        tile = (await client.get("/api/v1/metrics/q.volume/tile")).json()

    assert tile["scope_partial"] is True
    assert tile["missing_accounts"] == ["ACME_APAC"]
    assert "ACME_APAC" not in tile["contributing_accounts"]
    assert Decimal(str(tile["value"])) > 0  # still an answer, just not the whole one


@pytest.mark.asyncio
async def test_an_organization_usage_figure_is_not_partial_when_detail_is_missing(
    incomplete_lake: Settings,
) -> None:
    """The distinction that makes the flag worth reading.

    Billing covers every account whether or not its `ACCOUNT_USAGE` was ever
    uploaded, so an organization spend figure is complete even here. Flagging
    it would train people to ignore the warning on the figures that are
    genuinely incomplete.
    """
    async with client_for(incomplete_lake) as client:
        tile = (await client.get("/api/v1/metrics/org.spend_currency/tile")).json()

    assert tile["scope_partial"] is False
    assert tile["missing_accounts"] == []
    assert Decimal(str(tile["value"])) > 0


@pytest.mark.asyncio
async def test_a_complete_fleet_raises_no_partial_warning(settings: Settings) -> None:
    """A warning that is always on is one nobody reads."""
    async with client_for(settings) as client:
        tile = (await client.get("/api/v1/metrics/q.volume/tile")).json()
    assert tile["scope_partial"] is False
    assert tile["missing_accounts"] == []


# ───────────────────────────────────────────────── what a scope cannot answer
def test_an_account_usage_metric_has_no_single_query_organization_total_in_live() -> None:
    """LIVE returns one account per connection, and averaging a rate across
    accounts would be wrong — so the platform declines rather than inventing
    a number (R12).
    """
    model, registry = default_model(), default_registry()
    verdict = assess(
        model.metric("q.volume"),
        ScopeRequest(scope=Scope.ORGANIZATION),
        model=model,
        registry=registry,
        mode="live",
    )
    assert not verdict.available
    assert verdict.reason is not None
    assert "ACCOUNT_USAGE" in verdict.reason
    assert "select an account" in verdict.reason.lower()


def test_an_organization_source_answers_at_organization_scope_in_live() -> None:
    """The other half: ORGANIZATION_USAGE is organization-wide from any
    connection granted it, so it needs no fan-out."""
    model, registry = default_model(), default_registry()
    verdict = assess(
        model.metric("cost.spend_usd"),
        ScopeRequest(scope=Scope.ORGANIZATION),
        model=model,
        registry=registry,
        mode="live",
    )
    assert verdict.available


@pytest.mark.asyncio
async def test_a_metric_that_cannot_narrow_explains_itself_rather_than_widening(
    settings: Settings,
) -> None:
    """The failure this design exists to prevent.

    Widening an un-narrowable metric to organization scope would return the
    organization's figure under an account's label, and nothing downstream
    could tell. A tile says why instead, and shows no number.
    """
    service = MetricService(settings)
    un_narrowable = [
        metric.id
        for metric in service.catalog_entries()
        if not service.scope_verdict(
            metric.id, ScopeRequest(scope=Scope.ACCOUNT, account="ACME_PROD")
        ).available
    ]
    if not un_narrowable:
        pytest.skip("every metric in the catalogue can currently be narrowed to an account")

    async with client_for(settings) as client:
        tile = (
            await client.get(
                f"/api/v1/metrics/{un_narrowable[0]}/tile?scope=account&account=ACME_PROD"
            )
        ).json()

    assert tile["value"] is None
    assert tile["unavailable_reason"]
    assert tile["scope"] == "account"
