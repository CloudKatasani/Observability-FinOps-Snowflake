"""Landing and cataloguing (steps 5–7 of §7.3).

Rows land as partitioned Parquet under ``{tenant}/{source_id}/`` in the storage
root and are registered as DuckDB views. A second upload for a later window
merges rather than replaces: dataset versions are tracked with their window
bounds, and duplicates on the declared grain resolve last-write-wins on ingest
timestamp.

**Which account an extract came from is platform knowledge, not source data.**
Real ``ACCOUNT_USAGE`` views carry no account column — an extract from one
account is indistinguishable from another's once it is a file on disk — so the
account is recorded the same way the load timestamp and batch id are: as an
ingest metadata column, ``_ACCOUNT``, stamped on every row of the batch. It
records *the Snowflake account the batch was extracted from*. For an
organization-scoped view (``ORGANIZATION_USAGE``) that is the ORGADMIN-enabled
or organization account the export ran in — usually named for the organization
— and the per-account attribution for those rows lives in the view's own
``ACCOUNT_NAME`` column, which ingest never overwrites.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from snowobs_common.logging import get_logger
from snowobs_ingest.mapper import MappingStatus, SourceMapping, identify
from snowobs_ingest.profiler import FileFormat, FileProfile, profile_file
from snowobs_ingest.tenancy import tenant_root, validate_tenant
from snowobs_ingest.validator import QualityReport, RowValidator
from snowobs_semantics.registry import ColumnType, SourceRegistry, default_registry

logger = get_logger(__name__)

BATCH_ROWS = 50_000
INGEST_COLUMNS = ("_LOADED_AT", "_SOURCE_VIEW", "_BATCH_ID", "_ACCOUNT")


@dataclass
class DatasetVersion:
    """One landed batch for one source."""

    source_id: str
    batch_id: str
    loaded_at: datetime
    file_name: str
    rows: int
    window_start: date | None
    window_end: date | None
    parquet_path: str
    #: The Snowflake account this batch was extracted from, if the uploader
    #: said. ``None`` means "not recorded" — never a guess.
    account: str | None = None


@dataclass
class IngestResult:
    """Outcome of ingesting one file."""

    file_name: str
    mapping: SourceMapping
    report: QualityReport | None = None
    version: DatasetVersion | None = None

    @property
    def landed(self) -> bool:
        return self.version is not None

    @property
    def needs_confirmation(self) -> bool:
        return self.mapping.status is MappingStatus.NEEDS_CONFIRMATION


@dataclass
class IngestSummary:
    """Outcome of ingesting an upload (a folder or zip of files)."""

    results: list[IngestResult] = field(default_factory=list)

    @property
    def landed(self) -> list[IngestResult]:
        return [r for r in self.results if r.landed]

    @property
    def pending_confirmation(self) -> list[IngestResult]:
        return [r for r in self.results if r.needs_confirmation and not r.landed]

    @property
    def unrecognised(self) -> list[IngestResult]:
        return [r for r in self.results if r.mapping.status is MappingStatus.UNRECOGNISED]

    @property
    def total_rows(self) -> int:
        return sum(r.version.rows for r in self.landed if r.version)

    def source_ids(self) -> set[str]:
        return {r.version.source_id for r in self.landed if r.version}

    def accounts(self) -> set[str]:
        """Accounts this upload was tagged with (empty when none was given)."""
        return {r.version.account for r in self.landed if r.version and r.version.account}


def _rows_from_profile(profile: FileProfile) -> Iterator[dict[str, Any]]:
    """Stream every row of a profiled file (not just the sample)."""
    if profile.file_format is FileFormat.PARQUET:
        import pyarrow.parquet as pq

        parquet_file = pq.ParquetFile(profile.path)
        for batch in parquet_file.iter_batches(batch_size=10_000):
            yield from batch.to_pylist()
        return

    opener = (
        gzip.open(profile.path, "rt", encoding=profile.encoding, newline="")
        if profile.compressed
        else profile.path.open("r", encoding=profile.encoding, newline="")
    )
    with opener as handle:
        if profile.file_format is FileFormat.NDJSON:
            for line in handle:
                line = line.strip()
                if line:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(record, dict):
                        yield record
            return
        text = handle.read()
    reader = csv.DictReader(io.StringIO(text), delimiter=profile.delimiter or ",")
    yield from reader


def _arrow_schema(source_id: str, registry: SourceRegistry, extra: list[str]) -> Any:
    """Build an Arrow schema from the registry — DECIMAL for numbers, never float."""
    import pyarrow as pa

    type_map = {
        ColumnType.STRING: pa.string(),
        ColumnType.VARIANT: pa.string(),
        ColumnType.INTEGER: pa.int64(),
        ColumnType.NUMBER: pa.decimal128(38, 9),
        ColumnType.BOOLEAN: pa.bool_(),
        ColumnType.DATE: pa.string(),
        ColumnType.TIMESTAMP_LTZ: pa.string(),
        ColumnType.TIMESTAMP_NTZ: pa.string(),
        ColumnType.TIMESTAMP_TZ: pa.string(),
    }
    source = registry.get(source_id)
    fields = [pa.field(column.name.upper(), type_map[column.type]) for column in source.columns]
    fields.extend(pa.field(name.upper(), pa.string()) for name in extra)
    fields.extend(pa.field(name, pa.string()) for name in INGEST_COLUMNS)
    return pa.schema(fields)


def _quantise_decimals(rows: list[dict[str, Any]], schema: Any) -> None:
    """Arrow's decimal128(38,9) requires exactly-scaled Decimals."""
    import pyarrow as pa

    decimal_columns = [field.name for field in schema if pa.types.is_decimal(field.type)]
    if not decimal_columns:
        return
    exponent = Decimal("0.000000001")
    for row in rows:
        for column in decimal_columns:
            value = row.get(column)
            if isinstance(value, Decimal):
                row[column] = value.quantize(exponent)
            elif isinstance(value, int | float) and value is not None:
                row[column] = Decimal(str(value)).quantize(exponent)


