"""Canonical source registry (BUILD_PROMPT §7.1).

One YAML per Snowflake source object under ``packages/semantics/sources/``.
This registry is the only place source-view knowledge lives: latencies,
schedules, key columns, retention, edition requirements, and CSV import rules
all read from it. Adding a source view requires zero code changes.
"""

from __future__ import annotations

from collections.abc import Iterator
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from snowobs_common.errors import ConfigurationError
from snowobs_semantics import SOURCES_DIR


class ColumnType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"  # Snowflake NUMBER — mapped to DECIMAL(38,9), never float
    BOOLEAN = "boolean"
    TIMESTAMP_LTZ = "timestamp_ltz"
    TIMESTAMP_NTZ = "timestamp_ntz"
    TIMESTAMP_TZ = "timestamp_tz"
    DATE = "date"
    VARIANT = "variant"  # arrives as JSON text in CSV extracts


class Criticality(StrEnum):
    CORE = "core"
    IMPORTANT = "important"
    OPTIONAL = "optional"


class Edition(StrEnum):
    STANDARD = "standard"
    ENTERPRISE = "enterprise"
    BUSINESS_CRITICAL = "business_critical"


class LoadStrategy(StrEnum):
    INCREMENTAL_WATERMARK = "incremental_watermark"
    FULL_SNAPSHOT = "full_snapshot"


class SourceScope(StrEnum):
    """Whether a view describes one account or the whole organization.

    ACCOUNT_USAGE views are per-account and carry no account column at all, so
    which account an extract came from is knowledge the *platform* has to
    record. ORGANIZATION_USAGE views span the fleet and name the account in
    their own schema. The distinction drives ingest tagging, the coverage
    matrix, and which Snowflake role the extract needs.
    """

    ACCOUNT = "account"
    ORGANIZATION = "organization"


class SourceColumn(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    type: ColumnType
    required: bool = False
    default: str | int | float | bool | None = None


class WatermarkSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    column: str
    lookback_minutes: int = 180


class CsvRules(BaseModel):
    model_config = ConfigDict(frozen=True)

    header_signature: list[str]
    aliases: list[str] = Field(default_factory=list)


class SourceDefinition(BaseModel):
    """A registered Snowflake source object and how to read/import it."""

    model_config = ConfigDict(frozen=True)

    id: str
    snowflake_object: str
    domain: str
    criticality: Criticality
    #: Account-scoped by default: the great majority of registered views are
    #: ACCOUNT_USAGE, and defaulting keeps every existing YAML valid.
    scope: SourceScope = SourceScope.ACCOUNT
    edition_min: Edition = Edition.STANDARD
    required_db_role: str | None = None
    documented_latency_minutes: int
    latency_verified: bool = True  # False → re-check per ASSUMPTIONS U-1
    retention_days: int = 365
    grain: list[str]
    time_column: str | None = None
    watermark: WatermarkSpec | None = None
    load_strategy: LoadStrategy = LoadStrategy.INCREMENTAL_WATERMARK
    csv: CsvRules
    columns: list[SourceColumn]
    sensitivity: dict[str, str] = Field(default_factory=dict)
    enables_metrics: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("columns")
    @classmethod
    def _unique_columns(cls, columns: list[SourceColumn]) -> list[SourceColumn]:
        names = [c.name for c in columns]
        if len(names) != len(set(names)):
            raise ValueError("duplicate column names")
        return columns

    def column(self, name: str) -> SourceColumn | None:
        target = name.upper()
        for col in self.columns:
            if col.name.upper() == target:
                return col
        return None

    @property
    def required_columns(self) -> list[SourceColumn]:
        return [c for c in self.columns if c.required]

    @property
    def is_organization_scoped(self) -> bool:
        return self.scope is SourceScope.ORGANIZATION


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


class SourceMatch(BaseModel):
    source_id: str
    confidence: float
    matched_by: str  # "alias" | "header_signature"


class SourceRegistry(BaseModel):
    sources: dict[str, SourceDefinition]

    # BaseModel.__iter__ yields (name, value) pairs; iterating a registry
    # should yield the sources themselves, which is what every caller wants.
    def __iter__(self) -> Iterator[SourceDefinition]:  # type: ignore[override]
        return iter(self.sources.values())

    def __len__(self) -> int:
        return len(self.sources)

    def get(self, source_id: str) -> SourceDefinition:
        try:
            return self.sources[source_id]
        except KeyError:
            raise ConfigurationError(f"Unknown source id: {source_id}") from None

    def ids(self) -> list[str]:
        return sorted(self.sources)

    def scoped(self, scope: SourceScope) -> list[SourceDefinition]:
        """Every source at one scope, in id order."""
        return [
            self.sources[source_id]
            for source_id in self.ids()
            if self.sources[source_id].scope is scope
        ]

    def match_filename(self, filename: str) -> SourceMatch | None:
        """Match an uploaded file to a source by its filename aliases."""
        stem = Path(filename).name.lower()
        for suffix in (".gz", ".csv", ".tsv", ".parquet", ".json", ".ndjson"):
            stem = stem.removesuffix(suffix)
        stem = stem.strip("._-")
        for source in self.sources.values():
            candidates = {a.lower() for a in source.csv.aliases} | {source.id.lower()}
            if stem in candidates or any(stem.startswith(f"{c}_") for c in candidates):
                return SourceMatch(source_id=source.id, confidence=1.0, matched_by="alias")
        return None

    def match_header(self, header: list[str], threshold: float = 0.7) -> list[SourceMatch]:
        """Rank sources by Jaccard similarity of the header signature (§7.3)."""
        observed = {h.strip().upper() for h in header if h.strip()}
        matches: list[SourceMatch] = []
        for source in self.sources.values():
            signature = {c.upper() for c in source.csv.header_signature}
            # Jaccard of the observed header restricted to signature columns —
            # i.e. signature coverage. Plain Jaccard over the full header would
            # dilute below threshold for real exports, whose many extra columns
            # are expected and absorbed additively by drift handling.
            score = _jaccard(observed & signature, signature)
            if score >= threshold:
                matches.append(
                    SourceMatch(
                        source_id=source.id,
                        confidence=round(score, 4),
                        matched_by="header_signature",
                    )
                )
        matches.sort(key=lambda m: (-m.confidence, m.source_id))
        return matches


def load_registry(directory: Path | None = None) -> SourceRegistry:
    """Load and validate every source YAML in the directory."""
    directory = directory or SOURCES_DIR
    if not directory.is_dir():
        raise ConfigurationError(f"Source registry directory not found: {directory}")
    sources: dict[str, SourceDefinition] = {}
    for path in sorted(directory.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            definition = SourceDefinition.model_validate(raw)
        except Exception as exc:
            raise ConfigurationError(f"Invalid source definition {path.name}: {exc}") from exc
        if definition.id in sources:
            raise ConfigurationError(f"Duplicate source id {definition.id} in {path.name}")
        if path.stem != definition.id:
            raise ConfigurationError(
                f"{path.name}: filename must match source id '{definition.id}'"
            )
        sources[definition.id] = definition
    if not sources:
        raise ConfigurationError(f"No source definitions found in {directory}")
    return SourceRegistry(sources=sources)


@lru_cache(maxsize=1)
def default_registry() -> SourceRegistry:
    return load_registry()
