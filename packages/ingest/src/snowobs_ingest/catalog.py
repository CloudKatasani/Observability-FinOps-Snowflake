"""DuckDB catalog over landed Parquet.

Each registered source becomes a view over its Parquet parts, deduplicated on
the declared grain with last-write-wins on ``_LOADED_AT`` so overlapping
uploads merge rather than double-count. The catalog is the OFFLINE engine's
"raw" layer (§8.1).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from snowobs_common.errors import AppError
from snowobs_common.logging import get_logger
from snowobs_ingest.tenancy import tenant_root, validate_tenant
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


#: The ingest metadata column recording which Snowflake account a batch came
#: from. Not part of any source's schema — ACCOUNT_USAGE has no such column.
ACCOUNT_COLUMN = "_ACCOUNT"


@dataclass(frozen=True)
class SourceStats:
    """What the catalog knows about one landed source."""

    source_id: str
    rows: int
    min_timestamp: str | None
    max_timestamp: str | None
    batches: int
    #: The account the rows were tagged with, when these stats are scoped to
    #: one; ``None`` for the whole-source figures.
    account: str | None = None

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
        # Validated here rather than at every join: the tenant id becomes a
        # directory name, and `acme/../globex` reads another customer's data
        # without erroring (§17).
        self.tenant = validate_tenant(tenant)
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
        directory = tenant_root(self.storage_root, self.tenant) / source_id
        return sorted(directory.glob("part-*.parquet")) if directory.is_dir() else []

    def landed_sources(self) -> list[str]:
        return [s for s in self.registry.ids() if self.parts_for(s)]

    def _glob_for(self, source_id: str) -> str:
        return literal(
            str(tenant_root(self.storage_root, self.tenant) / source_id / "part-*.parquet")
        )

    def part_columns(self, source_id: str) -> list[str]:
        """Columns present in a source's landed Parquet, before registration.

        Read from the files rather than from the view, because the view's
        definition depends on this answer — the dedup key includes the account
        stamp only when the parts actually carry it.
        """
        if not self.parts_for(source_id):
            return []
        rows = self.connection.execute(
            f"DESCRIBE SELECT * FROM read_parquet({self._glob_for(source_id)}, "  # noqa: S608
            "union_by_name = true)"
        ).fetchall()
        return [str(row[0]) for row in rows]

    def dataset_version(self) -> str:
        """A fingerprint of exactly what is landed for this tenant.

        Two things depend on this. A cached result must not outlive the upload
        it was computed from — the SQL is unchanged when new data lands, so the
        statement alone cannot tell a stale answer from a fresh one. And a
        cache shared between tenants must never serve one tenant's rows to
        another: two tenants query identically-named views, so their compiled
        SQL is byte-identical and the tenant has to enter the key here.

        Batch file names are enough: a new upload writes a new part file, and
        ingest never rewrites one in place.
        """
        parts = [
            f"{source_id}:{path.name}"
            for source_id in self.landed_sources()
            for path in self.parts_for(source_id)
        ]
        digest = hashlib.sha256("|".join([self.tenant, *parts]).encode()).hexdigest()
        return digest[:16]

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
        glob = self._glob_for(source_id)

        if source.grain:
            grain_columns = [ident(column.upper()) for column in source.grain]
            # An ACCOUNT_USAGE view's grain is implicitly *per account* — its
            # rows carry no account column, so two accounts' extracts collide
            # on QUERY_ID or (WAREHOUSE_ID, START_TIME) and last-write-wins
            # would silently discard one account's entire history. The account
            # the batch came from is therefore part of the dedup key. An
            # ORGANIZATION_USAGE view already names the account in its own
            # schema, so its declared grain is complete as it stands.
            if not source.is_organization_scoped and self.has_account_column(source_id):
                grain_columns.append(ident(ACCOUNT_COLUMN))
            grain = ", ".join(grain_columns)
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

    # -------------------------------------------------------------- accounts
    def has_account_column(self, source_id: str) -> bool:
        """Whether this source's landed parts carry the ``_ACCOUNT`` stamp.

        Parquet written before the column existed simply does not have it, and
        ``union_by_name`` will not invent one, so every account-aware read
        checks first rather than failing on a lake that predates the column.
        """
        return ACCOUNT_COLUMN in self.part_columns(source_id)

    def accounts_for(self, source_id: str) -> list[str]:
        """Accounts whose extracts have landed for one source."""
        if not self.has_account_column(source_id):
            return []
        rows = self.connection.execute(
            f'SELECT DISTINCT "{ACCOUNT_COLUMN}" FROM {ident(source_id)} '  # noqa: S608
            f'WHERE "{ACCOUNT_COLUMN}" IS NOT NULL ORDER BY 1'
        ).fetchall()
        return [str(row[0]) for row in rows]

    def accounts(self) -> list[str]:
        """Every account present anywhere in this tenant's lake, in name order.

        An enterprise lake holds several accounts' extracts side by side; this
        is what tells the coverage page and the org roll-ups which they are.

        Only *account-scoped* sources count. An ``ORGANIZATION_USAGE`` extract
        is stamped too — it is exported once, from whichever account holds the
        grant — but that stamp names the organization, and the organization is
        not an account anyone can select a per-account view of. Counting it
        would put "ACME_GROUP" in the account picker beside its own members.
        """
        found: set[str] = set()
        for source_id in self.landed_sources():
            if self.registry.get(source_id).is_organization_scoped:
                continue
            found.update(self.accounts_for(source_id))
        return sorted(found)

    # ----------------------------------------------------------------- stats
    def stats(self, source_id: str, account: str | None = None) -> SourceStats | None:
        """Row counts and window for a source, optionally for one account."""
        if not self.parts_for(source_id):
            return None
        if account is not None and not self.has_account_column(source_id):
            return None
        source = self.registry.get(source_id)
        view = ident(source_id)
        time_column = ident(source.time_column.upper()) if source.time_column else "NULL"
        clause = f' WHERE "{ACCOUNT_COLUMN}" = ?' if account is not None else ""
        params = [account] if account is not None else []
        if source.time_column:
            projection = f"COUNT(*), MIN({time_column}), MAX({time_column})"
        else:
            projection = "COUNT(*), NULL, NULL"
        row = self.connection.execute(
            f'SELECT {projection}, COUNT(DISTINCT "_BATCH_ID") FROM {view}{clause}',  # noqa: S608
            params,
        ).fetchone()
        if row is None:  # pragma: no cover - an aggregate always returns a row
            raise CatalogError(f"No statistics returned for {source_id}")
        return SourceStats(
            source_id=source_id,
            rows=int(row[0]),
            min_timestamp=str(row[1]) if row[1] is not None else None,
            max_timestamp=str(row[2]) if row[2] is not None else None,
            batches=int(row[3]),
            account=account,
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
