"""Metric catalogue, query, and dashboard-tile endpoints (BUILD_PROMPT §15).

Every response carries `as_of`, `latency_floor_minutes`, `provisional`, and
`sources[]` for any figure, plus the compiled SQL — "show the SQL" is a
first-class affordance, not a debug feature (R5).
"""

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from snowobs_api.deps import SettingsDep
from snowobs_api.services.metrics import MetricService
from snowobs_common.errors import AppError
from snowobs_semantics.compiler import Filter, MetricRequest, Order, TimeRange
from snowobs_semantics.model import TimeGrain

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
    sql: str


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
    sql: str
    allocation_method: str | None = None
    unavailable_reason: str | None = None


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
        series = MetricService(settings).query(payload.to_metric_request())
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
        sql=series.sql,
    )


@router.get("/{metric_id}/tile", response_model=MetricTileResponse)
async def metric_tile(
    metric_id: str,
    settings: SettingsDep,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
) -> MetricTileResponse:
    """A single-figure tile. A missing source explains itself rather than showing 0."""
    service = MetricService(settings)
    # A tile is one figure for the whole period, not the largest day in it.
    request = MetricQueryRequest(
        metrics=[metric_id], start=start, end=end, limit=1, bucket_time=False
    )
    value = service.tile(metric_id, request.to_metric_request())
    return MetricTileResponse(**value.__dict__)
