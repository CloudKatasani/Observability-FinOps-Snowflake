"""Validation and quarantine (step 4 of §7.3).

Rows that cannot be trusted are quarantined with a reason rather than dropped
or silently defaulted — a cost figure derived from a coerced-to-zero credit
column is worse than a missing one (R3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from snowobs_ingest.mapper import CoercionError, SourceMapping, coerce_value
from snowobs_semantics.registry import SourceDefinition


@dataclass
class RejectedRow:
    row_number: int
    reason: str
    column: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class QualityReport:
    """Per-file Data Quality Report surfaced in the UI."""

    source_id: str
    file_name: str
    rows_read: int = 0
    rows_accepted: int = 0
    rows_rejected: int = 0
    duplicate_grain_rows: int = 0
    missing_required_columns: list[str] = field(default_factory=list)
    missing_optional_columns: list[str] = field(default_factory=list)
    drift_new_columns: list[str] = field(default_factory=list)
    min_timestamp: str | None = None
    max_timestamp: str | None = None
    rejects: list[RejectedRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def accepted_ratio(self) -> float:
        return self.rows_accepted / self.rows_read if self.rows_read else 0.0

    @property
    def usable(self) -> bool:
        """A file is usable when required columns are present and some rows landed."""
        return not self.missing_required_columns and self.rows_accepted > 0

    def window(self) -> tuple[date, date] | None:
        if not self.min_timestamp or not self.max_timestamp:
            return None
        return date.fromisoformat(self.min_timestamp[:10]), date.fromisoformat(
            self.max_timestamp[:10]
        )


MAX_RETAINED_REJECTS = 500


class RowValidator:
    """Coerces and validates rows for one mapped file."""

    def __init__(self, mapping: SourceMapping, source: SourceDefinition) -> None:
        self.mapping = mapping
        self.source = source
        self.report = QualityReport(
            source_id=source.id,
            file_name=mapping.profile.path.name,
            missing_required_columns=mapping.missing_required,
            missing_optional_columns=mapping.missing_optional,
            drift_new_columns=list(mapping.extra_columns),
            warnings=list(mapping.profile.warnings),
        )
        self._seen_grain: set[tuple[Any, ...]] = set()
        self._grain_columns = [g.upper() for g in source.grain]
        self._time_column = source.time_column.upper() if source.time_column else None

    def process(self, raw_row: dict[str, Any], row_number: int) -> dict[str, Any] | None:
        """Return a coerced row, or None if it was quarantined."""
        self.report.rows_read += 1
        upper = {str(k).strip().upper(): v for k, v in raw_row.items()}
        coerced: dict[str, Any] = {}

        for column in self.mapping.columns:
            name = column.target.upper()
            if column.missing:
                # Missing optional columns are back-filled from the registry
                # default; missing required columns disable the source entirely
                # and are reported rather than defaulted.
                coerced[name] = column.default
                continue
            value = upper.get(name)
            try:
                converted = coerce_value(value, column.target_type)
            except CoercionError as exc:
                self._reject(row_number, str(exc), column.target, raw_row)
                return None
            if converted is None and column.required:
                self._reject(row_number, "required value is null", column.target, raw_row)
                return None
            if converted is None and column.default is not None:
                converted = column.default
            coerced[name] = converted

        # Drift: new columns are absorbed additively so a schema change never
        # loses data, and are recorded for the drift log.
        declared = {c.target.upper() for c in self.mapping.columns}
        for column_name, value in upper.items():
            if column_name not in declared and column_name:
                coerced[column_name] = None if value == "" else str(value)

        if self._grain_columns:
            grain_key = tuple(coerced.get(column) for column in self._grain_columns)
            if any(part is None for part in grain_key):
                self._reject(row_number, "null in grain column", None, raw_row)
                return None
            if grain_key in self._seen_grain:
                self.report.duplicate_grain_rows += 1
                return None  # last-write-wins is applied at merge time, not here
            self._seen_grain.add(grain_key)

        if self._time_column:
            stamp = coerced.get(self._time_column)
            if isinstance(stamp, str) and stamp:
                if self.report.min_timestamp is None or stamp < self.report.min_timestamp:
                    self.report.min_timestamp = stamp
                if self.report.max_timestamp is None or stamp > self.report.max_timestamp:
                    self.report.max_timestamp = stamp

        self.report.rows_accepted += 1
        return coerced

    def _reject(
        self, row_number: int, reason: str, column: str | None, raw: dict[str, Any]
    ) -> None:
        self.report.rows_rejected += 1
        if len(self.report.rejects) < MAX_RETAINED_REJECTS:
            self.report.rejects.append(
                RejectedRow(row_number=row_number, reason=reason, column=column, raw=dict(raw))
            )
