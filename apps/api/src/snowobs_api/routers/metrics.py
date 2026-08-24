"""Metric catalogue, query, and dashboard-tile endpoints (BUILD_PROMPT §15).

Every response carries `as_of`, `latency_floor_minutes`, `provisional`, and
`sources[]` for any figure, plus the compiled SQL — "show the SQL" is a
first-class affordance, not a debug feature (R5).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from snowobs_api.deps import SettingsDep
from snowobs_api.services.engines import resolve_mode
from snowobs_api.services.metrics import MetricService
from snowobs_common.errors import AppError
from snowobs_semantics.compiler import Filter, MetricRequest, Order, TimeRange
from snowobs_semantics.model import TimeGrain
from snowobs_semantics.scope import Scope, ScopeRequest

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])


class MetricCatalogEntry(BaseModel):
    id: str
    name: str
    domain: str
    description: str
    format_type: str
    unit: str | None
    direction: str
    grain: str
    dimensions: list[str]
    synonyms: list[str]
    requires_sources: list[str]
    latency_floor_minutes: int
    allocation_method: str | None
    owner: str
    verified_queries: list[str]


class MetricQueryRequest(BaseModel):
    """The API shape of a `MetricRequest` (§15)."""

    metrics: list[str] = Field(min_length=1)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)
    start: date | None = None
    end: date | None = None
    grain: TimeGrain | None = None
    limit: int = 1000
    order: list[Order] = Field(default_factory=list)
    bucket_time: bool = True
    #: Organization-wide, or one account. Defaults to the organization, which
    #: for a single-account deployment is the same thing.
    scope: Scope = Scope.ORGANIZATION
    account: str | None = None

    @model_validator(mode="after")
    def _account_scope_names_an_account(self) -> MetricQueryRequest:
        if self.scope is Scope.ACCOUNT and not self.account:
            raise ValueError("scope=account requires an account name")
        return self

    def scope_request(self) -> ScopeRequest:
        return ScopeRequest(scope=self.scope, account=self.account)

    def to_metric_request(self) -> MetricRequest:
        time_range = TimeRange(start=self.start, end=self.end) if self.start and self.end else None
        return MetricRequest(
            metrics=self.metrics,
            dimensions=self.dimensions,
            filters=self.filters,
            time_range=time_range,
            grain=self.grain,
            limit=self.limit,
            order=self.order,
            bucket_time=self.bucket_time,
            account=self.scope_request().account_filter,
        )


class MetricQueryResponse(BaseModel):
    metrics: list[str]
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    as_of: datetime
    latency_floor_minutes: int
    provisional: bool
    sources: list[str]
    #: R7: the sources that gate completeness, which can be narrower than
    #: `sources` when an entity view joins a slow column this query never
    #: selects. A client should judge freshness from these, not from `sources`.
    gating_sources: list[str]
    sql: str
    #: Where this figure was computed, and over which accounts.
    scope: str
    scope_account: str | None
    scope_partial: bool
    contributing_accounts: list[str]
    #: Accounts billing knows about that have landed nothing at account level.
    missing_accounts: list[str] = Field(default_factory=list)


class MetricTileResponse(BaseModel):
    metric_id: str
    name: str
    value: Any
    format_type: str
    format_decimals: int
    unit: str | None
    direction: str
    as_of: datetime
    latency_floor_minutes: int
    provisional: bool
    sources: list[str]
    gating_sources: list[str]
    sql: str
    scope: str
    scope_account: str | None
    scope_partial: bool
    contributing_accounts: list[str]
    missing_accounts: list[str] = Field(default_factory=list)
    allocation_method: str | None = None
    unavailable_reason: str | None = None


class AccountScopeOption(BaseModel):
    """One entry in the scope selector."""

    #: "organization", or the account's name.
    value: str
    label: str
    scope: str
    #: How many of the catalogue's KPIs can be answered at this scope. An
    #: enterprise picking a sandbox account should see immediately that most of
    #: the catalogue has nothing to show there yet.
    answerable_metrics: int
    total_metrics: int


class ScopeOptionsResponse(BaseModel):
    mode: str
    organization: str | None
    options: list[AccountScopeOption]


@router.get("/scopes", response_model=ScopeOptionsResponse)
async def scope_options(settings: SettingsDep) -> ScopeOptionsResponse:
    """The scopes this deployment can answer at, and how much each can answer.

    Drives the organization/account filter. The per-scope count is the honest
    part: selecting an account that has only had its billing uploaded should
    visibly narrow the catalogue rather than silently returning blanks.
    """
    service = MetricService(settings)
    catalogue = service.catalog_entries()
    accounts = service.landed_accounts()

    def answerable(request: ScopeRequest) -> int:
        return sum(1 for metric in catalogue if service.scope_verdict(metric.id, request).available)

    options = [
        AccountScopeOption(
            value=Scope.ORGANIZATION.value,
            label="Organization",
            scope=Scope.ORGANIZATION.value,
            answerable_metrics=answerable(ScopeRequest()),
            total_metrics=len(catalogue),
        )
    ]
    options += [
        AccountScopeOption(
            value=account,
            label=account,
            scope=Scope.ACCOUNT.value,
            answerable_metrics=answerable(ScopeRequest(scope=Scope.ACCOUNT, account=account)),
            total_metrics=len(catalogue),
        )
        for account in accounts
    ]
    return ScopeOptionsResponse(
        mode=resolve_mode(settings),
        organization=settings.snowflake.organization,
        options=options,
    )


@router.get("/catalog", response_model=list[MetricCatalogEntry])
async def metric_catalog(settings: SettingsDep) -> list[MetricCatalogEntry]:
    """The governed metric catalogue — what the platform can answer."""
    return [
        MetricCatalogEntry(
            id=metric.id,
            name=metric.name,
            domain=metric.domain,
            description=metric.description.strip(),
            format_type=metric.format.type.value,
            unit=metric.format.unit,
            direction=metric.direction.value,
            grain=metric.grain.value,
            dimensions=metric.dimensions,
            synonyms=metric.synonyms,
            requires_sources=metric.requires_sources,
            latency_floor_minutes=metric.latency_floor_minutes,
            allocation_method=metric.allocation_method,
            owner=metric.owner,
            verified_queries=metric.verified_queries,
        )
        for metric in MetricService(settings).catalog_entries()
    ]


@router.post("/query", response_model=MetricQueryResponse)
async def query_metrics(payload: MetricQueryRequest, settings: SettingsDep) -> MetricQueryResponse:
    """Run a governed metric query and return rows with their provenance."""
    try:
        series = MetricService(settings).query(payload.to_metric_request(), payload.scope_request())
    except AppError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return MetricQueryResponse(
        metrics=series.metrics,
        columns=series.columns,
        rows=series.rows,
        row_count=series.row_count,
        truncated=series.truncated,
        as_of=series.as_of,
        latency_floor_minutes=series.latency_floor_minutes,
        provisional=series.provisional,
        sources=series.sources,
        gating_sources=series.gating_sources,
        sql=series.sql,
        scope=series.scope,
        scope_account=series.scope_account,
        scope_partial=series.scope_partial,
        contributing_accounts=series.contributing_accounts,
        missing_accounts=series.missing_accounts,
    )


@router.get("/{metric_id}/tile", response_model=MetricTileResponse)
async def metric_tile(
    metric_id: str,
    settings: SettingsDep,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    scope: Scope = Query(default=Scope.ORGANIZATION),
    account: str | None = Query(default=None),
) -> MetricTileResponse:
    """A single-figure tile. A missing source explains itself rather than showing 0."""
    service = MetricService(settings)
    # A tile is one figure for the whole period, not the largest day in it.
    request = MetricQueryRequest(
        metrics=[metric_id],
        start=start,
        end=end,
        limit=1,
        bucket_time=False,
        scope=scope,
        account=account,
    )
    value = service.tile(metric_id, request.to_metric_request(), request.scope_request())
    return MetricTileResponse(**value.__dict__)
