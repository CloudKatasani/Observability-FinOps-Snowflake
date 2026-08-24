"""Structured logging setup (BUILD_PROMPT §18).

JSON logs with context binding for production; readable console output for
development. Request/trace correlation is carried through structlog
contextvars — handlers bind ``trace_id``/``tenant_id`` once and every log line
in that context includes them.
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(*, json_output: bool = True, level: int = logging.INFO) -> None:
    """Configure stdlib + structlog once, at process startup. Idempotent."""
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: structlog.typing.Processor
    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Uvicorn's own loggers propagate to root so every line shares one format.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Named structlog logger; the sole logger entry point for application code."""
    return structlog.get_logger(name)  # type: ignore[no-any-return]
