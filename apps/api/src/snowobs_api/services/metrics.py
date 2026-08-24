"""Metric query orchestration.

Compiles a request through the semantic layer, executes it on the configured
engine, and returns the figure with everything R5/R7 require alongside it: the
compiled SQL, the sources, the as-of timestamp, the freshness floor, and whether
the figure is provisional.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from snowobs_api.services.engines import EngineChoice, open_engine, resolve_mode
from snowobs_api.services.scope import Scope, ScopeRequest, ScopeVerdict, assess
from snowobs_common.config import Settings
from snowobs_common.errors import ScopeUnavailableError
from snowobs_engines.cache import ResultCache
from snowobs_semantics.compiler import MetricRequest, SemanticCompiler
from snowobs_semantics.model import Metric, default_model
from snowobs_semantics.registry import default_registry


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
    #: Where this figure was computed: "organization" or one account's name.
    scope: str = "organization"
    scope_account: str | None = None
    #: True when an organization figure covers only the accounts landed so far.
    scope_partial: bool = False
    contributing_accounts: list[str] = field(default_factory=list)


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
    scope: str = "organization"
    scope_account: str | None = None
    scope_partial: bool = False
    contributing_accounts: list[str] = field(default_factory=list)


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

    # ----------------------------------------------------------------- scope
    def landed_accounts(self) -> list[str]:
        """Accounts present in this tenant's data, for the scope selector.

        Only names that have landed *account-scoped* data count. The
        organization's own extracts are stamped too — `ORGANIZATION_USAGE` is
        exported once, from whichever account holds the grant — and that name
        is the organization, not an account you can select. Offering
        "ACME_GROUP" alongside its four accounts would invite a per-account
        view of something that has no per-account meaning.
        """
        registry = default_registry()
        account_scoped = {source.id for source in registry if not source.is_organization_scoped}
        with self._engine() as chosen:
            catalog = getattr(chosen.engine, "catalog", None)
            if catalog is None or not hasattr(catalog, "accounts"):
                # LIVE reads one account per connection, so the accounts on
                # offer are the ones configured rather than the ones discovered.
                return [a.name for a in self.settings.snowflake.configured_accounts()]

            found: set[str] = set()
            for source_id in catalog.landed_sources():
                if source_id in account_scoped:
                    found.update(catalog.accounts_for(source_id))
            return sorted(found)

    def scope_verdict(self, metric_id: str, scope: ScopeRequest) -> ScopeVerdict:
        """Can this metric answer at this scope? The reason travels with the no."""
        return assess(
            self.model.metric(metric_id),
            scope,
            model=self.model,
            registry=default_registry(),
            mode=resolve_mode(self.settings),
            landed_accounts=self.landed_accounts(),
        )

    def _scope_fields(
        self, scope: ScopeRequest, verdict: ScopeVerdict, accounts: list[str]
    ) -> dict[str, Any]:
        contributing = (
            [scope.account] if scope.scope is Scope.ACCOUNT and scope.account else accounts
        )
        return {
            "scope": scope.scope.value,
            "scope_account": scope.account_filter,
            "scope_partial": verdict.partial and len(contributing) > 1,
            "contributing_accounts": contributing,
        }

    def query(self, request: MetricRequest, scope: ScopeRequest | None = None) -> MetricSeries:
        scope = scope or ScopeRequest()
        accounts = self.landed_accounts()
        verdicts = {
            metric_id: self.scope_verdict(metric_id, scope) for metric_id in request.metrics
        }
        refused = [(m, v) for m, v in verdicts.items() if not v.available]
        if refused:
            # R3: a scope a metric cannot answer is explained, not silently
            # widened to one it can. Widening would return the organization's
            # figure under an account's label, which nothing downstream could
            # detect.
            raise ScopeUnavailableError(
                "; ".join(reason for _metric, verdict in refused if (reason := verdict.reason))
            )
        partial = any(v.partial for v in verdicts.values())

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
                **self._scope_fields(scope, ScopeVerdict.ok(partial=partial), accounts),
            )

    def tile(
        self, metric_id: str, request: MetricRequest, scope: ScopeRequest | None = None
    ) -> MetricValue:
        """A single-figure tile. Unavailability is explained, never zeroed (R3)."""
        metric = self.model.metric(metric_id)
        scope = scope or ScopeRequest()
        accounts = self.landed_accounts()
        verdict = self.scope_verdict(metric_id, scope)
        scope_fields = self._scope_fields(scope, verdict, accounts)

        if not verdict.available:
            # A tile renders the reason rather than raising: the dashboard shows
            # every KPI at the chosen scope, and the ones that cannot answer
            # there are part of the answer (R3).
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
                unavailable_reason=verdict.reason,
                **scope_fields,
            )

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
                    **scope_fields,
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
                **scope_fields,
            )
