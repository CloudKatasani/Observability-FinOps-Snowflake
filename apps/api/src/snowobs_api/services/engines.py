"""Which engine answers a query, and in which dialect (BUILD_PROMPT §4, R10).

The two operating modes are a *deployment* concern, not a business-logic one:
the same compiled semantic layer runs against Snowflake by pushdown or against
DuckDB over landed Parquet, and nothing above this module branches on which
(R1). This is the one place that chooses, so a service cannot quietly answer
from landed data while the deployment believes it is reading LIVE.

Selection is deliberately conservative. `mode = "auto"` prefers LIVE only when a
connection is fully configured, because the failure it avoids is the expensive
one: a dashboard that silently serves stale uploaded extracts to an operator who
believes they are looking at their account.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from snowobs_common.config import Settings
from snowobs_common.errors import ConfigurationError
from snowobs_common.logging import get_logger
from snowobs_engines.cache import ResultCache
from snowobs_engines.duckdb_engine import DuckDBEngine
from snowobs_ingest.catalog import DuckDBCatalog
from snowobs_semantics.dialect_shims import Dialect

logger = get_logger(__name__)


@dataclass(frozen=True)
class EngineChoice:
    """The engine to run against, and why it was chosen."""

    engine: Any
    dialect: Dialect
    mode: str
    #: Populated when LIVE was configured but could not be used, so the caller
    #: can say so rather than presenting landed data as live (R3).
    fell_back_because: str | None = None


def live_is_configured(settings: Settings) -> bool:
    """Is there enough configuration to open a Snowflake session at all?

    Account, user, and a secret reference are the irreducible set: without any
    one of them the connector cannot authenticate, and discovering that at query
    time would surface as a dashboard error rather than as a configuration one.
    """
    snowflake = settings.snowflake
    return bool(snowflake.account and snowflake.user and snowflake.private_key_ref)


def resolve_mode(settings: Settings) -> str:
    """The mode this deployment will actually run in."""
    if settings.mode == "live":
        return "live"
    if settings.mode == "offline":
        return "offline"
    return "live" if live_is_configured(settings) else "offline"


@contextmanager
def open_engine(
    settings: Settings,
    *,
    tenant: str = "default",
    cache: ResultCache | None = None,
    storage_root: Path | None = None,
) -> Iterator[EngineChoice]:
    """Yield the engine for this deployment, closing whatever it opened.

    A context manager because the OFFLINE engine owns a DuckDB connection that
    must be closed, and callers should not have to know which engine they got in
    order to release it correctly.
    """
    mode = resolve_mode(settings)

    if mode == "live":
        fallback = _live_unavailable_reason(settings)
        if fallback is None:
            engine = _build_live_engine(settings, tenant=tenant, cache=cache)
            yield EngineChoice(engine=engine, dialect=Dialect.SNOWFLAKE, mode="live")
            return
        if settings.mode == "live":
            # Explicitly configured for LIVE: refuse rather than answer from
            # landed data. An operator who asked for LIVE and silently got
            # OFFLINE has no way to notice.
            raise ConfigurationError(
                f"LIVE mode is configured but unavailable: {fallback}. "
                "Fix the connection, or set SNOWOBS_MODE=offline to read landed extracts."
            )
        logger.info("live_unavailable_falling_back_to_offline", reason=fallback)
        mode, reason = "offline", fallback
    else:
        reason = None

    root = storage_root if storage_root is not None else _storage_root(settings)
    catalog = DuckDBCatalog(root, tenant=tenant)
    try:
        yield EngineChoice(
            engine=DuckDBEngine(catalog, cache=cache),
            dialect=Dialect.DUCKDB,
            mode=mode,
            fell_back_because=reason,
        )
    finally:
        catalog.close()


def _live_unavailable_reason(settings: Settings) -> str | None:
    """Why LIVE cannot be used right now, or None when it can."""
    if not live_is_configured(settings):
        missing = [
            name
            for name, value in (
                ("snowflake.account", settings.snowflake.account),
                ("snowflake.user", settings.snowflake.user),
                ("snowflake.private_key_ref", settings.snowflake.private_key_ref),
            )
            if not value
        ]
        return f"connection is not configured ({', '.join(missing)} unset)"
    try:
        import snowflake.connector  # noqa: F401
    except ImportError:
        # The connector is an optional extra, so a deployment can be configured
        # for LIVE against an image that cannot reach Snowflake at all.
        return "the snowflake-connector-python extra is not installed in this image"
    return None


def _build_live_engine(settings: Settings, *, tenant: str, cache: ResultCache | None) -> Any:
    from snowobs_live.connection import AuthMethod, ConnectionProfile, SnowflakeConnector
    from snowobs_live.engine import SnowflakeEngine

    snowflake = settings.snowflake
    profile = ConnectionProfile(
        account=str(snowflake.account),
        user=str(snowflake.user),
        auth=AuthMethod(snowflake.auth),
        secret_ref=snowflake.private_key_ref,
        role=snowflake.role,
        warehouse=snowflake.warehouse,
        query_tag_prefix=snowflake.query_tag_prefix,
        statement_timeout_s=snowflake.statement_timeout_s,
    )
    return SnowflakeEngine(
        SnowflakeConnector(profile),
        max_rows=settings.guardrails.max_rows,
        cache=cache,
        tenant=tenant,
    )


def _storage_root(settings: Settings) -> Path:
    from snowobs_api.services.datasets import storage_root

    return storage_root(settings)


__all__ = [
    "EngineChoice",
    "live_is_configured",
    "open_engine",
    "resolve_mode",
]
