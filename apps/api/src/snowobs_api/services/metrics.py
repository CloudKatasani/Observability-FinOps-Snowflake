"""Metric query orchestration.

Compiles a request through the semantic layer, executes it on the configured
engine, and returns the figure with everything R5/R7 require alongside it: the
compiled SQL, the sources, the as-of timestamp, the freshness floor, and whether
the figure is provisional.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from snowobs_api.services.engines import EngineChoice, open_engine
from snowobs_common.config import Settings
from snowobs_engines.cache import ResultCache
from snowobs_semantics.compiler import MetricRequest, SemanticCompiler
from snowobs_semantics.model import Metric, default_model


@dataclass
class MetricValue:
    """One tile's worth of answer, with its provenance."""

    metric_id: str
    name: str
    value: Any
    format_type: str
    format_decimals: int
    unit: str | None
    direction: str
    as_of: datetime
    latency_floor_minutes: int
    provisional: bool
    sources: list[str]
    #: The subset of `sources` whose latency actually gates this figure (R7).
    gating_sources: list[str]
    sql: str
    allocation_method: str | None = None
    #: Set when the metric cannot be computed — R3: say why, never show zero.
    unavailable_reason: str | None = None


@dataclass
class MetricSeries:
    """A chart's worth of answer, with the same provenance."""

    metrics: list[str]
    columns: list[str]
    rows: list[list[Any]]
    as_of: datetime
    latency_floor_minutes: int
    provisional: bool
    sources: list[str]
    gating_sources: list[str]
    sql: str
    truncated: bool
    row_count: int


def _json_safe(value: Any) -> Any:
    """Decimals cross the API boundary as strings — never as floats (§27.7)."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


class MetricService:
    """Compiles and executes metric requests for the API."""

    def __init__(self, settings: Settings, tenant: str = "default") -> None:
        self.settings = settings
        self.tenant = tenant
        self.compiler = SemanticCompiler()
        self.model = default_model()
        self._cache = ResultCache()

    def _storage_root(self) -> Path:
        from snowobs_api.services.datasets import storage_root

        return storage_root(self.settings)

    def _engine(self) -> AbstractContextManager[EngineChoice]:
        """The engine this deployment answers from — LIVE or OFFLINE (R10).

        Which one is decided in `services/engines.py` and nowhere else, so a
        dashboard cannot end up serving landed extracts while the deployment
        believes it is reading the account.
        """
        return open_engine(
            self.settings,
            tenant=self.tenant,
            cache=self._cache,
            storage_root=self._storage_root(),
        )

    def catalog_entries(self) -> list[Metric]:
        return sorted(self.model.metrics.values(), key=lambda m: (m.domain, m.id))

    def query(self, request: MetricRequest) -> MetricSeries:
        with self._engine() as chosen:
            compiled = self.compiler.compile(request, chosen.dialect)
            result = chosen.engine.execute(compiled)
            return MetricSeries(
                metrics=list(request.metrics),
                columns=result.columns,
                rows=[[_json_safe(cell) for cell in row] for row in result.rows],
                as_of=result.as_of,
                latency_floor_minutes=result.latency_floor_minutes,
                provisional=result.provisional,
                sources=result.sources,
                gating_sources=result.gating_sources,
                sql=result.executed_sql,
                truncated=result.truncated,
                row_count=result.row_count,
            )

    def tile(self, metric_id: str, request: MetricRequest) -> MetricValue:
        """A single-figure tile. Unavailability is explained, never zeroed (R3)."""
        metric = self.model.metric(metric_id)
        with self._engine() as chosen:
            available = chosen.engine.available_relations()
            missing = [
                source for source in metric.requires_sources if source.upper() not in available
            ]
            if missing:
                return MetricValue(
                    metric_id=metric.id,
                    name=metric.name,
                    value=None,
                    format_type=metric.format.type.value,
                    format_decimals=metric.format.decimals,
                    unit=metric.format.unit,
                    direction=metric.direction.value,
                    as_of=datetime.now(tz=UTC),
                    latency_floor_minutes=metric.latency_floor_minutes,
                    provisional=False,
                    sources=list(metric.requires_sources),
                    gating_sources=list(metric.requires_sources),
                    sql="",
                    allocation_method=metric.allocation_method,
                    unavailable_reason=("Unavailable — requires " + ", ".join(sorted(missing))),
                )

            compiled = self.compiler.compile(request, chosen.dialect)
            result = chosen.engine.execute(compiled)
            return MetricValue(
                metric_id=metric.id,
                name=metric.name,
                value=_json_safe(result.scalar()),
                format_type=metric.format.type.value,
                format_decimals=metric.format.decimals,
                unit=metric.format.unit,
                direction=metric.direction.value,
                as_of=result.as_of,
                latency_floor_minutes=result.latency_floor_minutes,
                provisional=result.provisional,
                sources=result.sources,
                gating_sources=result.gating_sources,
                sql=result.executed_sql,
                allocation_method=metric.allocation_method,
            )
