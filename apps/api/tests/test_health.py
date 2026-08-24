"""Health endpoint behaviour, including readiness against unreachable backends."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI

from snowobs_api.main import create_app
from snowobs_common import __version__
from snowobs_common.config import Settings

#: Backends that cannot be reached, *and* a deployment that says it needs them.
#: Both halves matter: an unreachable backend nothing requires is not a failure.
UNREACHABLE = Settings(
    _env_file=None,
    database_url="postgresql+asyncpg://u:p@127.0.0.1:59999/none",
    redis_url="redis://127.0.0.1:59998/0",
    readiness={"require_postgres": True, "require_redis": True},
)

#: The shipped default, and what `make demo-native` runs: no Postgres, no
#: Redis, and nothing in the API that reads either.
NO_BACKENDS = Settings(
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
async def test_a_deployment_that_uses_neither_backend_is_ready_without_them() -> None:
    """The demo run from a checkout starts no containers and needs none.

    Nothing in the API reads Postgres or Redis today — the query cache is
    in-process and Redis is the worker's queue — so an instance without them
    serves every page correctly. Reporting `not_ready` put two red crosses on
    the status page for services the deployment does not use, and returned 503
    from an endpoint a load balancer believes.
    """
    async with running_app(NO_BACKENDS) as client:
        response = await client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert {c["name"]: c["status"] for c in body["components"]} == {
        "postgres": "not_required",
        "redis": "not_required",
    }


@pytest.mark.asyncio
async def test_a_component_that_is_not_required_says_why_and_is_not_ticked() -> None:
    """Both wrong answers are available here, and the endpoint gives neither.

    A cross claims a failure that has not happened; a tick claims a check that
    never ran. `not_required` is the third state, and it carries the reason so
    a reader does not go hunting for a misconfiguration that is not there.
    """
    async with running_app(NO_BACKENDS) as client:
        body = (await client.get("/readyz")).json()

    for component in body["components"]:
        assert component["required"] is False
        assert component["status"] not in ("ok", "unavailable")
        assert component["detail"], "a skipped component must explain itself"
        # The remedy is named, so making it required again is not a code dive.
        assert "READINESS__REQUIRE_" in component["detail"]


@pytest.mark.asyncio
async def test_one_required_backend_still_gates_readiness() -> None:
    """The gate is per component, not all-or-nothing.

    A deployment that runs the worker needs Redis and still does not read
    Postgres; it must fail on the one it depends on and stay quiet about the
    other, or the flag is just a global off switch.
    """
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@127.0.0.1:59999/none",
        redis_url="redis://127.0.0.1:59998/0",
        readiness={"require_redis": True},
    )
    async with running_app(settings) as client:
        response = await client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert {c["name"]: c["status"] for c in body["components"]} == {
        "postgres": "not_required",
        "redis": "unavailable",
    }


@pytest.mark.asyncio
async def test_readiness_never_leaks_a_connection_string_to_an_open_endpoint() -> None:
    """`/readyz` is unauthenticated, and a driver's error text carries the host,
    the port, and often the user name it failed to authenticate.
    """
    async with running_app(UNREACHABLE) as client:
        raw = (await client.get("/readyz")).text

    for secret in ("59999", "59998", "127.0.0.1", "//u:p@"):
        assert secret not in raw


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
