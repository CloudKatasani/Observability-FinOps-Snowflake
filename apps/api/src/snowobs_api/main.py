"""API application factory.

Startup: validate settings, configure structured logging, create the shared
database engine and Redis client. Errors surface as RFC 7807
``application/problem+json`` documents (BUILD_PROMPT §15).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import create_async_engine

from snowobs_api.routers import datasets, health, meta
from snowobs_common import __version__
from snowobs_common.config import Settings, load_settings
from snowobs_common.errors import AppError
from snowobs_common.logging import configure_logging, get_logger

logger = get_logger(__name__)

PROBLEM_JSON = "application/problem+json"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    app.state.db_engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    app.state.redis = Redis.from_url(settings.redis_url)
    logger.info("api_started", version=__version__, mode=settings.mode)
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await app.state.db_engine.dispose()
        logger.info("api_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings if settings is not None else load_settings()
    configure_logging(json_output=resolved.log_json, level=logging.INFO)

    app = FastAPI(
        title="Observability & FinOps Platform for Snowflake — API",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = resolved

    @app.middleware("http")
    async def trace_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        trace_id = request.headers.get("x-request-id", uuid4().hex)
        structlog.contextvars.bind_contextvars(trace_id=trace_id)
        try:
            response: Response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("trace_id")
        response.headers["x-request-id"] = trace_id
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        problem = exc.to_problem(instance=str(request.url.path))
        return JSONResponse(
            status_code=problem.status,
            content=problem.model_dump(exclude_none=True),
            media_type=PROBLEM_JSON,
        )

    app.include_router(health.router)
    app.include_router(meta.router)
    app.include_router(datasets.router)
    return app


app = create_app()
