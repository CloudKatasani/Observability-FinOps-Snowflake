"""Liveness and readiness endpoints (BUILD_PROMPT §18).

``/healthz`` answers "is the process up" and never touches dependencies.
``/readyz`` answers "can this instance serve traffic" — it verifies the backing
services this tier genuinely requires (Postgres, Redis) and reports each
component, returning 503 while any required dependency is unreachable.
"""

import asyncio
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from snowobs_api.deps import DbEngineDep, RedisDep
from snowobs_common import __version__
from snowobs_common.logging import get_logger

router = APIRouter(tags=["health"])
logger = get_logger(__name__)

_CHECK_TIMEOUT_S = 2.0


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str = __version__


class ComponentStatus(BaseModel):
    name: str
    status: Literal["ok", "unavailable"]
    detail: str | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    version: str = __version__
    components: list[ComponentStatus]


@router.get("/healthz", response_model=LivenessResponse)
async def healthz() -> LivenessResponse:
    return LivenessResponse()


async def _check_database(engine: AsyncEngine) -> ComponentStatus:
    try:
        async with asyncio.timeout(_CHECK_TIMEOUT_S):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        return ComponentStatus(name="postgres", status="ok")
    except Exception as exc:
        logger.warning("readiness_check_failed", component="postgres", error=str(exc))
        return ComponentStatus(name="postgres", status="unavailable", detail=type(exc).__name__)


async def _check_redis(redis: Redis) -> ComponentStatus:
    try:
        async with asyncio.timeout(_CHECK_TIMEOUT_S):
            await redis.ping()
        return ComponentStatus(name="redis", status="ok")
    except Exception as exc:
        logger.warning("readiness_check_failed", component="redis", error=str(exc))
        return ComponentStatus(name="redis", status="unavailable", detail=type(exc).__name__)


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def readyz(engine: DbEngineDep, redis: RedisDep, response: Response) -> ReadinessResponse:
    components = list(await asyncio.gather(_check_database(engine), _check_redis(redis)))
    ready = all(c.status == "ok" for c in components)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ready" if ready else "not_ready", components=components)
