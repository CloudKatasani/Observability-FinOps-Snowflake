"""The data product record and its vocabulary (BUILD_PROMPT §13.1).

A data product is a *declaration*, not code: which governed metrics it exposes,
at what grain, to whom, with what freshness and availability promise, and under
whose ownership. Everything downstream — the contract, the emitted artifacts,
the publish gate — is derived from this declaration plus the semantic layer, so
a product cannot promise a column the metric layer does not produce (R1, R5).

Nothing here reaches into Snowflake. A product is metadata about telemetry the
customer already owns; the platform never becomes its system of record (R2).
"""

from __future__ import annotations

import re
from decimal import Decimal
from enum import StrEnum
from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

#: Product, dataset, and column identifiers are lower-snake slugs.
SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class Bump(StrEnum):
    """How far a version moved, and how far a set of changes requires it to move."""

    NONE = "none"
    PATCH = "patch"
    MINOR = "minor"
    MAJOR = "major"

    @property
    def rank(self) -> int:
        return {Bump.NONE: 0, Bump.PATCH: 1, Bump.MINOR: 2, Bump.MAJOR: 3}[self]

    def covers(self, required: Bump) -> bool:
        """True when this bump is at least as large as the one required."""
        return self.rank >= required.rank


class Version(BaseModel):
    """A semantic version. Parses from and serialises to ``"1.2.3"``."""

    model_config = ConfigDict(frozen=True)

    major: int = Field(ge=0)
    minor: int = Field(ge=0)
    patch: int = Field(ge=0)

    @model_validator(mode="before")
    @classmethod
    def _parse(cls, value: Any) -> Any:
        if isinstance(value, str):
            match = _VERSION_PATTERN.match(value.strip())
            if match is None:
                raise ValueError(f"not a semantic version: {value!r}")
            return {
                "major": int(match.group(1)),
                "minor": int(match.group(2)),
                "patch": int(match.group(3)),
            }
        return value

    @model_serializer
    def _serialize(self) -> str:
        return str(self)

    @classmethod
    def parse(cls, value: str) -> Version:
        return cls.model_validate(value)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def key(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def __lt__(self, other: Version) -> bool:
        return self.key < other.key

    def __le__(self, other: Version) -> bool:
        return self.key <= other.key

    def __gt__(self, other: Version) -> bool:
        return self.key > other.key

    def __ge__(self, other: Version) -> bool:
        return self.key >= other.key

    def bump_from(self, previous: Version) -> Bump:
        """Classify the move from ``previous`` to this version.

        A version that did not move forward is :attr:`Bump.NONE` — including a
        version that moved *backwards*, which the publish gate refuses.
        """
        if self <= previous:
            return Bump.NONE
        if self.major > previous.major:
            return Bump.MAJOR
        if self.minor > previous.minor:
            return Bump.MINOR
        return Bump.PATCH


class Lifecycle(StrEnum):
    """Product lifecycle (§13.1). Transitions are recorded, never implicit (R8)."""

    DRAFT = "draft"
    PROPOSED = "proposed"
    APPROVED = "approved"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class Classification(StrEnum):
    """Sensitivity of the product's contents, ordered least to most restricted."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"

    @property
    def requires_masking(self) -> bool:
        return self in (Classification.CONFIDENTIAL, Classification.RESTRICTED)


class RefreshCadence(BaseModel):
    """How often the published objects are rebuilt."""

    model_config = ConfigDict(frozen=True)

    #: Rebuild interval, and the value used for ``TARGET_LAG`` on Snowflake objects.
    target_lag_minutes: int = Field(ge=1)
    #: The schedule an orchestrator runs the rebuild on, as a 5-field cron string.
    cron: str
    description: str = ""

    @field_validator("cron")
    @classmethod
    def _five_fields(cls, cron: str) -> str:
        if len(cron.split()) != 5:
            raise ValueError(f"cron must have five fields, got {cron!r}")
        return cron

    @property
    def target_lag_clause(self) -> str:
        """The Snowflake ``TARGET_LAG`` literal for this cadence."""
        if self.target_lag_minutes % (60 * 24) == 0:
            days = self.target_lag_minutes // (60 * 24)
            return f"{days} day{'s' if days > 1 else ''}"
        if self.target_lag_minutes % 60 == 0:
            hours = self.target_lag_minutes // 60
            return f"{hours} hour{'s' if hours > 1 else ''}"
        return f"{self.target_lag_minutes} minutes"


class ProductSla(BaseModel):
    """The promise made to consumers, and the notice period for withdrawing it."""

    model_config = ConfigDict(frozen=True)

    #: Maximum age of the newest row a consumer may see. Must be achievable given
    #: the documented latency of the slowest source (R7) — the publish gate checks.
    freshness_target_minutes: int = Field(ge=1)
    availability_pct: Decimal = Field(gt=Decimal(0), le=Decimal(100))
    retention_days: int = Field(ge=1)
    support_channel: str
    #: §13.3: a breaking change needs a deprecation window, not just a major bump.
    deprecation_notice_days: int = Field(default=90, ge=1)

    @field_validator("availability_pct", mode="before")
    @classmethod
    def _decimal_only(cls, value: Any) -> Any:
        # §27.7: never let a float in — even for a percentage that sits beside
        # currency figures in the same contract document.
        if isinstance(value, float):
            raise ValueError("availability_pct must be a string or Decimal, never a float")
        return value


class Consumer(BaseModel):
    """A registered consumer of the product (§13.1 subscribers)."""

    model_config = ConfigDict(frozen=True)

    name: str
    contact: str
    purpose: str
    #: Snowflake account locator or role the consumer reads through.
    grantee: str | None = None


class RowExpectation(BaseModel):
    """Row-count expectation for one dataset, per day of the product's window."""

    model_config = ConfigDict(frozen=True)

    entity: str
    min_rows_per_day: int = Field(default=1, ge=0)
    max_rows_per_day: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.max_rows_per_day is not None and self.max_rows_per_day < self.min_rows_per_day:
            raise ValueError("max_rows_per_day is below min_rows_per_day")
        return self


class SearchSpec(BaseModel):
    """The text column a Cortex Search service indexes, and its filters."""

    model_config = ConfigDict(frozen=True)

    #: Contract column name (uppercase) the service is built ``ON``.
    column: str
    #: Contract columns exposed as filterable ``ATTRIBUTES``.
    attributes: list[str] = Field(default_factory=list)
    #: How far back the indexed window reaches.
    window_days: int = Field(default=30, ge=1)


class Relationship(BaseModel):
    """A declared join between two of the product's datasets (semantic view).

    Relationships are declared, never inferred — an invented join between two
    facts multiplies every figure that crosses it.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    from_entity: str
    from_columns: list[str] = Field(min_length=1)
    to_entity: str
    to_columns: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _same_arity(self) -> Self:
        if len(self.from_columns) != len(self.to_columns):
            raise ValueError(f"relationship {self.name}: column counts differ")
        return self


class ChangeLogEntry(BaseModel):
    """One released version and what it changed (§13.1 change_history)."""

    model_config = ConfigDict(frozen=True)

    version: Version
    released_on: str
    summary: str
    breaking: bool = False
    migration_note: str | None = None

    @model_validator(mode="after")
    def _breaking_needs_a_migration_note(self) -> Self:
        # §13.3: removing or retyping a contracted column requires a migration
        # note. A breaking release without one is not releasable.
        if self.breaking and not (self.migration_note or "").strip():
            raise ValueError(f"version {self.version} is breaking and needs a migration_note")
        return self


class DataProduct(BaseModel):
    """One data product record (§13.1)."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    version: Version
    owner: str
    domain: str
    description: str
    status: Lifecycle = Lifecycle.DRAFT
    classification: Classification = Classification.INTERNAL

    #: The boundary: governed metric ids this product exposes. Every id must
    #: exist in the semantic registry — the registry validates it, and there is
    #: a test that asserts exactly that.
    metrics: list[str] = Field(min_length=1)
    #: Dimensions the product publishes, in presentation order. Each is applied
    #: to every dataset whose entity can resolve it.
    dimensions: list[str] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)

    refresh: RefreshCadence
    sla: ProductSla
    consumers: list[Consumer] = Field(default_factory=list)
    row_expectations: list[RowExpectation] = Field(default_factory=list)
    #: Contract columns that carry personal or otherwise restricted values. They
    #: are masked in the published views and never indexed for text search.
    sensitive_columns: list[str] = Field(default_factory=list)
    search: SearchSpec | None = None

    documentation_url: str
    #: Marketplace listing categories.
    categories: list[str] = Field(default_factory=list)
    change_log: list[ChangeLogEntry] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not SLUG_PATTERN.match(value):
            raise ValueError(f"product id must be a lower-snake slug, got {value!r}")
        return value

    @field_validator("metrics", "dimensions", "sensitive_columns")
    @classmethod
    def _unique(cls, values: list[str]) -> list[str]:
        duplicates = sorted({v for v in values if values.count(v) > 1})
        if duplicates:
            raise ValueError(f"duplicate entries: {duplicates}")
        return values

    @field_validator("sensitive_columns")
    @classmethod
    def _upper(cls, values: list[str]) -> list[str]:
        return [v.upper() for v in values]

    @model_validator(mode="after")
    def _change_log_matches_version(self) -> Self:
        versions = [entry.version for entry in self.change_log]
        if len(versions) != len({str(v) for v in versions}):
            raise ValueError("duplicate versions in change_log")
        if versions and sorted(versions, key=lambda v: v.key) != versions:
            raise ValueError("change_log must be ordered oldest to newest")
        if versions and versions[-1] != self.version:
            raise ValueError(
                f"change_log ends at {versions[-1]} but the product declares {self.version}"
            )
        return self

    def row_expectation(self, entity: str) -> RowExpectation:
        """The declared expectation for an entity, or the default 'not empty'."""
        for expectation in self.row_expectations:
            if expectation.entity == entity:
                return expectation
        return RowExpectation(entity=entity)

    def is_sensitive(self, column: str) -> bool:
        return column.upper() in self.sensitive_columns

    @property
    def slug_upper(self) -> str:
        return self.id.upper()
