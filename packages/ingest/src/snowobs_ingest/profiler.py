"""File profiling: format, encoding, delimiter, header, and a typed sample.

Step 1 of the ingestion pipeline (§7.3). Profiling never trusts the file
extension alone — a ``.csv`` may be TSV, gzipped, UTF-16, or BOM-prefixed, and
an assessor's upload is exactly where those show up.
"""

from __future__ import annotations

import csv
import gzip
import io
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from snowobs_common.errors import AppError

SAMPLE_ROWS = 1_000
_SNIFF_BYTES = 64 * 1024
_DELIMITERS = ",;\t|"


class FileFormat(StrEnum):
    CSV = "csv"
    TSV = "tsv"
    PARQUET = "parquet"
    NDJSON = "ndjson"


class UnreadableFileError(AppError):
    status_code = 422
    title = "File could not be profiled"
    problem_type = "https://snowobs.dev/problems/unreadable-file"


@dataclass
class FileProfile:
    """Everything the mapper needs to read a file correctly."""

    path: Path
    file_format: FileFormat
    compressed: bool
    encoding: str
    delimiter: str | None
    header: list[str]
    sample_rows: list[dict[str, str]] = field(default_factory=list)
    size_bytes: int = 0
    #: Non-fatal observations surfaced in the Data Quality Report.
    warnings: list[str] = field(default_factory=list)

    @property
    def column_count(self) -> int:
        return len(self.header)


def _decode(raw: bytes) -> tuple[str, str]:
    """Return (text, encoding), handling BOMs and UTF-16 exports."""
    for bom, encoding in (
        (b"\xff\xfe\x00\x00", "utf-32"),
        (b"\x00\x00\xfe\xff", "utf-32"),
        (b"\xff\xfe", "utf-16"),
        (b"\xfe\xff", "utf-16"),
        (b"\xef\xbb\xbf", "utf-8-sig"),
    ):
        if raw.startswith(bom):
            return raw.decode(encoding), encoding
    # Snowsight downloads with non-ASCII object names are occasionally latin-1.
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise UnreadableFileError("File is not decodable as UTF-8, UTF-16, or Latin-1")


def _sniff_delimiter(sample: str, path: Path) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=_DELIMITERS).delimiter
    except csv.Error:
        # Sniffer fails on single-column or ragged samples; fall back to the
        # most frequent candidate on the header line, then the extension.
        first_line = sample.splitlines()[0] if sample.splitlines() else ""
        counts = {d: first_line.count(d) for d in _DELIMITERS}
        best = max(counts, key=lambda d: counts[d])
        if counts[best] > 0:
            return best
        return "\t" if path.name.endswith((".tsv", ".tsv.gz")) else ","


def _read_head(path: Path, compressed: bool) -> bytes:
    opener = gzip.open if compressed else open
    try:
        with opener(path, "rb") as handle:
            return handle.read(_SNIFF_BYTES)
    except (OSError, EOFError) as exc:
        raise UnreadableFileError(f"Could not read {path.name}: {exc}") from exc


def profile_file(path: Path) -> FileProfile:
    """Profile a single uploaded file."""
    if not path.is_file():
        raise UnreadableFileError(f"Not a file: {path}")
    size = path.stat().st_size
    if size == 0:
        raise UnreadableFileError(f"{path.name} is empty")

    name = path.name.lower()
    compressed = name.endswith(".gz")

    if name.endswith(".parquet"):
        return _profile_parquet(path, size)
    if name.endswith((".json", ".ndjson", ".json.gz", ".ndjson.gz")):
        return _profile_ndjson(path, size, compressed)
    return _profile_delimited(path, size, compressed)


def _profile_parquet(path: Path, size: int) -> FileProfile:
    import pyarrow.parquet as pq

    try:
        parquet_file = pq.ParquetFile(path)
    except Exception as exc:
        raise UnreadableFileError(f"{path.name} is not readable as Parquet: {exc}") from exc

    header = list(parquet_file.schema_arrow.names)
    sample: list[dict[str, str]] = []
    for batch in parquet_file.iter_batches(batch_size=min(SAMPLE_ROWS, 1024)):
        for row in batch.to_pylist():
            sample.append({k: "" if v is None else str(v) for k, v in row.items()})
            if len(sample) >= SAMPLE_ROWS:
                break
        if len(sample) >= SAMPLE_ROWS:
            break

    return FileProfile(
        path=path,
        file_format=FileFormat.PARQUET,
        compressed=False,  # Parquet carries its own internal compression
        encoding="binary",
        delimiter=None,
        header=header,
        sample_rows=sample,
        size_bytes=size,
    )


def _profile_ndjson(path: Path, size: int, compressed: bool) -> FileProfile:
    import json

    text, encoding = _decode(_read_head(path, compressed))
    header: list[str] = []
    sample: list[dict[str, str]] = []
    warnings: list[str] = []
    for line in text.splitlines()[:SAMPLE_ROWS]:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            warnings.append("Truncated or malformed JSON line in sample")
            continue
        if not isinstance(record, dict):
            raise UnreadableFileError(f"{path.name}: NDJSON records must be objects")
        for key in record:
            if key not in header:
                header.append(key)
        sample.append({k: "" if v is None else str(v) for k, v in record.items()})
    if not header:
        raise UnreadableFileError(f"{path.name}: no readable JSON records")
    return FileProfile(
        path=path,
        file_format=FileFormat.NDJSON,
        compressed=compressed,
        encoding=encoding,
        delimiter=None,
        header=header,
        sample_rows=sample,
        size_bytes=size,
        warnings=warnings,
    )


def _profile_delimited(path: Path, size: int, compressed: bool) -> FileProfile:
    text, encoding = _decode(_read_head(path, compressed))
    if not text.strip():
        raise UnreadableFileError(f"{path.name} contains no readable text")

    delimiter = _sniff_delimiter(text[:8192], path)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header_row = next(reader)
    except StopIteration:
        raise UnreadableFileError(f"{path.name} has no header row") from None

    header = [column.strip().lstrip("﻿") for column in header_row]
    warnings: list[str] = []
    if any(not column for column in header):
        warnings.append("Header contains blank column names")
    if len(set(header)) != len(header):
        warnings.append("Header contains duplicate column names")

    sample: list[dict[str, str]] = []
    for row in reader:
        if len(sample) >= SAMPLE_ROWS:
            break
        if len(row) != len(header):
            warnings.append("Ragged row detected in sample (column count mismatch)")
            continue
        sample.append(dict(zip(header, row, strict=True)))

    return FileProfile(
        path=path,
        file_format=FileFormat.TSV if delimiter == "\t" else FileFormat.CSV,
        compressed=compressed,
        encoding=encoding,
        delimiter=delimiter,
        header=header,
        sample_rows=sample,
        size_bytes=size,
        warnings=sorted(set(warnings)),
    )
