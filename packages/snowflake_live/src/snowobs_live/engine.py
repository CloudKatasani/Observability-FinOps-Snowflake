"""LIVE engine: pushdown execution against Snowflake (BUILD_PROMPT §7.2).

The app issues SQL and stores aggregates; it does not copy telemetry out (R2).
Every statement passes the SQL guard, carries the platform's query tag so the
tool is attributable in the customer's own telemetry, and is recorded in the
app's own query log so the platform can report its own run cost — the
``cost.platform_self_cost`` KPI.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from snowobs_common.logging import get_logger
from snowobs_engines.base import QueryResult
from snowobs_engines.cache import ResultCache
from snowobs_live.connection import ConnectionProfile, SnowflakeConnector, query_tag
from snowobs_semantics.compiler import CompiledQuery
from snowobs_semantics.dialect_shims import Dialect
from snowobs_sqlguard.guard import GuardPolicy, check, live_policy

if TYPE_CHECKING:  # pragma: no cover - typing only
    from snowflake.connector import SnowflakeConnection

logger = get_logger(__name__)

#: The registered usage schemas. Anything else needs an explicit grant in the
#: guard policy, so a compiled query can never wander into customer data.
USAGE_SCHEMAS = frozenset({"SNOWFLAKE.ACCOUNT_USAGE", "SNOWFLAKE.ORGANIZATION_USAGE"})


@dataclass
class SelfCostEntry:
    """One statement the platform ran, and what it cost."""

    query_id: str
    surface: str
    trace_id: str
    started_at: datetime
    elapsed_ms: float
    bytes_scanned: int | None = None
    credits: Decimal | None = None
    warehouse: str | None = None


@dataclass
class SelfCostLog:
    """The platform's own query log — the basis of its self-cost KPI (§7.2)."""

    entries: list[SelfCostEntry] = field(default_factory=list)

    def record(self, entry: SelfCostEntry) -> None:
        self.entries.append(entry)

    @property
    def total_credits(self) -> Decimal:
        return sum((e.credits for e in self.entries if e.credits), Decimal(0))

    @property
    def statement_count(self) -> int:
        return len(self.entries)

    def by_surface(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries:
            counts[entry.surface] = counts.get(entry.surface, 0) + 1
        return dict(sorted(counts.items()))


class SnowflakeEngine:
    """Executes compiled Snowflake SQL by pushdown."""

    dialect = Dialect.SNOWFLAKE

    def __init__(
        self,
        connector: SnowflakeConnector,
        *,
        max_rows: int = 50_000,
        cache: ResultCache | None = None,
        extra_schemas: frozenset[str] = frozenset(),
        tenant: str = "default",
    ) -> None:
        self.connector = connector
        self.max_rows = max_rows
        self.cache = cache
        self.extra_schemas = extra_schemas
        self.tenant = tenant
        self.self_cost = SelfCostLog()

    @property
    def profile(self) -> ConnectionProfile:
        return self.connector.profile

    def available_relations(self) -> frozenset[str]:
        """LIVE mode reads whole schemas rather than an enumerated view list."""
        return frozenset()

    def policy(self, *, surface: str = "app", trace_id: str = "-") -> GuardPolicy:
        return live_policy(
            max_rows=self.max_rows,
            warehouse=self.profile.warehouse,
            query_tag=query_tag(
                self.profile, tenant=self.tenant, surface=surface, trace_id=trace_id
            ),
            timeout_seconds=self.profile.statement_timeout_s,
            extra_schemas=self.extra_schemas,
        )

    def execute(
        self, compiled: CompiledQuery, *, surface: str = "tile", trace_id: str = "-"
    ) -> QueryResult:
        if compiled.dialect is not Dialect.SNOWFLAKE:
            raise ValueError(
                f"SnowflakeEngine received {compiled.dialect.value} SQL; compile for snowflake"
            )
        if self.cache is not None:
            cached = self.cache.get(compiled.cache_key)
            if cached is not None:
                return cached

        guarded = check(
            compiled.sql, self.policy(surface=surface, trace_id=trace_id), dialect="snowflake"
        )
        started = time.perf_counter()
        started_at = datetime.now(tz=UTC)

        with self._session(surface=surface, trace_id=trace_id) as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(guarded.sql)
                rows = list(cursor.fetchall())
                columns = [description[0] for description in (cursor.description or [])]
                query_id = str(getattr(cursor, "sfqid", "") or "")
                bytes_scanned = _stat(cursor, "bytes_scanned")
            finally:
                cursor.close()

        elapsed_ms = (time.perf_counter() - started) * 1000
        # R2: the app keeps the aggregate and the statement's own cost — not the
        # telemetry rows behind it.
        self.self_cost.record(
            SelfCostEntry(
                query_id=query_id,
                surface=surface,
                trace_id=trace_id,
                started_at=started_at,
                elapsed_ms=round(elapsed_ms, 2),
                bytes_scanned=bytes_scanned,
                warehouse=self.profile.warehouse,
            )
        )
        logger.info(
            "query_executed",
            engine="snowflake",
            metrics=compiled.metrics,
            rows=len(rows),
            elapsed_ms=round(elapsed_ms, 2),
            snowflake_query_id=query_id,
            surface=surface,
        )

        result = QueryResult(
            columns=columns,
            rows=rows,
            executed_sql=guarded.sql,
            dialect=Dialect.SNOWFLAKE,
            sources=list(compiled.sources_used),
            as_of=datetime.now(tz=UTC),
            latency_floor_minutes=compiled.latency_floor_minutes,
            provisional=compiled.provisional,
            row_count=len(rows),
            truncated=len(rows) >= guarded.limit,
            elapsed_ms=round(elapsed_ms, 2),
            warnings=list(guarded.adjustments),
        )
        if self.cache is not None:
            self.cache.put(compiled.cache_key, result)
        return result

    def _session(self, *, surface: str, trace_id: str) -> _Session:
        return _Session(self.connector, surface=surface, trace_id=trace_id)

    def close(self) -> None:
        """Connections are per-statement; nothing is held open between queries."""
        return None


def _stat(cursor: Any, name: str) -> int | None:
    """Read an execution statistic the driver exposes, when it does."""
    value = getattr(cursor, name, None)
    if isinstance(value, int):
        return value
    stats = getattr(cursor, "query_result_format", None)
    del stats
    return None


class _Session:
    """A connection scoped to one statement, so nothing leaks between surfaces."""

    def __init__(self, connector: SnowflakeConnector, *, surface: str, trace_id: str) -> None:
        self.connector = connector
        self.surface = surface
        self.trace_id = trace_id
        self._connection: SnowflakeConnection | None = None

    def __enter__(self) -> SnowflakeConnection:
        self._connection = self.connector.connect(surface=self.surface, trace_id=self.trace_id)
        return self._connection

    def __exit__(self, *exc: object) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None


def iter_batches(cursor: Any, size: int = 10_000) -> Iterator[list[tuple[Any, ...]]]:
    """Fetch in batches so a large aggregate never materialises twice."""
    while True:
        batch = cursor.fetchmany(size)
        if not batch:
            return
        yield list(batch)
