"""Chargeback allocation and the reconciliation gate (BUILD_PROMPT §15, §10).

Allocated figures are only published behind a green gate (R6). This router
exposes the gate's verdict alongside the numbers so a caller cannot present
chargeback without also seeing whether it reconciles.
"""

from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from snowobs_api.deps import SettingsDep
from snowobs_api.services.chargeback import ChargebackService

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


@router.get("/allocation", response_model=AllocationResponse)
async def allocation(
    settings: SettingsDep,
    start: date = Query(...),
    end: date = Query(...),
) -> AllocationResponse:
    """Fully allocated cost by team, with the reconciliation verdict."""
    return ChargebackService(settings).allocation_response(start, end)


@router.get("/reconciliation/{usage_date}", response_model=ReconciliationResponse)
async def reconciliation(settings: SettingsDep, usage_date: date) -> ReconciliationResponse:
    """The stored reconciliation for one day — the artifact finance asks for."""
    return ChargebackService(settings).reconciliation_response(usage_date, usage_date)


def _decimal(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None
