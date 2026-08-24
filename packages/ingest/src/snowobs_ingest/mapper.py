"""Source identification and column mapping (steps 2–3 of §7.3).

Identification is deliberately conservative: a cost-bearing source is never
guessed silently. Anything below the confidence floor goes to the human
confirmation queue with its candidate shortlist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from snowobs_ingest.profiler import FileProfile
from snowobs_semantics.registry import (
    ColumnType,
    SourceDefinition,
    SourceMatch,
    SourceRegistry,
)

#: Below this header-signature score a match is never applied automatically.
AUTO_CONFIRM_THRESHOLD = 0.9
#: Below this, no candidate is offered at all.
CANDIDATE_THRESHOLD = 0.7


class MappingStatus(StrEnum):
    CONFIRMED = "confirmed"  # unambiguous: filename alias or a single strong header match
    NEEDS_CONFIRMATION = "needs_confirmation"  # candidates exist, human must choose
    UNRECOGNISED = "unrecognised"  # nothing plausible


@dataclass
class ColumnMapping:
    """One registry column and the file column that satisfies it."""

    target: str
    source_column: str | None
    target_type: ColumnType
    required: bool
    default: str | int | float | bool | None = None

    @property
    def missing(self) -> bool:
        return self.source_column is None


@dataclass
class SourceMapping:
    """The decision about what a file is and how to read it."""

    profile: FileProfile
    status: MappingStatus
    source_id: str | None
    confidence: float
    candidates: list[SourceMatch] = field(default_factory=list)
    columns: list[ColumnMapping] = field(default_factory=list)
    #: Columns present in the file but absent from the registry (drift).
    extra_columns: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def missing_required(self) -> list[str]:
        return [c.target for c in self.columns if c.required and c.missing]

    @property
    def missing_optional(self) -> list[str]:
        return [c.target for c in self.columns if not c.required and c.missing]


def identify(profile: FileProfile, registry: SourceRegistry) -> SourceMapping:
    """Match a profiled file to a registered source."""
    by_name = registry.match_filename(profile.path.name)
    header_matches = registry.match_header(profile.header, threshold=CANDIDATE_THRESHOLD)

    if by_name is not None:
        source = registry.get(by_name.source_id)
        # A filename alias is strong evidence, but the header must corroborate
        # it — a renamed file must not smuggle the wrong schema into a
        # cost-bearing source.
        corroboration = next(
            (m.confidence for m in header_matches if m.source_id == by_name.source_id), 0.0
        )
        if corroboration >= CANDIDATE_THRESHOLD:
            return _build_mapping(
                profile,
                source,
                MappingStatus.CONFIRMED,
                1.0,
                header_matches,
                reason="Filename alias corroborated by header signature",
            )
        return SourceMapping(
            profile=profile,
            status=MappingStatus.NEEDS_CONFIRMATION,
            source_id=by_name.source_id,
            confidence=corroboration,
            candidates=header_matches or [by_name],
            reason=(
                f"Filename suggests '{by_name.source_id}' but the header does not match it "
                f"(signature coverage {corroboration:.0%})"
            ),
        )

    if not header_matches:
        return SourceMapping(
            profile=profile,
            status=MappingStatus.UNRECOGNISED,
            source_id=None,
            confidence=0.0,
            reason="No registered source matches this file's name or header",
        )

    best = header_matches[0]
    runner_up = header_matches[1].confidence if len(header_matches) > 1 else 0.0
    unambiguous = best.confidence >= AUTO_CONFIRM_THRESHOLD and best.confidence > runner_up

    source = registry.get(best.source_id)
    if unambiguous:
        return _build_mapping(
            profile,
            source,
            MappingStatus.CONFIRMED,
            best.confidence,
            header_matches,
            reason="Header signature matched a single source",
        )
    return _build_mapping(
        profile,
        source,
        MappingStatus.NEEDS_CONFIRMATION,
        best.confidence,
        header_matches,
        reason=(
            "Header matched more than one source or matched weakly; "
            "confirm before loading a cost-bearing source"
        ),
    )


def _build_mapping(
    profile: FileProfile,
    source: SourceDefinition,
    status: MappingStatus,
    confidence: float,
    candidates: list[SourceMatch],
    *,
    reason: str,
) -> SourceMapping:
    columns = map_columns(profile, source)
    declared = {c.name.upper() for c in source.columns}
    extra = [c for c in profile.header if c.strip().upper() not in declared and c.strip()]
    return SourceMapping(
        profile=profile,
        status=status,
        source_id=source.id,
        confidence=confidence,
        candidates=candidates,
        columns=columns,
        extra_columns=extra,
        reason=reason,
    )


def map_columns(profile: FileProfile, source: SourceDefinition) -> list[ColumnMapping]:
    """Case-insensitive column matching against the registry definition."""
    available = {column.strip().upper(): column for column in profile.header if column.strip()}
    mappings: list[ColumnMapping] = []
    for column in source.columns:
        mappings.append(
            ColumnMapping(
                target=column.name,
                source_column=available.get(column.name.upper()),
                target_type=column.type,
                required=column.required,
                default=column.default,
            )
        )
    return mappings


# ------------------------------------------------------------------ coercion
_TRUE = {"true", "t", "yes", "y", "1"}
_FALSE = {"false", "f", "no", "n", "0"}
_TS_TRAILING = (" -", " +")


class CoercionError(ValueError):
    """A value could not be coerced to its declared type."""


def coerce_value(raw: Any, target: ColumnType) -> Any:
    """Coerce one raw string to the registry-declared type.

    Credits and currency go through :class:`~decimal.Decimal` — never float
    (§27.7). Empty strings become ``None`` so a missing figure stays unknown
    rather than becoming a zero (R3).
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.strip()
        if raw == "" or raw.upper() in {"NULL", "\\N"}:
            return None
    match target:
        case ColumnType.STRING | ColumnType.VARIANT:
            return str(raw)
        case ColumnType.INTEGER:
            try:
                return int(Decimal(str(raw)))
            except (InvalidOperation, ValueError) as exc:
                raise CoercionError(f"not an integer: {raw!r}") from exc
        case ColumnType.NUMBER:
            try:
                return Decimal(str(raw))
            except InvalidOperation as exc:
                raise CoercionError(f"not a number: {raw!r}") from exc
        case ColumnType.BOOLEAN:
            text = str(raw).strip().lower()
            if text in _TRUE:
                return True
            if text in _FALSE:
                return False
            raise CoercionError(f"not a boolean: {raw!r}")
        case ColumnType.DATE:
            return _coerce_date(raw)
        case _:
            return _coerce_timestamp(raw)


