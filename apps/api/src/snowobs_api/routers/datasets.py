"""Coverage matrix and extract-kit endpoints (BUILD_PROMPT §15)."""

from fastapi import APIRouter, Query
from pydantic import BaseModel

from snowobs_api.deps import SettingsDep
from snowobs_api.services.datasets import DatasetService
from snowobs_ingest.coverage import CoverageMatrix
from snowobs_semantics.registry import default_registry

router = APIRouter(prefix="/api/v1", tags=["datasets"])


class SourceSummary(BaseModel):
    id: str
    snowflake_object: str
    domain: str
    criticality: str
    documented_latency_minutes: int
    latency_verified: bool
    required_db_role: str | None


class ExtractKitResponse(BaseModel):
    files: dict[str, str]


@router.get("/sources", response_model=list[SourceSummary])
async def list_sources() -> list[SourceSummary]:
    """The canonical source registry — what the platform knows how to read."""
    return [
        SourceSummary(
            id=source.id,
            snowflake_object=source.snowflake_object,
            domain=source.domain,
            criticality=source.criticality.value,
            documented_latency_minutes=source.documented_latency_minutes,
            latency_verified=source.latency_verified,
            required_db_role=source.required_db_role,
        )
        for source in sorted(default_registry(), key=lambda s: (s.domain, s.id))
    ]


@router.get("/datasets/coverage", response_model=CoverageMatrix)
async def get_coverage(settings: SettingsDep) -> CoverageMatrix:
    """Per-source and per-KPI availability with remediation (R3)."""
    return DatasetService(settings).coverage()


@router.get("/exports/extract-kit", response_model=ExtractKitResponse)
async def get_extract_kit(
    settings: SettingsDep,
    days: int = Query(default=120, ge=1, le=365),
    file_format: str = Query(default="PARQUET", pattern="^(CSV|PARQUET)$"),
) -> ExtractKitResponse:
    """The tailored extract kit an operator runs in their own account (§7.3)."""
    kit = DatasetService(settings).extract_kit(days=days, file_format=file_format)
    return ExtractKitResponse(files=kit.files)
