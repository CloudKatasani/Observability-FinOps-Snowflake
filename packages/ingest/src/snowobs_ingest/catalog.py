"""DuckDB catalog over landed Parquet.

Each registered source becomes a view over its Parquet parts, deduplicated on
the declared grain with last-write-wins on ``_LOADED_AT`` so overlapping
uploads merge rather than double-count. The catalog is the OFFLINE engine's
"raw" layer (§8.1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from snowobs_common.errors import AppError
from snowobs_common.logging import get_logger
from snowobs_semantics.registry import SourceRegistry, default_registry

if TYPE_CHECKING:  # pragma: no cover - typing only
    import duckdb

logger = get_logger(__name__)

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class CatalogError(AppError):
    status_code = 500
    title = "Catalog error"
    problem_type = "https://snowobs.dev/problems/catalog"


def ident(name: str) -> str:
    """Quote an identifier, rejecting anything that is not a bare name.

    Identifiers here come from the source registry (validated YAML), but they
    still reach SQL by interpolation, so they are vetted rather than trusted —
    there is no string-concatenation escape hatch anywhere (R9).
    """
    if not _SAFE_IDENTIFIER.match(name):
        raise CatalogError(f"Unsafe SQL identifier: {name!r}")
    return f'"{name}"'


def literal(value: str) -> str:
    """Quote a string literal (a filesystem glob), rejecting quote characters."""
    if "'" in value or "\\" in value:
        raise CatalogError(f"Unsafe SQL literal: {value!r}")
    return f"'{value}'"


@dataclass(frozen=True)
class SourceStats:
    """What the catalog knows about one landed source."""

    source_id: str
    rows: int
    min_timestamp: str | None
    max_timestamp: str | None
    batches: int

    @property
    def window(self) -> tuple[date, date] | None:
        if not self.min_timestamp or not self.max_timestamp:
            return None
        return (
            date.fromisoformat(self.min_timestamp[:10]),
            date.fromisoformat(self.max_timestamp[:10]),
        )


class DuckDBCatalog:
    """Registers landed Parquet as queryable views."""

    def __init__(
        self,
        storage_root: Path,
        registry: SourceRegistry | None = None,
        tenant: str = "default",
        database: str | Path = ":memory:",
    ) -> None:
        import duckdb

        self.storage_root = storage_root
        self.registry = registry or default_registry()
        self.tenant = tenant
        self.connection: duckdb.DuckDBPyConnection = duckdb.connect(str(database))
        self.connection.execute("SET TimeZone='UTC'")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> DuckDBCatalog:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------- discovery
    def parts_for(self, source_id: str) -> list[Path]:
        directory = self.storage_root / self.tenant / source_id
        return sorted(directory.glob("part-*.parquet")) if directory.is_dir() else []

    def landed_sources(self) -> list[str]:
        return [s for s in self.registry.ids() if self.parts_for(s)]

    # -------------------------------------------------------------- register
    def register_all(self) -> list[str]:
        """(Re)create a view per landed source. Returns the view names created."""
        created: list[str] = []
        for source_id in self.landed_sources():
            self.register(source_id)
            created.append(source_id)
        logger.info("catalog_registered", sources=len(created), tenant=self.tenant)
        return created

    def register(self, source_id: str) -> None:
        parts = self.parts_for(source_id)
        if not parts:
            return
        source = self.registry.get(source_id)
        # Identifiers reach SQL only after ident()/literal() vetting, so the
        # interpolation below cannot carry an injection payload (R9).
        view = ident(source_id)
        glob = literal(str(self.storage_root / self.tenant / source_id / "part-*.parquet"))

        if source.grain:
            grain = ", ".join(ident(column.upper()) for column in source.grain)
            # Last-write-wins on ingest time keeps overlapping uploads idempotent.
            sql = f"""
                CREATE OR REPLACE VIEW {view} AS
                SELECT * EXCLUDE (_dedup_rank) FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY {grain} ORDER BY "_LOADED_AT" DESC, "_BATCH_ID" DESC
                    ) AS _dedup_rank
                    FROM read_parquet({glob}, union_by_name = true)
                ) WHERE _dedup_rank = 1
            """  # noqa: S608 — identifiers vetted by ident(); path vetted by literal()
        else:
            sql = f"""
                CREATE OR REPLACE VIEW {view} AS
                SELECT * FROM read_parquet({glob}, union_by_name = true)
            """  # noqa: S608 — as above
        self.connection.execute(sql)

    # ----------------------------------------------------------------- stats
    def stats(self, source_id: str) -> SourceStats | None:
        if not self.parts_for(source_id):
            return None
        source = self.registry.get(source_id)
        view = ident(source_id)
        time_column = ident(source.time_column.upper()) if source.time_column else None
        if time_column:
            row = self.connection.execute(
                f"SELECT COUNT(*), MIN({time_column}), MAX({time_column}), "  # noqa: S608
                f'COUNT(DISTINCT "_BATCH_ID") FROM {view}'
            ).fetchone()
        else:
            row = self.connection.execute(
                f'SELECT COUNT(*), NULL, NULL, COUNT(DISTINCT "_BATCH_ID") FROM {view}'  # noqa: S608
            ).fetchone()
        if row is None:  # pragma: no cover - an aggregate always returns a row
            raise CatalogError(f"No statistics returned for {source_id}")
        return SourceStats(
            source_id=source_id,
            rows=int(row[0]),
            min_timestamp=str(row[1]) if row[1] is not None else None,
            max_timestamp=str(row[2]) if row[2] is not None else None,
            batches=int(row[3]),
        )

    def query(self, sql: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
        """Execute read-only SQL against the catalog (callers pre-validate via sqlguard)."""
        cursor = self.connection.execute(sql, params or [])
        return cursor.fetchall()

    def columns_of(self, source_id: str) -> list[str]:
        rows = self.connection.execute(f"DESCRIBE {ident(source_id)}").fetchall()
        return [str(row[0]) for row in rows]


def freshness_minutes(stats: SourceStats, *, as_of: datetime | None = None) -> float | None:
    """Age of the newest row, in minutes — the input to the freshness banner."""
    if stats.max_timestamp is None:
        return None
    reference = as_of or datetime.now()  # noqa: DTZ005 — compared to naive source stamps
    try:
        newest = datetime.fromisoformat(stats.max_timestamp)
    except ValueError:
        return None
    if newest.tzinfo is not None:
        newest = newest.replace(tzinfo=None)
    return max((reference - newest).total_seconds() / 60.0, 0.0)
