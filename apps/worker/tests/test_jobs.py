"""Worker job functions and registration."""

import pytest

from snowobs_common import __version__
from snowobs_worker.main import WorkerSettings, ping


@pytest.mark.asyncio
async def test_ping_returns_payload_and_version() -> None:
    result = await ping({}, payload="hello")
    assert result["pong"] == "hello"
    assert result["worker_version"] == __version__
    assert result["at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_ping_defaults() -> None:
    result = await ping({})
    assert result["pong"] == "pong"


def test_jobs_are_registered_explicitly() -> None:
    assert ping in WorkerSettings.functions
    assert WorkerSettings.health_check_interval == 30
