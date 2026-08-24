"""Asking an agent about one account rather than the whole organization (§9).

An enterprise runs many accounts under one organization, and the same question
means different things at the two levels. Three failures matter here, and each
would produce a confident, exact, wrong answer:

* answering organization-wide a question that named one account;
* answering for an account a metric that has no per-account meaning;
* quoting an organization total as complete when an account never landed.

The tool refuses or qualifies in all three cases, and the tests below are what
hold it to that. The refusals matter more than the successes: a figure at the
wrong scope is not detectable downstream, because it is a real number.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from snowobs_agents.runtime.routing import account_named
from snowobs_agents.runtime.tools import ToolContext, build_registry
from snowobs_engines.duckdb_engine import DuckDBEngine
from snowobs_fixtures.organization import (
    OrganizationConfig,
    generate_organization,
    write_organization_csv,
)
from snowobs_ingest.catalog import DuckDBCatalog
from snowobs_ingest.loader import IngestPipeline
from snowobs_semantics.compiler import SemanticCompiler
from snowobs_semantics.model import default_model

ACCOUNTS = ("ACME_ANALYTICS", "ACME_APAC", "ACME_PROD", "ACME_SANDBOX")


@pytest.fixture(scope="module")
def org_lake(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root: Path = tmp_path_factory.mktemp("agent-org-lake")
    extract: Path = tmp_path_factory.mktemp("agent-org-extract")
    organization = generate_organization(OrganizationConfig(days=7))
    layout = write_organization_csv(organization, extract)
    pipeline = IngestPipeline(root)
    for account, directory in layout.account_dirs.items():
        pipeline.ingest_directory(directory, account=account)
    pipeline.ingest_directory(layout.organization_dir, account=organization.organization_name)
    return root


@pytest.fixture
def org_context(org_lake: Path) -> Iterator[ToolContext]:
    catalog = DuckDBCatalog(org_lake, tenant="default")
    catalog.register_all()
    try:
        yield ToolContext(
            engine=DuckDBEngine(catalog),
            compiler=SemanticCompiler(),
            model=default_model(),
            actor="tester@example.com",
            accounts=ACCOUNTS,
            organization="ACME_GROUP",
        )
    finally:
        catalog.close()


def _query(context: ToolContext, **arguments: object) -> tuple[dict[str, object], bool]:
    outcome = build_registry()["query_metric"].run(context, dict(arguments))
    if outcome.is_error:
        return {"error": outcome.content}, True
    return json.loads(outcome.content), False


# ────────────────────────────────────────────────────── scoping to an account
def test_an_account_scoped_answer_is_that_account_alone(org_context: ToolContext) -> None:
    payload, failed = _query(org_context, metrics=["q.volume"], account="ACME_PROD", by_time=False)
    assert not failed
    assert payload["scope"] == "account"
    assert payload["scope_account"] == "ACME_PROD"
    assert payload["contributing_accounts"] == ["ACME_PROD"]
    assert payload["missing_accounts"] == []


def test_the_accounts_add_up_to_the_organization(org_context: ToolContext) -> None:
    """The scoped figures partition the organization's, so a mis-scoped answer
    would not merely be differently labelled — it would be arithmetically wrong.
    """

    def volume(**arguments: object) -> Decimal:
        payload, failed = _query(org_context, metrics=["q.volume"], by_time=False, **arguments)
        assert not failed, payload
        rows = payload["rows"]
        assert isinstance(rows, list)
        return Decimal(str(rows[0]["Q_VOLUME"]))

    assert sum(volume(account=account) for account in ACCOUNTS) == volume()


def test_an_organization_wide_answer_names_the_accounts_behind_it(
    org_context: ToolContext,
) -> None:
    payload, failed = _query(org_context, metrics=["q.volume"], by_time=False)
    assert not failed
    assert payload["scope"] == "organization"
    assert payload["scope_account"] is None
    assert payload["contributing_accounts"] == list(ACCOUNTS)
    assert payload["scope_label"] == "ACME_GROUP"


# ─────────────────────────────────────────────────────────────── the refusals
def test_an_account_that_does_not_exist_is_refused_not_widened(
    org_context: ToolContext,
) -> None:
    """R3: the alternative is answering organization-wide under a name that was
    never in the fleet, which nothing downstream could detect.
    """
    payload, failed = _query(org_context, metrics=["q.volume"], account="ACME_NOPE")
    assert failed
    error = str(payload["error"])
    assert "ACME_NOPE" in error
    assert "ACME_PROD" in error  # says which accounts do exist


def test_a_metric_with_no_per_account_meaning_says_so(org_context: ToolContext) -> None:
    """A contract belongs to the organization. Scoping it to an account would
    return the organization's figure under the account's label.
    """
    organization_only = [
        metric.id
        for metric in default_model().metrics.values()
        if metric.domain == "organization" and "account" not in metric.dimensions
    ]
    assert organization_only, "the catalogue should carry organization-only metrics"

    payload, failed = _query(
        org_context, metrics=[organization_only[0]], account="ACME_PROD", by_time=False
    )
    assert failed
    assert "organization" in str(payload["error"]).lower()


def test_an_incomplete_roll_up_is_reported_with_the_answer(org_context: ToolContext) -> None:
    """An organization total missing an account is an under-count, and the tool
    result says which account is missing rather than leaving the agent to
    present the shortfall as the whole.
    """
    org_context.missing_accounts = ("ACME_EMEA",)
    payload, failed = _query(org_context, metrics=["q.volume"], by_time=False)
    assert not failed
    assert payload["missing_accounts"] == ["ACME_EMEA"]


# ───────────────────────────────────────────────────────────── list_accounts
def test_list_accounts_reports_the_fleet_and_its_gaps(org_context: ToolContext) -> None:
    org_context.missing_accounts = ("ACME_EMEA",)
    outcome = build_registry()["list_accounts"].run(org_context, {})
    assert not outcome.is_error
    payload = json.loads(outcome.content)
    assert payload["organization"] == "ACME_GROUP"
    assert payload["accounts_with_data"] == list(ACCOUNTS)
    assert payload["accounts_missing_data"] == ["ACME_EMEA"]


def test_a_single_account_deployment_says_it_has_no_breakdown() -> None:
    """Not every deployment is an enterprise. One that never stamped an account
    says so, rather than reporting an empty fleet as though accounts were lost.
    """
    context = ToolContext(engine=None, compiler=SemanticCompiler(), model=default_model())
    outcome = build_registry()["list_accounts"].run(context, {})
    assert not outcome.is_error
    assert "no per-account breakdown" in outcome.content


# ───────────────────────────────────────────── naming an account in the words
@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("what did ACME_PROD spend last month?", "ACME_PROD"),
        ("what did acme_prod spend last month?", "ACME_PROD"),
        ("how much did we spend?", None),
        # Two accounts named is a comparison, not a scope: answering for either
        # one would put one account's figure under a question about both.
        ("compare ACME_PROD and ACME_SANDBOX", None),
        # Word-bounded, so an account is not matched inside a longer word.
        ("how is the ACME_PRODUCTION rollout?", None),
    ],
)
def test_the_deterministic_path_scopes_to_an_account_the_question_names(
    question: str, expected: str | None
) -> None:
    assert account_named(question, ACCOUNTS) == expected
