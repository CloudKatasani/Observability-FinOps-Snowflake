"""Data product registry, contracts, and the approval-gated publish flow (§15).

The privileged endpoints here follow the same shape as `connections.py`: the
platform generates artifacts for a human to review and run, and never executes
anything against a customer account itself. What is added on top is the approval
record — an actor, a timestamp, and a reason on every transition — because a
data product publication is a change to somebody else's Snowflake account, and
R8 says an agent proposes while a human disposes.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, Path
from pydantic import BaseModel, Field

from snowobs_api.deps import SettingsDep
from snowobs_api.services.products import ProductService
from snowobs_common.errors import AppError
from snowobs_dataproducts.contracts import contract_dict
from snowobs_dataproducts.model import Lifecycle
from snowobs_dataproducts.publish import ApprovalEvent, PreflightReport, PublicationBundle

router = APIRouter(prefix="/api/v1/products", tags=["products"])

ProductId = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{2,63}$")]


class ApproverIdentityError(AppError):
    """The caller did not present the identity every approval must record."""

    status_code = 401
    title = "Approver identity required"
    problem_type = "https://snowobs.dev/problems/approver-identity"


class BundleUnavailableError(AppError):
    """A bundle was requested for a product that has not been published."""

    status_code = 409
    title = "No published bundle"
    problem_type = "https://snowobs.dev/problems/bundle-unavailable"


# ------------------------------------------------------------------ schemas
class ConsumerResponse(BaseModel):
    name: str
    contact: str
    purpose: str
    grantee: str | None


class ChangeLogResponse(BaseModel):
    version: str
    released_on: str
    summary: str
    breaking: bool
    migration_note: str | None


class ProductSummary(BaseModel):
    id: str
    name: str
    version: str
    owner: str
    domain: str
    description: str
    #: The status after replaying the recorded transitions, not the YAML default.
    status: str
    classification: str
    metrics: list[str]
    dataset_count: int
    freshness_guarantee_minutes: int
    freshness_target_minutes: int
    availability_pct: str
    retention_days: int
    support_channel: str
    documentation_url: str
    refresh_cron: str
    consumer_count: int


class ProductDetail(ProductSummary):
    dimensions: list[str]
    sensitive_columns: list[str]
    search_column: str | None
    categories: list[str]
    consumers: list[ConsumerResponse]
    change_log: list[ChangeLogResponse]
    #: Registered source ids behind the product, for lineage (§13.1).
    sources: list[str]
    #: Drift findings against the current semantic layer; empty means clean.
    contract_findings: list[str]


class ContractColumnResponse(BaseModel):
    name: str
    type: str
    nullable: bool
    description: str
    metric_id: str | None
    unit: str | None
    sensitive: bool
    searchable: bool


class ContractDatasetResponse(BaseModel):
    name: str
    entity: str
    description: str
    grain: list[str]
    time_grain: str | None
    freshness_minutes: int
    expected_min_rows_per_day: int
    expected_max_rows_per_day: int | None
    sources: list[str]
    columns: list[ContractColumnResponse]


class ContractResponse(BaseModel):
    product_id: str
    version: str
    owner: str
    classification: str
    freshness_guarantee_minutes: int
    availability_pct: str
    retention_days: int
    support_channel: str
    deprecation_notice_days: int
    breaking_change_policy: str
    datasets: list[ContractDatasetResponse]
    #: Empty when the contract still matches what the semantic layer produces.
    findings: list[str]


class ContractChangeResponse(BaseModel):
    kind: str
    severity: str
    path: str
    detail: str
    before: str | None
    after: str | None


class ContractDiffResponse(BaseModel):
    product_id: str
    #: Null when this is the product's first version — an explicit "no baseline",
    #: never an empty diff pretending nothing changed (R3).
    baseline_version: str | None
    target_version: str
    changes: list[ContractChangeResponse]
    breaking_count: int
    required_bump: str
    declared_bump: str
    version_sufficient: bool
    release_notes: str | None
    #: Set when the declared version is too small for what changed (§13.3).
    refusal: str | None


class PreflightCheckResponse(BaseModel):
    name: str
    status: str
    detail: str


class PreflightResponse(BaseModel):
    product_id: str
    version: str
    passed: bool
    summary: str
    checks: list[PreflightCheckResponse]


class ApprovalRequest(BaseModel):
    """Why this transition should happen. The actor comes from the caller identity."""

    reason: str = Field(min_length=1, max_length=2000)


class TransitionResponse(BaseModel):
    product_id: str
    version: str
    from_status: str
    to_status: str
    actor: str
    at: datetime
    reason: str
    diff_summary: str | None


class BundleResponse(BaseModel):
    product_id: str
    version: str
    approval: TransitionResponse
    preflight: PreflightResponse
    file_names: list[str]
    #: The artifacts themselves. Text only — the platform never applies them.
    files: dict[str, str]
    validation_checklist: list[str]


# ------------------------------------------------------------------ helpers
def _approver(actor: str | None) -> str:
    """The identity every approval record carries.

    The header is how the API surfaces the approving human until OIDC lands
    (§17). A transition without one is refused rather than attributed to
    "system", because an audit record nobody signed is not an audit record.
    """
    if not actor or not actor.strip():
        raise ApproverIdentityError(
            "Set the X-Snowobs-Actor header to the person making this decision; "
            "an approval must name a human (R8)"
        )
    return actor.strip()


ActorHeader = Annotated[str | None, Header(alias="X-Snowobs-Actor")]


def _summary(service: ProductService, product_id: str) -> ProductSummary:
    product = service.get(product_id)
    contract = service.contract(product_id)
    return ProductSummary(
        id=product.id,
        name=product.name,
        version=str(product.version),
        owner=product.owner,
        domain=product.domain,
        description=" ".join(product.description.split()),
        status=service.status(product).value,
        classification=product.classification.value,
        metrics=list(product.metrics),
        dataset_count=len(contract.datasets),
        freshness_guarantee_minutes=contract.freshness_guarantee_minutes,
        freshness_target_minutes=product.sla.freshness_target_minutes,
        availability_pct=str(contract.availability_pct),
        retention_days=contract.retention_days,
        support_channel=contract.support_channel,
        documentation_url=product.documentation_url,
        refresh_cron=product.refresh.cron,
        consumer_count=len(product.consumers),
    )


def _transition_response(event: ApprovalEvent) -> TransitionResponse:
    return TransitionResponse(
        product_id=event.product_id,
        version=event.version,
        from_status=event.from_status.value,
        to_status=event.to_status.value,
        actor=event.actor,
        at=event.at,
        reason=event.reason,
        diff_summary=event.diff_summary,
    )


def _preflight_response(report: PreflightReport) -> PreflightResponse:
    return PreflightResponse(
        product_id=report.product_id,
        version=report.version,
        passed=report.passed,
        summary=report.summary(),
        checks=[
            PreflightCheckResponse(name=c.name, status=c.status.value, detail=c.detail)
            for c in report.checks
        ],
    )


def _bundle_response(service: ProductService, bundle: PublicationBundle) -> BundleResponse:
    workflow = service.workflow(bundle.product_id)
    return BundleResponse(
        product_id=bundle.product_id,
        version=bundle.version,
        approval=_transition_response(bundle.approval),
        preflight=_preflight_response(bundle.report),
        file_names=bundle.names,
        files=bundle.files,
        validation_checklist=workflow.validation_checklist(bundle.contract),
    )


# ------------------------------------------------------------------ routes
@router.get("", response_model=list[ProductSummary])
async def list_products(settings: SettingsDep) -> list[ProductSummary]:
    """The internal catalogue: every registered data product (§13.5)."""
    service = ProductService(settings)
    return [_summary(service, product.id) for product in service.list_products()]


@router.get("/{product_id}", response_model=ProductDetail)
async def get_product(product_id: ProductId, settings: SettingsDep) -> ProductDetail:
    """One product, with its consumers, change history, and any contract drift."""
    service = ProductService(settings)
    product = service.get(product_id)
    contract = service.contract(product_id)
    validation = service.validation(product_id)
    return ProductDetail(
        **_summary(service, product_id).model_dump(),
        dimensions=list(product.dimensions),
        sensitive_columns=list(product.sensitive_columns),
        search_column=product.search.column if product.search else None,
        categories=list(product.categories),
        consumers=[
            ConsumerResponse(
                name=c.name,
                contact=c.contact,
                purpose=" ".join(c.purpose.split()),
                grantee=c.grantee,
            )
            for c in product.consumers
        ],
        change_log=[
            ChangeLogResponse(
                version=str(entry.version),
                released_on=entry.released_on,
                summary=" ".join(entry.summary.split()),
                breaking=entry.breaking,
                migration_note=(
                    " ".join(entry.migration_note.split()) if entry.migration_note else None
                ),
            )
            for entry in product.change_log
        ],
        sources=contract.sources,
        contract_findings=[f"{f.path}: {f.detail}" for f in validation.findings],
    )


@router.get("/{product_id}/contract", response_model=ContractResponse)
async def get_contract(product_id: ProductId, settings: SettingsDep) -> ContractResponse:
    """The product's contract, derived from the live semantic layer (§13.2)."""
    service = ProductService(settings)
    contract = service.contract(product_id)
    validation = service.validation(product_id)
    payload = contract_dict(contract)
    return ContractResponse(
        product_id=contract.product_id,
        version=str(contract.version),
        owner=contract.owner,
        classification=contract.classification.value,
        freshness_guarantee_minutes=contract.freshness_guarantee_minutes,
        availability_pct=str(contract.availability_pct),
        retention_days=contract.retention_days,
        support_channel=contract.support_channel,
        deprecation_notice_days=contract.deprecation_notice_days,
        breaking_change_policy=contract.breaking_change_policy,
        datasets=[ContractDatasetResponse.model_validate(d) for d in payload["datasets"]],
        findings=[f"{f.path}: {f.detail}" for f in validation.findings],
    )