class LakeWriter:
    """Writes validated rows to Parquet under the storage root."""

    def __init__(
        self, storage_root: Path, tenant: str = "default", account: str | None = None
    ) -> None:
        self.storage_root = storage_root
        # The write side needs the same guard as the read side: a traversal
        # here would land one customer's extract inside another's prefix.
        self.tenant = validate_tenant(tenant)
        #: Default account stamped on batches this writer lands. Unlike the
        #: tenant it is not a path segment, so it is recorded verbatim.
        self.account = account

    def path_for(self, source_id: str, batch_id: str) -> Path:
        return tenant_root(self.storage_root, self.tenant) / source_id / f"part-{batch_id}.parquet"

    def write(
        self,
        source_id: str,
        rows: list[dict[str, Any]],
        batch_id: str,
        registry: SourceRegistry,
        extra_columns: list[str],
        account: str | None = None,
    ) -> Path:
        import pyarrow as pa
        import pyarrow.parquet as pq

        schema = _arrow_schema(source_id, registry, extra_columns)
        loaded_at = datetime.now(tz=UTC).isoformat()
        stamped_account = account if account is not None else self.account
        for row in rows:
            row["_LOADED_AT"] = loaded_at
            row["_SOURCE_VIEW"] = registry.get(source_id).snowflake_object
            row["_BATCH_ID"] = batch_id
            # Never derived from the rows: an ACCOUNT_USAGE extract carries no
            # account column, and guessing one would be a fabricated figure.
            row["_ACCOUNT"] = stamped_account
        _quantise_decimals(rows, schema)

        normalised = [{field.name: row.get(field.name) for field in schema} for row in rows]
        table = pa.Table.from_pylist(normalised, schema=schema)
        path = self.path_for(source_id, batch_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, path, compression="zstd")
        return path


