"""Data product orchestration for the API (BUILD_PROMPT §13, §15).

Holds the registry, the published-contract store, and the lifecycle ledger the
router reads and appends to. There is no SQL here and no Snowflake connection:
the service composes the product package's pure functions and records approval
events (R2, R8).
"""

from __future__ import annotations

from snowobs_common.config import Settings
from snowobs_dataproducts.contracts import (
    BreakingChangeError,
    ContractDiff,
    ContractStore,
    ContractValidation,
    DataContract,
    build_contract,
)
from snowobs_dataproducts.emitters import SnowflakeTarget
from snowobs_dataproducts.model import DataProduct, Lifecycle
from snowobs_dataproducts.publish import (
    ApprovalEvent,
    LifecycleLedger,
    PreflightReport,
    PublicationBundle,
    PublishWorkflow,
)
from snowobs_dataproducts.registry import ProductRegistry, load_products
from snowobs_semantics.model import default_model
from snowobs_semantics.registry import default_registry

#: Process-wide ledger. Approval events are durable in the Postgres audit tables
#: (§17); this is the in-flight record the API appends to and reads back within
#: a running process (ASSUMPTIONS A-18).
_LEDGER = LifecycleLedger()


class ProductService:
    """Registry, contracts, diffs, bundles, and lifecycle transitions."""

    def __init__(self, settings: Settings, ledger: LifecycleLedger | None = None) -> None:
        self.settings = settings
        self.ledger = ledger if ledger is not None else _LEDGER
        self.store = ContractStore()
        self.target = SnowflakeTarget()

    # ------------------------------------------------------------- registry
    @property
    def registry(self) -> ProductRegistry:
        return load_products()

    def list_products(self) -> list[DataProduct]:
        registry = self.registry
        return [registry.get(product_id) for product_id in registry.ids()]

    def get(self, product_id: str) -> DataProduct:
        return self.registry.get(product_id)

    def status(self, product: DataProduct) -> Lifecycle:
        return self.ledger.current_status(product)

    def history(self, product_id: str) -> list[ApprovalEvent]:
        return self.ledger.for_product(product_id)

    # ------------------------------------------------------------- contracts
    def contract(self, product_id: str) -> DataContract:
        return build_contract(self.get(product_id), default_model(), default_registry())

    def validation(self, product_id: str) -> ContractValidation:
        return self.contract(product_id).validate_against(default_model(), default_registry())

    def diff(self, product_id: str) -> tuple[ContractDiff | None, str | None]:
        """The diff against the last published contract.

        Returns ``(diff, None)`` when there is one, ``(None, reason)`` when there
        is no previous published version, and raises
        :class:`~snowobs_dataproducts.contracts.BreakingChangeError` when the
        declared version is too small for what changed (§13.3).
        """
        product = self.get(product_id)
        previous = self.store.latest_before(product_id, product.version)
        if previous is None:
            return None, (
                f"{product_id} {product.version} has no earlier published contract to "
                f"compare against"
            )
        return self.workflow(product_id).contract_diff(), None

    def diff_or_error(self, product_id: str) -> tuple[ContractDiff | None, str | None]:
        """Like :meth:`diff`, but surfacing the refusal's own diff for display."""
        try:
            return self.diff(product_id)
        except BreakingChangeError as exc:
            return exc.contract_diff, str(exc.detail or exc.title)

    # ------------------------------------------------------------- workflow
    def workflow(self, product_id: str) -> PublishWorkflow:
        return PublishWorkflow(
            self.get(product_id),
            default_model(),
            default_registry(),
            self.store,
            self.ledger,
            target=self.target,
        )

    def preflight(self, product_id: str) -> PreflightReport:
        return self.workflow(product_id).preflight()

    def transition(
        self, product_id: str, to_status: Lifecycle, *, actor: str, reason: str
    ) -> ApprovalEvent:
        return self.workflow(product_id).transition(to_status, actor=actor, reason=reason)

    def publish(self, product_id: str, *, actor: str, reason: str) -> PublicationBundle:
        return self.workflow(product_id).publish(actor=actor, reason=reason)

    def bundle(self, product_id: str) -> PublicationBundle | None:
        """The bundle for an already-published product, rebuilt from its approval.

        Returns ``None`` when the product has not been published in this process:
        a bundle without a recorded approval behind it is exactly what R8 exists
        to prevent, so the API refuses rather than generating one on demand.
        """
        workflow = self.workflow(product_id)
        approvals = [
            event
            for event in self.ledger.for_product(product_id)
            if event.to_status is Lifecycle.PUBLISHED
        ]
        if not approvals:
            return None
        approval = approvals[-1]
        report = workflow.preflight()
        contract = workflow.contract()
        return PublicationBundle(
            product_id=product_id,
            version=str(workflow.product.version),
            files=workflow.bundle(contract, approval, report),
            report=report,
            approval=approval,
            contract=contract,
        )
