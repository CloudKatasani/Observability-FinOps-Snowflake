"""Liveness and readiness endpoints (BUILD_PROMPT §18).

``/healthz`` answers "is the process up" and never touches dependencies.
``/readyz`` answers "can this instance serve traffic" — it verifies the backing
services this deployment genuinely requires and reports each component,
returning 503 while any *required* dependency is unreachable.

"Genuinely requires" is configuration (`ReadinessSettings`), not a constant.
The API's only consumer of Postgres and Redis today is this file: the query
cache is in-process, nothing reads the metadata database yet (A-16, A-18), and
Redis is the worker's queue. A deployment that provides neither — the
all-in-one demo run from a checkout, which starts no containers — is fully
functional, and calling it `not_ready` reported a failure the user could not
act on and did not have.

A component that is not required is reported as `not_required` with the reason,
and is not probed. The two honest states for something unchecked are "not used
here" and silence; a green tick would be the same lie as the red cross, in the
opposite direction.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any, Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from snowobs_api.deps import DbEngineDep, RedisDep, SettingsDep
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
    status: Literal["ok", "unavailable", "not_required"]
    #: Whether this deployment needs the component to serve traffic. Only a
    #: required component can make the instance `not_ready`.
    required: bool = True
    #: The error type for a failure, or why the component is not required.
    #: Never the exception's message: a connection error can carry a host, a
    #: port, or a user name, and readiness is an unauthenticated endpoint.
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


#: Why each component may be absent, so "not_required" carries its reason to
#: the status page rather than leaving a reader to wonder what is switched off.
_NOT_REQUIRED_DETAIL = {
    "postgres": (
        "Not used by this deployment — app metadata has no durable store yet "
        "(see ASSUMPTIONS A-16, A-18). Set READINESS__REQUIRE_POSTGRES=true to gate on it."
    ),
    "redis": (
        "Not used by this deployment — Redis is the background worker's queue, and "
        "this process runs no worker. Set READINESS__REQUIRE_REDIS=true to gate on it."
    ),
}


def _skipped(name: str) -> ComponentStatus:
    return ComponentStatus(
        name=name, status="not_required", required=False, detail=_NOT_REQUIRED_DETAIL[name]
    )


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def readyz(
    settings: SettingsDep, engine: DbEngineDep, redis: RedisDep, response: Response
) -> ReadinessResponse:
    checks: list[Coroutine[Any, Any, ComponentStatus]] = []
    if settings.readiness.require_postgres:
        checks.append(_check_database(engine))
    if settings.readiness.require_redis:
        checks.append(_check_redis(redis))
    probed = {c.name: c for c in await asyncio.gather(*checks)}

    # Reported in a fixed order so the status page does not reshuffle itself
    # between polls, and so a component that is switched off keeps its place
    # rather than vanishing from the list.
    components = [probed.get(name) or _skipped(name) for name in ("postgres", "redis")]
    ready = all(c.status == "ok" for c in components if c.required)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ready" if ready else "not_ready", components=components)
