"""The publication workflow (BUILD_PROMPT §13.3, §13.4; R2, R8).

This module is a **proposal and approval recorder**, not a mutation engine. It
never connects to Snowflake and never executes DDL. What it produces is a bundle
of artifacts and an audit trail; a human runs the artifacts in their own account
(R2), and no state advances without an approval event naming who, when, and why
(R8).

The state machine is:

``draft → proposed → approved → published → deprecated → retired``

with two escape hatches that are themselves recorded: a proposal can be sent
back to draft, and an approval can be withdrawn. Nothing skips ``approved``.

Publication runs preflight checks and **refuses with the specific failing
check** rather than publishing something that half works:

1. the contract still matches what the semantic layer produces;
2. every metric in the boundary compiles, in both dialects;
3. the freshness SLA is achievable given documented source latency (R7);
4. the change against the previously published contract carries a large enough
   version bump (§13.3);
5. a breaking change declares a migration note and a deprecation window;
6. the generated SQL contains no blanket grant (§27.3).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from snowobs_common.errors import AppError
from snowobs_dataproducts.contracts import (
    BreakingChangeError,
    ContractDiff,
    ContractStore,
    DataContract,
    build_contract,
    diff,
    freshness_floor,
)
from snowobs_dataproducts.emitters import SnowflakeTarget, audit_generated_sql
from snowobs_dataproducts.emitters.agent_spec import agent_name, emit_agent_spec
from snowobs_dataproducts.emitters.cortex_search import emit_cortex_search, search_service_name
from snowobs_dataproducts.emitters.dbt import emit_dbt_project
from snowobs_dataproducts.emitters.ddl import (
    emit_foundations_ddl,
    emit_grants,
    emit_policies,
    emit_published_views,
)
from snowobs_dataproducts.emitters.listing import (
    emit_listing_ddl,
    emit_listing_manifest,
    listing_name,
    share_name,
)
from snowobs_dataproducts.emitters.semantic_view import emit_semantic_view
from snowobs_dataproducts.model import DataProduct, Lifecycle
from snowobs_dataproducts.resolve import compile_dataset, resolve_datasets
from snowobs_semantics.compiler import CompilationError
from snowobs_semantics.dialect_shims import Dialect
from snowobs_semantics.model import SemanticModel, default_model
from snowobs_semantics.registry import SourceRegistry, default_registry

#: Which transitions exist at all. Anything not listed here is refused.
TRANSITIONS: dict[Lifecycle, frozenset[Lifecycle]] = {
    Lifecycle.DRAFT: frozenset({Lifecycle.PROPOSED}),
    Lifecycle.PROPOSED: frozenset({Lifecycle.APPROVED, Lifecycle.DRAFT}),
    Lifecycle.APPROVED: frozenset({Lifecycle.PUBLISHED, Lifecycle.DRAFT}),
    Lifecycle.PUBLISHED: frozenset({Lifecycle.DEPRECATED}),
    Lifecycle.DEPRECATED: frozenset({Lifecycle.RETIRED, Lifecycle.PUBLISHED}),
    Lifecycle.RETIRED: frozenset(),
}

#: Transitions that constitute a human approving something (R8). These carry the
#: strictest evidence requirement.
APPROVAL_TRANSITIONS = frozenset({Lifecycle.APPROVED, Lifecycle.PUBLISHED, Lifecycle.RETIRED})

#: Shortest reason we will accept on the record. "ok" is not an audit trail.
MIN_REASON_LENGTH = 12


class LifecycleError(AppError):
    """An illegal or unevidenced lifecycle transition."""

    status_code = 409
    title = "Invalid data product transition"
    problem_type = "https://snowobs.dev/problems/product-lifecycle"


class ApprovalError(LifecycleError):
    """A transition that would advance state without recorded human approval."""

    status_code = 400
    title = "Approval evidence missing"
    problem_type = "https://snowobs.dev/problems/approval-required"


class PreflightError(AppError):
    """Publication refused: at least one preflight check failed."""

    status_code = 409
    title = "Data product failed preflight"
    problem_type = "https://snowobs.dev/problems/product-preflight"

    def __init__(self, detail: str, report: PreflightReport) -> None:
        super().__init__(detail)
        self.report = report


class CheckStatus(StrEnum):
    PASSED = "pass"
    FAILED = "fail"


@dataclass(frozen=True)
class PreflightCheck:
    """One gate, its verdict, and the evidence behind it."""

    name: str
    status: CheckStatus
    detail: str

    @property
    def passed(self) -> bool:
        return self.status is CheckStatus.PASSED


@dataclass(frozen=True)
class PreflightReport:
    """Every check, in the order they ran — the publish wizard's checklist."""

    product_id: str
    version: str
    checks: tuple[PreflightCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> list[PreflightCheck]:
        return [check for check in self.checks if not check.passed]

    def summary(self) -> str:
        if self.passed:
            return f"{self.product_id} {self.version}: {len(self.checks)} checks passed"
        names = ", ".join(check.name for check in self.failures)
        return (
            f"{self.product_id} {self.version}: {len(self.failures)} of "
            f"{len(self.checks)} checks failed ({names})"
        )


@dataclass(frozen=True)
class ApprovalEvent:
    """One recorded transition. This is the audit record R8 requires."""

    product_id: str
    version: str
    from_status: Lifecycle
    to_status: Lifecycle
    actor: str
    at: datetime
    reason: str
    #: What changed, when the transition was a publication of a new version.
    diff_summary: str | None = None

    def to_record(self) -> dict[str, str | None]:
        return {
            "product_id": self.product_id,
            "version": self.version,
            "from_status": self.from_status.value,
            "to_status": self.to_status.value,
            "actor": self.actor,
            "at": self.at.isoformat(),
            "reason": self.reason,
            "diff_summary": self.diff_summary,
        }


@dataclass
class LifecycleLedger:
    """Append-only record of every transition, newest last.

    In-process by design at this phase: the durable audit store is the Postgres
    audit table (§17), and this ledger is what feeds it. It is append-only here
    too — there is no method that edits or removes an event.
    """

    events: list[ApprovalEvent] = field(default_factory=list)

    def append(self, event: ApprovalEvent) -> ApprovalEvent:
        self.events.append(event)
        return event

    def for_product(self, product_id: str) -> list[ApprovalEvent]:
        return [event for event in self.events if event.product_id == product_id]

    def current_status(self, product: DataProduct) -> Lifecycle:
        """The product's status after replaying its recorded transitions."""
        history = self.for_product(product.id)
        return history[-1].to_status if history else product.status

    def last_approval(self, product_id: str) -> ApprovalEvent | None:
        approvals = [
            event
            for event in self.for_product(product_id)
            if event.to_status in APPROVAL_TRANSITIONS
        ]
        return approvals[-1] if approvals else None


@dataclass(frozen=True)
class PublicationBundle:
    """Everything a human needs to apply the product by hand (§13.4, OFFLINE)."""

    product_id: str
    version: str
    files: dict[str, str]
    report: PreflightReport
    approval: ApprovalEvent
    contract: DataContract

    def __getitem__(self, name: str) -> str:
        return self.files[name]

    @property
    def names(self) -> list[str]:
        return sorted(self.files)


def _validate_evidence(actor: str, reason: str, to_status: Lifecycle) -> None:
    """Refuse a transition whose audit record would be worthless."""
    if not actor.strip():
        raise ApprovalError("a transition must record the human who made it (R8)")
    if not reason.strip():
        raise ApprovalError(f"a transition to {to_status.value} must record why (R8)")
    if to_status in APPROVAL_TRANSITIONS and len(reason.strip()) < MIN_REASON_LENGTH:
        raise ApprovalError(
            f"an approval reason must say something a reviewer can act on; "
            f"'{reason.strip()}' is under {MIN_REASON_LENGTH} characters"
        )


class PublishWorkflow:
    """Drives one product through its lifecycle, recording every step.

    The workflow owns no connection and issues no statement. Its output is an
    audit trail plus a bundle of text.
    """

    def __init__(
        self,
        product: DataProduct,
        model: SemanticModel | None = None,
        registry: SourceRegistry | None = None,
        store: ContractStore | None = None,
        ledger: LifecycleLedger | None = None,
        *,
        target: SnowflakeTarget | None = None,
    ) -> None:
        self.product = product
        self.model = model or default_model()
        self.registry = registry or default_registry()
        self.store = store or ContractStore()
        self.ledger = ledger or LifecycleLedger()
        self.target = target or SnowflakeTarget()

    # ------------------------------------------------------------- lifecycle
    @property
    def status(self) -> Lifecycle:
        return self.ledger.current_status(self.product)

    def transition(
        self,
        to_status: Lifecycle,
        *,
        actor: str,
        reason: str,
        diff_summary: str | None = None,
    ) -> ApprovalEvent:
        """Record one transition, refusing anything the state machine forbids."""
        current = self.status
        allowed = TRANSITIONS[current]
        if to_status not in allowed:
            permitted = ", ".join(sorted(s.value for s in allowed)) or "nothing"
            raise LifecycleError(
                f"{self.product.id} is {current.value}; it can move to {permitted}, "
                f"not to {to_status.value}"
            )
        _validate_evidence(actor, reason, to_status)
        return self.ledger.append(
            ApprovalEvent(
                product_id=self.product.id,
                version=str(self.product.version),
                from_status=current,
                to_status=to_status,
                actor=actor,
                at=datetime.now(tz=UTC),
                reason=reason.strip(),
                diff_summary=diff_summary,
            )
        )

    def propose(self, *, actor: str, reason: str) -> ApprovalEvent:
        return self.transition(Lifecycle.PROPOSED, actor=actor, reason=reason)

    def approve(self, *, actor: str, reason: str) -> ApprovalEvent:
        return self.transition(Lifecycle.APPROVED, actor=actor, reason=reason)

    def send_back(self, *, actor: str, reason: str) -> ApprovalEvent:
        return self.transition(Lifecycle.DRAFT, actor=actor, reason=reason)

    def deprecate(self, *, actor: str, reason: str) -> ApprovalEvent:
        return self.transition(Lifecycle.DEPRECATED, actor=actor, reason=reason)

    def retire(self, *, actor: str, reason: str) -> ApprovalEvent:
        return self.transition(Lifecycle.RETIRED, actor=actor, reason=reason)

    # ------------------------------------------------------------- preflight
    def contract(self) -> DataContract:
        return build_contract(self.product, self.model, self.registry)

    def contract_diff(self) -> ContractDiff | None:
        """The diff against the last published contract, or ``None`` if first.

        Propagates :class:`BreakingChangeError` — a version bump too small for
        the change is not a diff result, it is a refusal.
        """
        previous = self.store.latest_before(self.product.id, self.product.version)
        if previous is None:
            return None
        return diff(previous, self.contract())

    def preflight(self) -> PreflightReport:
        """Run every gate and report each verdict. Never raises on a failure."""
        checks: list[PreflightCheck] = [
            self._check_contract_validates(),
            self._check_metrics_compile(),
            self._check_freshness_achievable(),
            self._check_version_policy(),
            self._check_migration_note(),
            self._check_generated_sql(),
        ]
        return PreflightReport(
            product_id=self.product.id,
            version=str(self.product.version),
            checks=tuple(checks),
        )

    def _check_contract_validates(self) -> PreflightCheck:
        name = "contract matches the semantic layer"
        try:
            validation = self.contract().validate_against(self.model, self.registry)
        except Exception as exc:
            return PreflightCheck(name, CheckStatus.FAILED, str(exc))
        if validation.ok:
            return PreflightCheck(
                name, CheckStatus.PASSED, "every contracted column maps to a live governed metric"
            )
        detail = "; ".join(f"{f.path}: {f.detail}" for f in validation.findings)
        return PreflightCheck(name, CheckStatus.FAILED, detail)

    def _check_metrics_compile(self) -> PreflightCheck:
        name = "every metric compiles in both engines"
        failures: list[str] = []
        for spec in resolve_datasets(self.product, self.model):
            for dialect in Dialect:
                try:
                    compile_dataset(spec, dialect, self.model)
                except (CompilationError, AppError) as exc:
                    failures.append(f"{spec.view_name} ({dialect.value}): {exc}")
        if failures:
            return PreflightCheck(name, CheckStatus.FAILED, "; ".join(failures))
        return PreflightCheck(
            name,
            CheckStatus.PASSED,
            f"{len(self.product.metrics)} metrics compile for Snowflake and DuckDB",
        )

    def _check_freshness_achievable(self) -> PreflightCheck:
        name = "freshness SLA is achievable"
        floor = freshness_floor(list(self.product.metrics), self.model, self.registry)
        target = self.product.sla.freshness_target_minutes
        if target < floor:
            return PreflightCheck(
                name,
                CheckStatus.FAILED,
                f"the product promises {target} minutes but its slowest source documents "
                f"{floor} minutes of latency (R7)",
            )
        return PreflightCheck(
            name,
            CheckStatus.PASSED,
            f"target {target} min ≥ documented source latency {floor} min",
        )

    def _check_version_policy(self) -> PreflightCheck:
        name = "version bump covers the change"
        try:
            computed = self.contract_diff()
        except BreakingChangeError as exc:
            return PreflightCheck(name, CheckStatus.FAILED, str(exc.detail or exc))
        except Exception as exc:
            return PreflightCheck(name, CheckStatus.FAILED, str(exc))
        if computed is None:
            return PreflightCheck(
                name, CheckStatus.PASSED, "first published version; nothing to compare against"
            )
        return PreflightCheck(
            name,
            CheckStatus.PASSED,
            f"{len(computed.changes)} change(s) since {computed.baseline_version}; "
            f"requires a {computed.required_bump.value} bump, declared "
            f"{computed.declared_bump.value}",
        )

    def _check_migration_note(self) -> PreflightCheck:
        name = "breaking release carries a migration note"
        entry = next(
            (e for e in self.product.change_log if e.version == self.product.version), None
        )
        if entry is None:
            return PreflightCheck(
                name,
                CheckStatus.FAILED,
                f"version {self.product.version} has no change_log entry",
            )
        if not entry.breaking:
            return PreflightCheck(name, CheckStatus.PASSED, "release is not breaking")
        if self.product.sla.deprecation_notice_days < 1:
            return PreflightCheck(
                name, CheckStatus.FAILED, "a breaking release needs a deprecation window (§13.3)"
            )
        return PreflightCheck(
            name,
            CheckStatus.PASSED,
            f"migration note recorded; {self.product.sla.deprecation_notice_days}-day "
            f"deprecation window",
        )

    def _check_generated_sql(self) -> PreflightCheck:
        name = "generated SQL grants nothing blanket"
        try:
            artifacts = self._sql_artifacts(self.contract())
        except Exception as exc:
            return PreflightCheck(name, CheckStatus.FAILED, f"artifact generation failed: {exc}")
        problems = [
            f"{path}: {problem}"
            for path, sql in artifacts.items()
            for problem in audit_generated_sql(sql)
        ]
        if problems:
            return PreflightCheck(name, CheckStatus.FAILED, "; ".join(problems))
        return PreflightCheck(
            name,
            CheckStatus.PASSED,
            f"{len(artifacts)} SQL artifacts audited; no IMPORTED PRIVILEGES, no "
            f"ACCOUNTADMIN, no ALL PRIVILEGES",
        )

    # ------------------------------------------------------------- artifacts
    def _sql_artifacts(self, contract: DataContract) -> dict[str, str]:
        product = self.product
        manifest = emit_listing_manifest(product, contract, target=self.target)
        artifacts: dict[str, str] = {
            "sql/01_foundations.sql": emit_foundations_ddl(self.target),
            "sql/02_published_views.sql": emit_published_views(
                product, contract, self.model, target=self.target
            ),
            "sql/04_semantic_view.sql": emit_semantic_view(
                product, contract, self.model, target=self.target
            ),
            "sql/06_share_and_listing.sql": emit_listing_ddl(
                product, contract, manifest, target=self.target
            ),
            "sql/07_agent.sql": emit_agent_spec(product, contract, self.model, target=self.target),
            "sql/08_grants.sql": emit_grants(product, contract, target=self.target),
        }
        policies = emit_policies(product, contract, target=self.target)
        if policies is not None:
            artifacts["sql/03_policies.sql"] = policies
        if product.search is not None:
            artifacts["sql/05_cortex_search.sql"] = emit_cortex_search(
                product, contract, target=self.target
            )
        return artifacts

    def bundle(
        self, contract: DataContract, approval: ApprovalEvent, report: PreflightReport
    ) -> dict[str, str]:
        """Every artifact, keyed by its path inside the downloadable bundle."""
        files = dict(self._sql_artifacts(contract))
        files["listing_manifest.yaml"] = emit_listing_manifest(
            self.product, contract, target=self.target
        )
        files["contract.yaml"] = contract.to_yaml()
        files.update(
            emit_dbt_project(
                self.product, contract, self.model, self.registry, target=self.target
            ).files
        )
        files["README.md"] = self.runbook(contract, approval, report, sorted(files))
        return files

    # ------------------------------------------------------------- publish
    def publish(self, *, actor: str, reason: str) -> PublicationBundle:
        """Record the publication and emit the bundle — or refuse, with the reason.

        Refuses when the product has not been approved, when the approval
        evidence is thin, or when any preflight check fails. It does not execute
        the artifacts it produces; a human does (R2, R8).
        """
        current = self.status
        if current is not Lifecycle.APPROVED:
            raise LifecycleError(
                f"{self.product.id} is {current.value}: nothing publishes without a "
                f"recorded approval (R8)"
            )
        _validate_evidence(actor, reason, Lifecycle.PUBLISHED)

        report = self.preflight()
        if not report.passed:
            raise PreflightError(report.summary(), report)

        contract = self.contract()
        computed = self.contract_diff()
        summary = (
            f"{len(computed.changes)} change(s) since {computed.baseline_version}; "
            f"{len(computed.breaking)} breaking"
            if computed is not None
            else "first published version"
        )
        approval = self.transition(
            Lifecycle.PUBLISHED, actor=actor, reason=reason, diff_summary=summary
        )
        return PublicationBundle(
            product_id=self.product.id,
            version=str(self.product.version),
            files=self.bundle(contract, approval, report),
            report=report,
            approval=approval,
            contract=contract,
        )

    # ------------------------------------------------------------- runbook
    def runbook(
        self,
        contract: DataContract,
        approval: ApprovalEvent,
        report: PreflightReport,
        file_names: Sequence[str],
    ) -> str:
        """The bundle's README: what to run, in what order, and how to roll back."""
        product = self.product
        lines = [
            f"# {product.name} {product.version} — deployment bundle",
            "",
            f"Product `{product.id}` · owner **{product.owner}** · classification "
            f"**{product.classification.value}**",
            "",
            "## What this is",
            "",
            "Everything needed to stand this data product up in a Snowflake account by",
            "hand. The platform generated these files; it did not and will not run them.",
            "Applying them is a deliberate act by a person holding the publisher role.",
            "",
            "## Approval on record",
            "",
            f"- **Approved by:** {approval.actor}",
            f"- **At:** {approval.at.isoformat()}",
            f"- **Reason:** {approval.reason}",
            f"- **Transition:** {approval.from_status.value} → {approval.to_status.value}",
        ]
        if approval.diff_summary:
            lines.append(f"- **Change since the last published version:** {approval.diff_summary}")
        lines.extend(
            [
                "",
                "## Contract summary",
                "",
                f"- Relations: {len(contract.datasets)} ({', '.join(contract.dataset_names)})",
                f"- Columns: {contract.column_count}",
                f"- Governed metrics: {len(contract.metric_ids)}",
                f"- Freshness guarantee: **{contract.freshness_guarantee_minutes} minutes** — "
                f"the documented latency of the slowest source. No surface may imply a "
                f"figure is fresher than this.",
                f"- Availability target: {contract.availability_pct}%",
                f"- Retention: {contract.retention_days} days",
                f"- Deprecation notice: {contract.deprecation_notice_days} days",
                "",
                "## Preflight",
                "",
            ]
        )
        lines.extend(f"- [x] {check.name} — {check.detail}" for check in report.checks)
        lines.extend(["", "## Apply, in order", ""])
        lines.extend(
            f"{index}. `{name}`"
            for index, name in enumerate(sorted(n for n in file_names if n.startswith("sql/")), 1)
        )
        lines.extend(
            [
                "",
                "The dbt project under `dbt/` rebuilds the same relations on a schedule; run",
                "`dbt build` against a profile pointing at the same database. `contract.yaml`",
                "is the machine-readable contract and `listing_manifest.yaml` is the manifest",
                "embedded in the listing DDL — both are here so a reviewer can read them",
                "without parsing SQL.",
                "",
                "## Validation checklist",
                "",
            ]
        )
        lines.extend(f"- [ ] {item}" for item in self.validation_checklist(contract))
        lines.extend(["", "## Rollback", "", "In this order, and safe to run at any point:", ""])
        lines.extend(
            f"{index}. {step}" for index, step in enumerate(self.rollback_steps(contract), 1)
        )
        lines.extend(
            [
                "",
                "The curated and raw layers are untouched by this bundle, so rolling back",
                "loses no data.",
                "",
                "## Support",
                "",
                f"- Documentation: {product.documentation_url}",
                f"- Support channel: {product.sla.support_channel}",
                "",
            ]
        )
        return "\n".join(lines)

    def rollback_steps(self, contract: DataContract) -> list[str]:
        """Undo a publication, newest object first."""
        product = self.product
        target = self.target
        steps = [
            f"`ALTER ORGANIZATION LISTING {listing_name(product)} SET PUBLISH = FALSE;`",
            f"`DROP AGENT IF EXISTS {target.qualified(target.agent_schema, agent_name(product))};`",
        ]
        if product.search is not None:
            steps.append(
                f"`DROP CORTEX SEARCH SERVICE IF EXISTS "
                f"{target.qualified(target.search_schema, search_service_name(product))};`"
            )
        steps.append(
            f"`DROP SEMANTIC VIEW IF EXISTS "
            f"{target.qualified(target.semantic_schema, product.slug_upper)};`"
        )
        steps.append(f"Revoke the share grants, then `DROP SHARE IF EXISTS {share_name(product)};`")
        steps.extend(
            f"`DROP VIEW IF EXISTS {target.view(dataset.name)};`" for dataset in contract.datasets
        )
        return steps

    def validation_checklist(self, contract: DataContract) -> list[str]:
        """The post-apply checks a human ticks off (skill Phase 7)."""
        product = self.product
        items = [
            f"Every published view returns rows: "
            f"`SELECT COUNT(*) FROM {self.target.view(dataset.name)};` is non-zero"
            for dataset in contract.datasets
        ]
        items.extend(
            [
                f"The newest row in each view is inside its freshness guarantee "
                f"({contract.freshness_guarantee_minutes} minutes at product level)",
                f"Semantic view resolves: `DESCRIBE SEMANTIC VIEW "
                f"{self.target.qualified(self.target.semantic_schema, product.slug_upper)};`",
                "A verified query from the semantic view returns the expected shape through "
                "Cortex Analyst",
            ]
        )
        if product.search is not None:
            items.append(
                f"The Cortex Search service returns hits for a known "
                f"{product.search.column.lower()} value"
            )
        if product.classification.requires_masking:
            items.append(
                "A role outside the consumer role sees masked values in every column the "
                "contract marks sensitive"
            )
        items.extend(
            [
                "The listing is visible in Snowsight → Data Products → Private Sharing for a "
                "consumer-role user once you publish it",
                "A consumer account can mount the listing and select from the published views",
                "The agent answers a sample question end to end in Snowflake Intelligence",
                f"`{self.target.agent_warehouse}` carries a resource monitor with a credit cap",
                "Grants match the product's consumer register and nothing wider",
            ]
        )
        return items
