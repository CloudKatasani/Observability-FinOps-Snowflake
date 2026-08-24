"""Which engine answers a query (BUILD_PROMPT §4, R10).

This is the seam where the two operating modes meet, and its failure mode is
silent: a deployment configured for LIVE that quietly answers from landed
extracts shows an operator plausible figures for an account they are not
looking at. Nothing raises, and the dashboard looks fine.

The tests therefore assert the *choice*, not just that a query succeeded.
"""

from __future__ import annotations

import duckdb
import pytest

from snowobs_api.services.engines import live_is_configured, open_engine, resolve_mode
from snowobs_common.config import Settings
from snowobs_common.errors import ConfigurationError
from snowobs_semantics.dialect_shims import Dialect

LIVE_CONNECTION = {
    "account": "acme-prod",
    "user": "SNOWOBS_APP",
    "private_key_ref": "secret://snowobs/keypair",
    "role": "SNOWOBS_READER",
    "warehouse": "WH_SNOWOBS_APP",
}


def _settings(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────── the choice
def test_a_connection_needs_account_user_and_a_secret_reference() -> None:
    """Partial configuration is not configuration — it fails at query time."""
    assert live_is_configured(_settings(snowflake=LIVE_CONNECTION))
    for omitted in ("account", "user", "private_key_ref"):
        partial = {k: v for k, v in LIVE_CONNECTION.items() if k != omitted}
        assert not live_is_configured(_settings(snowflake=partial)), omitted


def test_auto_reads_landed_data_when_no_connection_is_configured() -> None:
    assert resolve_mode(_settings(mode="auto")) == "offline"


def test_auto_prefers_live_once_a_connection_is_configured() -> None:
    assert resolve_mode(_settings(mode="auto", snowflake=LIVE_CONNECTION)) == "live"


def test_offline_stays_offline_even_with_a_connection_configured() -> None:
    """An operator who pins OFFLINE gets OFFLINE — configuration is not a hint."""
    assert resolve_mode(_settings(mode="offline", snowflake=LIVE_CONNECTION)) == "offline"


# ──────────────────────────────────────────────────────── what gets opened
def test_offline_yields_the_duckdb_engine_and_its_dialect(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(mode="offline", storage={"provider": "local", "bucket": str(tmp_path)})
    with open_engine(settings) as chosen:
        assert chosen.mode == "offline"
        assert chosen.dialect is Dialect.DUCKDB
        # The compiled SQL must match the engine that will run it (R1).
        assert chosen.engine.dialect is Dialect.DUCKDB


def test_the_duckdb_connection_is_released_when_the_block_exits(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(mode="offline", storage={"provider": "local", "bucket": str(tmp_path)})
    with open_engine(settings) as chosen:
        catalog = chosen.engine.catalog
    with pytest.raises(duckdb.ConnectionException):
        catalog.connection.execute("SELECT 1")


def test_explicit_live_refuses_rather_than_answering_from_landed_data(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The heart of it: a misconfigured LIVE deployment must fail loudly.

    Falling back would hand an operator OFFLINE figures under a LIVE banner,
    and there is no way for them to notice from the answer.
    """
    settings = _settings(mode="live", storage={"provider": "local", "bucket": str(tmp_path)})
    with pytest.raises(ConfigurationError) as raised:
        with open_engine(settings):
            pass
    message = str(raised.value)
    assert "LIVE" in message
    assert "SNOWOBS_MODE=offline" in message  # and it says how to proceed


def test_auto_falls_back_to_offline_and_records_why(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """`auto` may fall back, but never silently: the reason is on the result."""
    settings = _settings(
        mode="auto",
        # Configured, but the connector extra is absent from this environment.
        snowflake=LIVE_CONNECTION,
        storage={"provider": "local", "bucket": str(tmp_path)},
    )
    try:
        import snowflake.connector  # noqa: F401
    except ImportError:
        with open_engine(settings) as chosen:
            assert chosen.mode == "offline"
            assert chosen.fell_back_because
            assert "extra" in chosen.fell_back_because
    else:
        # With the connector installed, `auto` genuinely selects LIVE. No
        # session is opened until a query runs, so this stays offline-safe.
        with open_engine(settings) as chosen:
            assert chosen.mode == "live"
            assert chosen.dialect is Dialect.SNOWFLAKE


def test_the_live_engine_carries_the_configured_role_and_warehouse(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """R4: the app connects as its own read-only role, on its own warehouse."""
    pytest.importorskip("snowflake.connector")
    settings = _settings(mode="live", snowflake=LIVE_CONNECTION)
    with open_engine(settings) as chosen:
        profile = chosen.engine.profile
        assert profile.role == "SNOWOBS_READER"
        assert profile.warehouse == "WH_SNOWOBS_APP"
        # Secrets are held by reference, never inline (§27.13).
        assert profile.secret_ref == "secret://snowobs/keypair"
        assert "private_key" not in profile.redacted()


# ──────────────────────────────────────────── the services use the choice
def test_no_service_hardcodes_an_engine_or_a_dialect() -> None:
    """The regression this file exists for.

    Every service compiled `Dialect.DUCKDB` and built a `DuckDBEngine`
    directly, so LIVE mode was fully implemented, fully tested, and
    unreachable from any dashboard. A new service that does the same would
    reintroduce exactly that, and nothing else would catch it.
    """
    import pathlib

    services = pathlib.Path(__file__).parents[1] / "src/snowobs_api/services"
    offenders: list[str] = []
    for path in sorted(services.glob("*.py")):
        if path.name == "engines.py":  # the one place allowed to choose
            continue
        text = path.read_text(encoding="utf-8")
        if "Dialect.DUCKDB" in text or "DuckDBEngine(" in text:
            offenders.append(path.name)
    assert not offenders, (
        f"{offenders} select an engine directly instead of using "
        "services/engines.open_engine(), which makes them OFFLINE-only"
    )
