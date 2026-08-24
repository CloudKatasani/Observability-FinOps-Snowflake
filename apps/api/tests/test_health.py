"""Health endpoint behaviour, including readiness against unreachable backends."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI

from snowobs_api.main import create_app
from snowobs_common import __version__
from snowobs_common.config import Settings

UNREACHABLE = Settings(
    _env_file=None,
    database_url="postgresql+asyncpg://u:p@127.0.0.1:59999/none",
    redis_url="redis://127.0.0.1:59998/0",
)


@asynccontextmanager
async def running_app(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.mark.asyncio
async def test_healthz_is_up_without_dependencies() -> None:
    async with running_app(UNREACHABLE) as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__


@pytest.mark.asyncio
async def test_readyz_reports_unavailable_components_as_503() -> None:
    async with running_app(UNREACHABLE) as client:
        response = await client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    statuses = {c["name"]: c["status"] for c in body["components"]}
    assert statuses == {"postgres": "unavailable", "redis": "unavailable"}


@pytest.mark.asyncio
async def test_meta_serves_branding_from_config() -> None:
    async with running_app(UNREACHABLE) as client:
        response = await client.get("/api/v1/meta")
    assert response.status_code == 200
    body = response.json()
    assert body["branding"]["short_name"] == "snowobs"
    assert body["branding"]["display_name"]
    assert body["mode"] == "auto"


@pytest.mark.asyncio
async def test_responses_carry_request_id_header() -> None:
    async with running_app(UNREACHABLE) as client:
        response = await client.get("/healthz", headers={"x-request-id": "trace-42"})
    assert response.headers["x-request-id"] == "trace-42"
