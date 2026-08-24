"""OFFLINE engine: embedded DuckDB over the landed Parquet catalog."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from snowobs_common.logging import get_logger
from snowobs_engines.base import QueryResult
from snowobs_engines.cache import ResultCache
from snowobs_ingest.catalog import DuckDBCatalog
from snowobs_semantics.compiler import CompiledQuery
from snowobs_semantics.dialect_shims import Dialect
from snowobs_sqlguard.guard import GuardPolicy, check, offline_policy

logger = get_logger(__name__)


class DuckDBEngine:
    """Executes compiled DuckDB SQL against the registered source views."""

    dialect = Dialect.DUCKDB

    def __init__(
        self,
        catalog: DuckDBCatalog,
        *,
        max_rows: int = 50_000,
        cache: ResultCache | None = None,
    ) -> None:
        self.catalog = catalog
        self.max_rows = max_rows
        self.cache = cache
        self._registered = frozenset(catalog.register_all())

    def available_relations(self) -> frozenset[str]:
        return frozenset(name.upper() for name in self._registered)

    def policy(self) -> GuardPolicy:
        return offline_policy(self.available_relations(), max_rows=self.max_rows)

    def refresh(self) -> None:
        """Re-register views after a new upload lands."""
        self._registered = frozenset(self.catalog.register_all())

    def execute(self, compiled: CompiledQuery) -> QueryResult:
        if compiled.dialect is not Dialect.DUCKDB:
            raise ValueError(
                f"DuckDBEngine received {compiled.dialect.value} SQL; compile for duckdb"
            )
        if self.cache is not None:
            cached = self.cache.get(compiled.cache_key)
            if cached is not None:
                return cached

        guarded = check(compiled.sql, self.policy(), dialect="duckdb")
        started = time.perf_counter()
        cursor = self.catalog.connection.execute(guarded.sql)
        rows = cursor.fetchall()
        elapsed_ms = (time.perf_counter() - started) * 1000
        columns = [description[0] for description in (cursor.description or [])]

        result = QueryResult(
            columns=columns,
            rows=rows,
            executed_sql=guarded.sql,
            dialect=Dialect.DUCKDB,
            sources=list(compiled.sources_used),
            gating_sources=list(compiled.gating_sources),
            as_of=datetime.now(tz=UTC),
            latency_floor_minutes=compiled.latency_floor_minutes,
            provisional=compiled.provisional,
            row_count=len(rows),
            truncated=len(rows) >= guarded.limit,
            elapsed_ms=round(elapsed_ms, 2),
            warnings=list(guarded.adjustments),
        )
        logger.info(
            "query_executed",
            engine="duckdb",
            metrics=compiled.metrics,
            rows=len(rows),
            elapsed_ms=result.elapsed_ms,
        )
        if self.cache is not None:
            self.cache.put(compiled.cache_key, result)
        return result

    def close(self) -> None:
        self.catalog.close()
