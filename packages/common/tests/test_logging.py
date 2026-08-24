"""Structured logging emits JSON lines with correlation context."""

import json
import logging

import structlog

from snowobs_common.logging import configure_logging, get_logger


def test_json_logs_carry_bound_context(capsys) -> None:  # type: ignore[no-untyped-def]
    configure_logging(json_output=True, level=logging.INFO)
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(trace_id="abc123", tenant_id="t1")

    get_logger("test.module").info("hello", figure=42)

    line = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "hello"
    assert payload["figure"] == 42
    assert payload["trace_id"] == "abc123"
    assert payload["tenant_id"] == "t1"
    assert payload["level"] == "info"
    assert payload["logger"] == "test.module"
    assert "timestamp" in payload
    structlog.contextvars.clear_contextvars()


def test_stdlib_loggers_share_the_format(capsys) -> None:  # type: ignore[no-untyped-def]
    configure_logging(json_output=True, level=logging.INFO)
    logging.getLogger("third.party").warning("plain stdlib message")
    line = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "plain stdlib message"
    assert payload["level"] == "warning"