class IngestPipeline:
    """profile → identify → map → validate → land → drift (§7.3)."""

    def __init__(
        self,
        storage_root: Path,
        registry: SourceRegistry | None = None,
        tenant: str = "default",
        account: str | None = None,
    ) -> None:
        self.registry = registry or default_registry()
        self.writer = LakeWriter(storage_root, tenant, account)
        self.tenant = tenant
        self.account = account
        self.versions: list[DatasetVersion] = []

    def ingest_file(
        self,
        path: Path,
        *,
        confirmed_source_id: str | None = None,
        account: str | None = None,
    ) -> IngestResult:
        """Ingest one file. ``confirmed_source_id`` applies a human decision.

        ``account`` names the Snowflake account this file was extracted from and
        is stamped on every landed row as ``_ACCOUNT``; it overrides the
        pipeline's own default for this file only.
        """
        profile = profile_file(path)
        mapping = identify(profile, self.registry)

        if confirmed_source_id is not None:
            from snowobs_ingest.mapper import _build_mapping

            mapping = _build_mapping(
                profile,
                self.registry.get(confirmed_source_id),
                MappingStatus.CONFIRMED,
                1.0,
                mapping.candidates,
                reason="Confirmed by an operator",
            )

        if mapping.status is not MappingStatus.CONFIRMED or mapping.source_id is None:
            logger.info(
                "ingest_needs_decision",
                file=path.name,
                status=mapping.status.value,
                reason=mapping.reason,
            )
            return IngestResult(file_name=path.name, mapping=mapping)

        source = self.registry.get(mapping.source_id)
        validator = RowValidator(mapping, source)
        accepted: list[dict[str, Any]] = []
        for row_number, raw in enumerate(_rows_from_profile(profile), start=1):
            row = validator.process(raw, row_number)
            if row is not None:
                accepted.append(row)

        report = validator.report
        if not report.usable:
            logger.warning(
                "ingest_file_unusable",
                file=path.name,
                source=source.id,
                missing_required=report.missing_required_columns,
                rows_rejected=report.rows_rejected,
            )
            return IngestResult(file_name=path.name, mapping=mapping, report=report)

        batch_id = f"{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%S%f')}-{source.id}"
        stamped_account = account if account is not None else self.account
        parquet_path = self.writer.write(
            source.id,
            accepted,
            batch_id,
            self.registry,
            mapping.extra_columns,
            account=stamped_account,
        )
        window = report.window()
        version = DatasetVersion(
            source_id=source.id,
            batch_id=batch_id,
            loaded_at=datetime.now(tz=UTC),
            file_name=path.name,
            rows=len(accepted),
            window_start=window[0] if window else None,
            window_end=window[1] if window else None,
            parquet_path=str(parquet_path),
            account=stamped_account,
        )
        self.versions.append(version)
        logger.info(
            "ingest_file_landed",
            file=path.name,
            source=source.id,
            rows=len(accepted),
            rejected=report.rows_rejected,
            drift_columns=report.drift_new_columns,
            account=stamped_account,
        )
        return IngestResult(file_name=path.name, mapping=mapping, report=report, version=version)

    def ingest_directory(self, directory: Path, *, account: str | None = None) -> IngestSummary:
        """Ingest every data file in a directory (the upload path).

        ``account`` tags every row landed from this directory with the account
        it was extracted from — one directory per account is how an enterprise
        exports, so it is also how the platform records provenance.
        """
        summary = IngestSummary()
        for path in sorted(directory.iterdir()):
            if not path.is_file():
                continue
            if path.name in {"03_manifest.json", "ground_truth.json"}:
                continue  # manifest is metadata, not a data file
            if path.suffix.lower() not in {
                ".csv",
                ".tsv",
                ".gz",
                ".parquet",
                ".json",
                ".ndjson",
            }:
                continue
            summary.results.append(self.ingest_file(path, account=account))
        return summary
