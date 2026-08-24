"""Chargeback allocation and the reconciliation gate (BUILD_PROMPT §15, §10).

Allocated figures are only published behind a green gate (R6). This router
exposes the gate's verdict alongside the numbers so a caller cannot present
chargeback without also seeing whether it reconciles.
"""

from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from snowobs_api.deps import SettingsDep
from snowobs_api.services.chargeback import ChargebackService
from snowobs_semantics.scope import Scope, ScopeRequest

router = APIRouter(prefix="/api/v1/chargeback", tags=["chargeback"])


class TeamCost(BaseModel):
    team: str
    direct_credits: str
    idle_credits: str
    cloud_services_credits: str
    total_credits: str
    cost_usd: str | None
    share_of_total: str


class ReconciliationResponse(BaseModel):
    outcome: str
    allocated_credits: str
    metered_credits: str
    variance_credits: str
    variance_pct: str | None
    tolerance_pct: str
    publication_allowed: bool
    banner: str
    ran_at: datetime
    worst_days: list[dict[str, str | None]]


class SqlDisclosure(BaseModel):
    """One statement behind the allocation, and what it contributes (R5)."""

    purpose: str
    metrics: list[str]
    dimensions: list[str]
    sql: str


class AllocationResponse(BaseModel):
    period_start: date
    period_end: date
    mode: str
    teams: list[TeamCost]
    unattributed_share: str
    credit_price_usd: str | None
    reconciliation: ReconciliationResponse
    #: R6: chargeback figures are withheld when the gate is red.
    figures_published: bool
    as_of: datetime
    #: §15 provenance. `provisional` is true whenever any input is still inside
    #: its restatement window, so a caller is never told a figure is settled
    #: because the allocation flattened three queries into one answer.
    provisional: bool
    latency_floor_minutes: int
    sources: list[str]
    #: R5: an allocation is three queries, and all three are shown.
    sql: list[SqlDisclosure]
    #: Where these figures were computed, in the same shape the metric
    #: endpoints report it — so a page showing a KPI tile and a chargeback
    #: table side by side can prove both are talking about the same accounts.
    scope: str
    scope_account: str | None
    scope_partial: bool
    contributing_accounts: list[str]
    #: Accounts billing knows about that have landed nothing at account level.
    #: An organization-wide chargeback missing one of these is under-counted,
    #: and says so rather than presenting the shortfall as the whole bill.
    missing_accounts: list[str] = Field(default_factory=list)


def _scope_request(scope: Scope, account: str | None) -> ScopeRequest:
    if scope is Scope.ACCOUNT and not account:
        raise HTTPException(status_code=422, detail="scope=account requires an account name")
    return ScopeRequest(scope=scope, account=account)


@router.get("/allocation", response_model=AllocationResponse)
async def allocation(
    settings: SettingsDep,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    scope: Scope = Query(default=Scope.ORGANIZATION),
    account: str | None = Query(default=None),
) -> AllocationResponse:
    """Fully allocated cost by team, with the reconciliation verdict.

    Omitting the dates allocates the whole landed window, matching the metric
    endpoints. The response always echoes `period_start` and `period_end`, so a
    default is reported rather than assumed — a caller never has to know the
    data window before it can ask a question about it.

    `scope` narrows the allocation the same way the KPI endpoints narrow a
    tile: `organization` allocates every account together, `account` allocates
    one — including its reconciliation, which is checked against that account's
    bill rather than the organization's.
    """
    return ChargebackService(settings).allocation_response(
        start, end, _scope_request(scope, account)
    )


@router.get("/reconciliation/{usage_date}", response_model=ReconciliationResponse)
async def reconciliation(
    settings: SettingsDep,
    usage_date: date,
    scope: Scope = Query(default=Scope.ORGANIZATION),
    account: str | None = Query(default=None),
) -> ReconciliationResponse:
    """The stored reconciliation for one day — the artifact finance asks for."""
    return ChargebackService(settings).reconciliation_response(
        usage_date, usage_date, _scope_request(scope, account)
    )