def _coerce_date(raw: Any) -> str:
    from datetime import date, datetime

    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw.isoformat()
    text = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()  # noqa: DTZ007
        except ValueError:
            continue
    # Timestamps are acceptable input for a date column; take the date part.
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    raise CoercionError(f"not a date: {raw!r}")


def _coerce_timestamp(raw: Any) -> str:
    """Normalise Snowflake timestamp string forms to ISO-8601 text.

    Timestamps are kept as text through landing and parsed by the engine; this
    keeps one representation across CSV, Parquet, and both SQL dialects.
    """
    from datetime import UTC, datetime

    if isinstance(raw, datetime):
        return raw.isoformat(sep=" ")
    text = str(raw).strip()
    if not text:
        raise CoercionError("empty timestamp")

    # Epoch variants (seconds / milliseconds / microseconds since 1970).
    if text.lstrip("-").isdigit():
        value = int(text)
        for divisor in (1, 1_000, 1_000_000):
            candidate = value / divisor
            if 0 < candidate < 4_102_444_800:  # < year 2100
                return (
                    datetime.fromtimestamp(candidate, tz=UTC)
                    .replace(tzinfo=None)
                    .isoformat(sep=" ", timespec="milliseconds")
                )
        raise CoercionError(f"epoch out of range: {raw!r}")

    normalised = text.replace("T", " ")
    # Strip a trailing zone offset ("… -07:00") — the offset is preserved by the
    # source registry's declared type, and the app renders in account time.
    for marker in _TS_TRAILING:
        index = normalised.rfind(marker)
        if index > 10:
            normalised = normalised[:index]
            break
    if normalised.endswith("Z"):
        normalised = normalised[:-1]
    normalised = normalised.strip()

    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(normalised, fmt).isoformat(  # noqa: DTZ007
                sep=" ", timespec="milliseconds"
            )
        except ValueError:
            continue
    raise CoercionError(f"not a timestamp: {raw!r}")
