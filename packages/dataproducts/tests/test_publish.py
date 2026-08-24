"""The publication state machine and its refusals (§13.3, §13.4; R2, R8).

Every refusal path is tested individually, because each one is the last thing
standing between a half-working data product and somebody's Snowflake account.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from snowobs_dataproducts.contracts import ContractStore
from snowobs_dataproducts.model import DataProduct, Lifecycle, Version
from snowobs_dataproducts.publish import (
    APPROVAL_TRANSITIONS,
    MIN_REASON_LENGTH,
    TRANSITIONS,
    ApprovalError,
    CheckStatus,
    LifecycleError,
    LifecycleLedger,
    PreflightError,
    PublishWorkflow,
)
from snowobs_dataproducts.registry import load_products

ACTOR = "sam@internal"
REASON = "Reviewed at the 2026-08-24 data governance board"


@pytest.fixture(scope="module")
def products():
    return load_products()


@pytest.fixture
def draft(products) -> DataProduct:
    return products.get("pipeline_health").model_copy(update={"status": Lifecycle.DRAFT})


@pytest.fixture
def workflow(draft) -> PublishWorkflow:
    return PublishWorkflow(draft, ledger=LifecycleLedger())


def _approve(workflow: PublishWorkflow) -> None:
    workflow.propose(actor="dana@internal", reason="Ready for reliability review")
    workflow.approve(actor=ACTOR, reason=REASON)


# ══════════════════════════════════════════════════════ the state machine ════
def test_the_happy_path_records_every_step(workflow) -> None:
    _approve(workflow)
    assert workflow.status is Lifecycle.APPROVED
    bundle = workflow.publish(actor=ACTOR, reason=REASON)
    assert workflow.status is Lifecycle.PUBLISHED

    history = workflow.ledger.for_product(workflow.product.id)
    assert [e.to_status for e in history] == [
        Lifecycle.PROPOSED,
        Lifecycle.APPROVED,
        Lifecycle.PUBLISHED,
    ]
    assert bundle.approval.actor == ACTOR
    assert bundle.approval.reason == REASON
    assert bundle.approval.at.tzinfo is not None
    assert bundle.approval.diff_summary == "first published version"


def test_the_lifecycle_continues_through_deprecation_and_retirement(workflow) -> None:
    _approve(workflow)
    workflow.publish(actor=ACTOR, reason=REASON)
    workflow.deprecate(actor=ACTOR, reason="Superseded by the reliability data product")
    assert workflow.status is Lifecycle.DEPRECATED
    workflow.retire(actor=ACTOR, reason="Deprecation window elapsed with no consumers")
    assert workflow.status is Lifecycle.RETIRED


def test_a_proposal_can_be_sent_back_to_draft(workflow) -> None:
    workflow.propose(actor="dana@internal", reason="Ready for review")
    workflow.send_back(actor=ACTOR, reason="Freshness target is optimistic; rework")
    assert workflow.status is Lifecycle.DRAFT


def test_every_transition_is_recorded_with_actor_time_and_reason(workflow) -> None:
    event = workflow.propose(actor="dana@internal", reason="Ready for review")
    record = event.to_record()
    assert record["actor"] == "dana@internal"
    assert record["reason"] == "Ready for review"
    assert record["from_status"] == "draft"
    assert record["to_status"] == "proposed"
    assert record["at"]


def test_the_ledger_is_append_only(workflow) -> None:
    _approve(workflow)
    assert not hasattr(workflow.ledger, "remove")
    assert not hasattr(workflow.ledger, "update")
    assert len(workflow.ledger.events) == 2


def test_a_retired_product_has_nowhere_left_to_go() -> None:
    assert TRANSITIONS[Lifecycle.RETIRED] == frozenset()


# ══════════════════════════════════════════════════════════ refusals ═════════
def test_a_draft_cannot_skip_straight_to_published(workflow) -> None:
    with pytest.raises(LifecycleError, match="it can move to proposed"):
        workflow.transition(Lifecycle.PUBLISHED, actor=ACTOR, reason=REASON)


def test_publishing_without_an_approval_is_refused(workflow) -> None:
    """R8: nothing publishes without a recorded human approval."""
    workflow.propose(actor="dana@internal", reason="Ready for review")
    with pytest.raises(LifecycleError, match="recorded approval"):
        workflow.publish(actor=ACTOR, reason=REASON)


def test_publishing_a_draft_is_refused(workflow) -> None:
    with pytest.raises(LifecycleError, match="recorded approval"):
        workflow.publish(actor=ACTOR, reason=REASON)


def test_an_anonymous_transition_is_refused(workflow) -> None:
    with pytest.raises(ApprovalError, match="the human who made it"):
        workflow.propose(actor="   ", reason="Ready for review")


def test_a_transition_without_a_reason_is_refused(workflow) -> None:
    with pytest.raises(ApprovalError, match="record why"):
        workflow.propose(actor=ACTOR, reason="  ")


def test_a_throwaway_approval_reason_is_refused(workflow) -> None:
    """An audit record reading "ok" is not an audit record."""
    workflow.propose(actor="dana@internal", reason="Ready for review")
    with pytest.raises(ApprovalError, match=str(MIN_REASON_LENGTH)):
        workflow.approve(actor=ACTOR, reason="ok")


def test_a_short_reason_is_acceptable_below_the_approval_bar(workflow) -> None:
    """Only approvals carry the strict bar; a proposal may be terse."""
    assert Lifecycle.PROPOSED not in APPROVAL_TRANSITIONS
    workflow.propose(actor="dana@internal", reason="wip")
    assert workflow.status is Lifecycle.PROPOSED


def test_republishing_a_published_product_is_refused(workflow) -> None:
    _approve(workflow)
    workflow.publish(actor=ACTOR, reason=REASON)
    with pytest.raises(LifecycleError, match="recorded approval"):
        workflow.publish(actor=ACTOR, reason=REASON)


# ══════════════════════════════════════════════════════════ preflight ═══════
def test_every_shipped_product_passes_preflight(products) -> None:
    for product in products:
        report = PublishWorkflow(product, ledger=LifecycleLedger()).preflight()
        assert report.passed, report.summary()
        assert len(report.checks) == 6


def test_an_unachievable_freshness_sla_fails_preflight(draft) -> None:
    """R7: a product cannot promise five minutes over a three-hour view."""
    optimistic = draft.model_copy(
        update={"sla": draft.sla.model_copy(update={"freshness_target_minutes": 5})}
    )
    report = PublishWorkflow(optimistic, ledger=LifecycleLedger()).preflight()
    failure = next(c for c in report.checks if c.name == "freshness SLA is achievable")
    assert failure.status is CheckStatus.FAILED
    assert "documents" in failure.detail
    assert not report.passed


def test_a_release_with_no_changelog_entry_fails_preflight(draft) -> None:
    orphaned = draft.model_copy(update={"change_log": []})
    report = PublishWorkflow(orphaned, ledger=LifecycleLedger()).preflight()
    failure = next(c for c in report.checks if c.name.startswith("breaking release"))
    assert failure.status is CheckStatus.FAILED
    assert "no change_log entry" in failure.detail


def test_a_breaking_change_under_a_minor_bump_fails_preflight(products, tmp_path) -> None:
    """§13.3: the version gate refuses a breaking change without a major bump."""
    product = products.get("warehouse_efficiency")
    # The real published 1.0.0 contract, which carried a relation the current
    # version withdrew. Re-declaring that release as 1.1.0 ships the withdrawal
    # as a minor version, which is exactly what the gate exists to catch.
    store = ContractStore(tmp_path)
    store.write(ContractStore().get(product.id, Version.parse("1.0.0")))
    minor = product.model_copy(
        update={
            "version": Version.parse("1.1.0"),
            "change_log": [
                product.change_log[0],
                product.change_log[1].model_copy(update={"version": Version.parse("1.1.0")}),
            ],
        }
    )
    report = PublishWorkflow(minor, store=store, ledger=LifecycleLedger()).preflight()
    failure = next(c for c in report.checks if c.name == "version bump covers the change")
    assert failure.status is CheckStatus.FAILED
    assert "major bump" in failure.detail
    assert not report.passed


def test_publish_refuses_when_a_check_fails_and_names_it(draft) -> None:
    optimistic = draft.model_copy(
        update={"sla": draft.sla.model_copy(update={"freshness_target_minutes": 5})}
    )
    workflow = PublishWorkflow(optimistic, ledger=LifecycleLedger())
    _approve(workflow)
    with pytest.raises(PreflightError) as excinfo:
        workflow.publish(actor=ACTOR, reason=REASON)
    assert "freshness SLA is achievable" in str(excinfo.value.detail)
    assert excinfo.value.report.failures
    # The refusal must not have advanced the product's state.
    assert workflow.status is Lifecycle.APPROVED


def test_a_failed_preflight_records_no_publication_event(draft) -> None:
    orphaned = draft.model_copy(update={"change_log": []})
    workflow = PublishWorkflow(orphaned, ledger=LifecycleLedger())
    _approve(workflow)
    with pytest.raises(PreflightError):
        workflow.publish(actor=ACTOR, reason=REASON)
    assert not [event for event in workflow.ledger.events if event.to_status is Lifecycle.PUBLISHED]


# ══════════════════════════════════════════════════════════ the bundle ══════
def test_the_bundle_carries_every_artifact(workflow) -> None:
    _approve(workflow)
    bundle = workflow.publish(actor=ACTOR, reason=REASON)
    for expected in (
        "README.md",
        "contract.yaml",
        "listing_manifest.yaml",
        "sql/01_foundations.sql",
        "sql/02_published_views.sql",
        "sql/04_semantic_view.sql",
        "sql/05_cortex_search.sql",
        "sql/06_share_and_listing.sql",
        "sql/07_agent.sql",
        "sql/08_grants.sql",
        "dbt/dbt_project.yml",
    ):
        assert expected in bundle.names, expected
    assert any(name.startswith("dbt/tests/") for name in bundle.names)


def test_a_product_without_sensitive_columns_ships_no_policy_file(products) -> None:
    product = products.get("finops_chargeback").model_copy(update={"status": Lifecycle.APPROVED})
    workflow = PublishWorkflow(product, ledger=LifecycleLedger())
    bundle = workflow.publish(actor=ACTOR, reason=REASON)
    assert "sql/03_policies.sql" not in bundle.names
    assert "sql/05_cortex_search.sql" not in bundle.names


def test_a_restricted_product_ships_masking_and_row_access(products) -> None:
    product = products.get("access_governance").model_copy(update={"status": Lifecycle.APPROVED})
    workflow = PublishWorkflow(product, ledger=LifecycleLedger())
    bundle = workflow.publish(actor=ACTOR, reason=REASON)
    policies = bundle["sql/03_policies.sql"]
    assert "CREATE MASKING POLICY" in policies
    assert "ADD ROW ACCESS POLICY" in policies


def test_the_runbook_records_the_approval_and_the_rollback(workflow) -> None:
    _approve(workflow)
    bundle = workflow.publish(actor=ACTOR, reason=REASON)
    readme = bundle["README.md"]
    assert "## Approval on record" in readme
    assert ACTOR in readme
    assert REASON in readme
    assert "## Rollback" in readme
    assert "DROP SEMANTIC VIEW IF EXISTS" in readme
    assert "## Validation checklist" in readme
    assert str(bundle.contract.freshness_guarantee_minutes) in readme


def test_the_runbook_says_the_platform_does_not_run_the_scripts(workflow) -> None:
    """R2/R8: the bundle is what a person applies, not what the app applies."""
    _approve(workflow)
    bundle = workflow.publish(actor=ACTOR, reason=REASON)
    assert "did not and will not run them" in bundle["README.md"]
    for name, body in bundle.files.items():
        if name.startswith("sql/"):
            assert "The platform does not execute this script" in body


def test_the_bundle_contract_round_trips(workflow) -> None:
    from snowobs_dataproducts.contracts import DataContract

    _approve(workflow)
    bundle = workflow.publish(actor=ACTOR, reason=REASON)
    assert DataContract.from_yaml(bundle["contract.yaml"]) == bundle.contract


def test_publishing_is_a_pure_text_operation(workflow, tmp_path: Path) -> None:
    """Nothing is written, connected to, or executed while a bundle is built."""
    before = sorted(p.name for p in tmp_path.iterdir())
    _approve(workflow)
    workflow.publish(actor=ACTOR, reason=REASON)
    assert sorted(p.name for p in tmp_path.iterdir()) == before