@router.get("/{product_id}/diff", response_model=ContractDiffResponse)
async def get_diff(product_id: ProductId, settings: SettingsDep) -> ContractDiffResponse:
    """The contract diff against the previous published version (§13.3).

    A version bump too small for what changed is reported here as a refusal with
    the offending changes attached, rather than as a silent pass.
    """
    service = ProductService(settings)
    product = service.get(product_id)
    computed, refusal = service.diff_or_error(product_id)
    if computed is None:
        return ContractDiffResponse(
            product_id=product_id,
            baseline_version=None,
            target_version=str(product.version),
            changes=[],
            breaking_count=0,
            required_bump="none",
            declared_bump="none",
            version_sufficient=True,
            release_notes=None,
            refusal=refusal,
        )
    return ContractDiffResponse(
        product_id=computed.product_id,
        baseline_version=str(computed.baseline_version),
        target_version=str(computed.target_version),
        changes=[
            ContractChangeResponse(
                kind=change.kind.value,
                severity=change.severity.value,
                path=change.path,
                detail=change.detail,
                before=change.before,
                after=change.after,
            )
            for change in computed.changes
        ],
        breaking_count=len(computed.breaking),
        required_bump=computed.required_bump.value,
        declared_bump=computed.declared_bump.value,
        version_sufficient=computed.is_version_sufficient,
        release_notes=computed.release_notes(),
        refusal=refusal,
    )


