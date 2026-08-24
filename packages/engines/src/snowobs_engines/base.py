"""The QueryEngine protocol (BUILD_PROMPT §4).

Both engines execute *compiled* SQL and return aggregates. Neither contains
business logic: the semantic compiler owns what a metric means, the engine owns
only how to run it and what the result's provenance is (R1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from snowobs_semantics.compiler import CompiledQuery
from snowobs_semantics.dialect_shims import Dialect


@dataclass(frozen=True)
class QueryResult:
    """Rows plus everything R5 and R7 require alongside a figure."""

    columns: list[str]
    rows: list[tuple[Any, ...]]
    #: The SQL actually executed, after the guard — "show the SQL" shows this.
    executed_sql: str
    dialect: Dialect
    sources: list[str]
    as_of: datetime
    latency_floor_minutes: int
    provisional: bool
    row_count: int
    truncated: bool = False
    elapsed_ms: float = 0.0
    #: Credits this query itself consumed, when the engine can report it.
    engine_credits: Decimal | None = None
    cache_hit: bool = False
    warnings: list[str] = field(default_factory=list)

    def dicts(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, row, strict=True)) for row in self.rows]

    def scalar(self) -> Any:
        """The single value of a single-row, single-column result, or None."""
        if not self.rows or not self.rows[0]:
            return None
        return self.rows[0][-1]


@runtime_checkable
class QueryEngine(Protocol):
    """What both execution engines implement."""

    @property
    def dialect(self) -> Dialect:  # pragma: no cover - structural
        ...

    def execute(self, compiled: CompiledQuery) -> QueryResult:
        """Run a compiled query through the SQL guard and return aggregates."""
        ...

    def available_relations(self) -> frozenset[str]:
        """Relations this engine can currently read — drives the guard policy."""
        ...

    def close(self) -> None: ...
