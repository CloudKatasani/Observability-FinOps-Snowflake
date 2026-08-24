"""arq worker entry point (ADR-0002).

Job functions are registered explicitly — no dynamic discovery — so the job
surface is auditable. Phase 0 ships the harness plus a single functional job
(``ping``) used by self-diagnostics and the compose healthcheck; ingestion,
refresh, reconciliation, close, forecast, and eval jobs land with their
respective phases.
"""

import logging
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any, ClassVar

from arq.connections import RedisSettings

from snowobs_common import __version__
from snowobs_common.config import Settings, load_settings
from snowobs_common.logging import configure_logging, get_logger

logger = get_logger(__name__)


async def ping(ctx: dict[str, Any], payload: str | None = None) -> dict[str, str]:
    """Round-trip liveness job: proves enqueue → execute → result works."""
    result = {
        "pong": payload or "pong",
        "worker_version": __version__,
        "at": datetime.now(tz=UTC).isoformat(),
    }
    logger.info("ping_handled", payload=payload)
    return result


async def startup(ctx: dict[str, Any]) -> None:
    settings: Settings = ctx.get("settings") or load_settings()
    ctx["settings"] = settings
    configure_logging(json_output=settings.log_json, level=logging.INFO)
    logger.info("worker_started", version=__version__, mode=settings.mode)


async def shutdown(ctx: dict[str, Any]) -> None:
    logger.info("worker_stopped")


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(load_settings().redis_url)


class WorkerSettings:
    """arq configuration object (referenced as ``snowobs_worker.main.WorkerSettings``)."""

    functions: ClassVar[list[Callable[..., Coroutine[Any, Any, dict[str, str]]]]] = [ping]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = _redis_settings()
    health_check_interval = 30
    max_jobs = 10
    job_timeout = 600