@router.get("/{product_id}/preflight", response_model=PreflightResponse)
async def get_preflight(product_id: ProductId, settings: SettingsDep) -> PreflightResponse:
    """Run the publish gates without publishing — the wizard's checklist (§13.4)."""
    return _preflight_response(ProductService(settings).preflight(product_id))


@router.get("/{product_id}/bundle", response_model=BundleResponse)
async def get_bundle(product_id: ProductId, settings: SettingsDep) -> BundleResponse:
    """Download the artifact bundle for a published product.

    Refuses for a product with no recorded publication: a bundle implies an
    approved release, and generating one on demand would let the artifacts
    circulate without the approval that authorised them (R8).
    """
    bundle = ProductService(settings).bundle(product_id)
    if bundle is None:
        raise BundleUnavailableError(
            f"{product_id} has no recorded publication in this deployment; approve and "
            f"publish it first"
        )
    return _bundle_response(ProductService(settings), bundle)


@router.post("/{product_id}/propose", response_model=TransitionResponse)
async def propose(
    product_id: ProductId,
    payload: ApprovalRequest,
    settings: SettingsDep,
    x_snowobs_actor: ActorHeader = None,
) -> TransitionResponse:
    """Move a draft into review. Recorded like every other transition."""
    service = ProductService(settings)
    event = service.transition(
        product_id, Lifecycle.PROPOSED, actor=_approver(x_snowobs_actor), reason=payload.reason
    )
    return _transition_response(event)


@router.post("/{product_id}/approve", response_model=TransitionResponse)
async def approve(
    product_id: ProductId,
    payload: ApprovalRequest,
    settings: SettingsDep,
    x_snowobs_actor: ActorHeader = None,
) -> TransitionResponse:
    """Record a human's approval of a proposed product (R8).

    Approval is not publication: it authorises the artifacts to be generated, and
    a separate, separately-recorded act publishes them.
    """
    service = ProductService(settings)
    event = service.transition(
        product_id, Lifecycle.APPROVED, actor=_approver(x_snowobs_actor), reason=payload.reason
    )
    return _transition_response(event)


@router.post("/{product_id}/publish", response_model=BundleResponse)
async def publish(
    product_id: ProductId,
    payload: ApprovalRequest,
    settings: SettingsDep,
    x_snowobs_actor: ActorHeader = None,
) -> BundleResponse:
    """Publish an approved product and return its artifact bundle.

    Runs every preflight gate first and refuses with the specific failing check.
    Nothing is executed against Snowflake: the response is the set of scripts a
    human runs, plus the validation checklist to tick off afterwards (R2, R8).
    """
    service = ProductService(settings)
    bundle = service.publish(product_id, actor=_approver(x_snowobs_actor), reason=payload.reason)
    return _bundle_response(service, bundle)


@router.post("/{product_id}/deprecate", response_model=TransitionResponse)
async def deprecate(
    product_id: ProductId,
    payload: ApprovalRequest,
    settings: SettingsDep,
    x_snowobs_actor: ActorHeader = None,
) -> TransitionResponse:
    """Start the deprecation window for a published product (§13.3)."""
    service = ProductService(settings)
    event = service.transition(
        product_id, Lifecycle.DEPRECATED, actor=_approver(x_snowobs_actor), reason=payload.reason
    )
    return _transition_response(event)


@router.get("/{product_id}/history", response_model=list[TransitionResponse])
async def history(product_id: ProductId, settings: SettingsDep) -> list[TransitionResponse]:
    """Every recorded transition for this product, oldest first (R8)."""
    service = ProductService(settings)
    service.get(product_id)  # 404s for an unknown product before returning an empty list
    return [_transition_response(event) for event in service.history(product_id)]
