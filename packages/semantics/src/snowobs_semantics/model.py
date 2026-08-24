"""Entity and metric definitions — the validated IR (BUILD_PROMPT §8.2, §9.1).

Entities describe the curated star schema (facts and dimensions, their grain,
their source, and how they join). Metrics describe aggregations over one
entity. Both are declared once in YAML and compiled to either dialect; there is
no engine-specific business logic anywhere in this module (R1).
"""

from __future__ import annotations

from collections.abc import Iterator
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from snowobs_common.errors import ConfigurationError
from snowobs_semantics import ENTITIES_DIR, METRICS_DIR


class EntityKind(StrEnum):
    FACT = "fact"
    DIMENSION = "dimension"


class TimeGrain(StrEnum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class Direction(StrEnum):
    LOWER_IS_BETTER = "lower_is_better"
    HIGHER_IS_BETTER = "higher_is_better"
    NEUTRAL = "neutral"


class FormatType(StrEnum):
    NUMBER = "number"
    CURRENCY = "currency"
    PERCENT = "percent"
    DURATION_MS = "duration_ms"
    BYTES = "bytes"
    INTEGER = "integer"


class MetricFormat(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: FormatType = FormatType.NUMBER
    decimals: int = 1
    unit: str | None = None


class DimensionRef(BaseModel):
    """A dimension a metric can be sliced by, and how to reach it."""

    model_config = ConfigDict(frozen=True)

    name: str
    #: SQL expression evaluated against the entity's own columns.
    expression: str
    description: str | None = None


class JoinSpec(BaseModel):
    """A declared relationship. Join graphs are declared, never inferred (§8.3)."""

    model_config = ConfigDict(frozen=True)

    entity: str
    left_key: str
    right_key: str
    type: str = "left"

    @model_validator(mode="after")
    def _known_join_type(self) -> JoinSpec:
        if self.type not in {"left", "inner"}:
            raise ValueError(f"unsupported join type: {self.type}")
        return self


class Entity(BaseModel):
    """A fact or dimension in the curated model."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    kind: EntityKind
    description: str = ""
    #: Registered source ids this entity reads.
    sources: list[str]
    #: The SQL that materialises the entity from its sources, as a SELECT body
    #: written in portable ANSI plus the shims in ``dialect_shims``.
    sql: str
    grain: list[str]
    time_column: str | None = None
    dimensions: list[DimensionRef] = Field(default_factory=list)
    joins: list[JoinSpec] = Field(default_factory=list)
    scd: str | None = None

    def dimension(self, name: str) -> DimensionRef | None:
        return next((d for d in self.dimensions if d.name == name), None)

    @property
    def dimension_names(self) -> set[str]:
        return {d.name for d in self.dimensions}


class Metric(BaseModel):
    """One governed KPI (§9.1)."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    domain: str
    entity: str
    expression: str
    format: MetricFormat = MetricFormat()
    grain: TimeGrain = TimeGrain.DAY
    dimensions: list[str] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list)
    description: str = ""
    requires_sources: list[str]
    optional_sources: list[str] = Field(default_factory=list)
    latency_floor_minutes: int
    direction: Direction = Direction.NEUTRAL
    owner: str = "platform"
    verified_queries: list[str] = Field(default_factory=list)
    #: Chargeback metrics declare how the figure was allocated (§9.3).
    allocation_method: str | None = None
    #: True while inside the slowest source's restatement window (§9.3).
    provisional_window_days: int = 0
    thresholds: dict[str, float | None] = Field(default_factory=dict)
    notes: str | None = None

    @property
    def is_ratio(self) -> bool:
        return self.format.type is FormatType.PERCENT


class SemanticModel(BaseModel):
    """The loaded, cross-validated semantic layer."""

    entities: dict[str, Entity]
    metrics: dict[str, Metric]

    def entity(self, entity_id: str) -> Entity:
        try:
            return self.entities[entity_id]
        except KeyError:
            raise ConfigurationError(f"Unknown entity: {entity_id}") from None

    def metric(self, metric_id: str) -> Metric:
        try:
            return self.metrics[metric_id]
        except KeyError:
            raise ConfigurationError(f"Unknown metric: {metric_id}") from None

    def metrics_for_domain(self, domain: str) -> list[Metric]:
        return sorted((m for m in self.metrics.values() if m.domain == domain), key=lambda m: m.id)

    def metric_ids(self) -> list[str]:
        return sorted(self.metrics)

    def requirements(self) -> dict[str, list[str]]:
        """metric id → required source ids, for the coverage matrix (R3)."""
        return {m.id: list(m.requires_sources) for m in self.metrics.values()}

    def optional_requirements(self) -> dict[str, list[str]]:
        return {m.id: list(m.optional_sources) for m in self.metrics.values() if m.optional_sources}

    def sources_used(self, metric_ids: list[str]) -> list[str]:
        used: set[str] = set()
        for metric_id in metric_ids:
            metric = self.metric(metric_id)
            used.update(metric.requires_sources)
            used.update(self.entity(metric.entity).sources)
        return sorted(used)

    def __iter__(self) -> Iterator[Metric]:  # type: ignore[override]
        return iter(self.metrics.values())


def _load_yaml_dir(directory: Path, pattern: str = "*.yaml") -> list[dict[str, object]]:
    if not directory.is_dir():
        raise ConfigurationError(f"Semantic directory not found: {directory}")
    documents: list[dict[str, object]] = []
    for path in sorted(directory.glob(pattern)):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if raw is None:
            continue
        items = raw if isinstance(raw, list) else [raw]
        for item in items:
            if not isinstance(item, dict):
                raise ConfigurationError(f"{path.name}: expected mappings, got {type(item)}")
            item["_file"] = path.name
            documents.append(item)
    return documents


def load_semantic_model(
    entities_dir: Path | None = None, metrics_dir: Path | None = None
) -> SemanticModel:
    """Load entities and metrics, then cross-validate the whole model."""
    entities: dict[str, Entity] = {}
    for document in _load_yaml_dir(entities_dir or ENTITIES_DIR):
        file_name = document.pop("_file", "?")
        try:
            entity = Entity.model_validate(document)
        except Exception as exc:
            raise ConfigurationError(f"Invalid entity in {file_name}: {exc}") from exc
        if entity.id in entities:
            raise ConfigurationError(f"Duplicate entity id: {entity.id}")
        entities[entity.id] = entity

    metrics: dict[str, Metric] = {}
    for document in _load_yaml_dir(metrics_dir or METRICS_DIR):
        file_name = document.pop("_file", "?")
        try:
            metric = Metric.model_validate(document)
        except Exception as exc:
            raise ConfigurationError(f"Invalid metric in {file_name}: {exc}") from exc
        if metric.id in metrics:
            raise ConfigurationError(f"Duplicate metric id: {metric.id}")
        metrics[metric.id] = metric

    model = SemanticModel(entities=entities, metrics=metrics)
    _validate(model)
    return model


def _validate(model: SemanticModel) -> None:
    """Cross-checks that catch the mistakes YAML alone cannot."""
    from snowobs_semantics.registry import default_registry

    registry = default_registry()
    known_sources = set(registry.ids())

    for entity in model.entities.values():
        missing = [s for s in entity.sources if s not in known_sources]
        if missing:
            raise ConfigurationError(
                f"Entity {entity.id} references unregistered sources: {missing}"
            )
        for join in entity.joins:
            if join.entity not in model.entities:
                raise ConfigurationError(f"Entity {entity.id} joins unknown entity '{join.entity}'")

    for metric in model.metrics.values():
        entity = model.entity(metric.entity)  # raises if unknown
        unknown_dimensions = [d for d in metric.dimensions if not _resolvable(model, entity, d)]
        if unknown_dimensions:
            raise ConfigurationError(
                f"Metric {metric.id} declares dimensions not reachable from "
                f"{entity.id}: {unknown_dimensions}"
            )
        missing = [s for s in metric.requires_sources if s not in known_sources]
        if missing:
            raise ConfigurationError(f"Metric {metric.id} requires unregistered sources: {missing}")
        # R7: a metric can never claim to be fresher than its slowest source.
        floor = max(
            (registry.get(s).documented_latency_minutes for s in metric.requires_sources),
            default=0,
        )
        if metric.latency_floor_minutes < floor:
            raise ConfigurationError(
                f"Metric {metric.id} claims a {metric.latency_floor_minutes}-minute latency "
                f"floor but its sources document {floor} minutes"
            )


def _resolvable(model: SemanticModel, entity: Entity, dimension: str) -> bool:
    """A dimension is reachable on the entity itself or across a declared join."""
    if dimension in entity.dimension_names:
        return True
    for join in entity.joins:
        joined = model.entities.get(join.entity)
        if joined and dimension in joined.dimension_names:
            return True
    return False


@lru_cache(maxsize=1)
def default_model() -> SemanticModel:
    return load_semantic_model()
