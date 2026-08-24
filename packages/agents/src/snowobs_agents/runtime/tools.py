"""The shared tool registry (BUILD_PROMPT §12.3).

``query_metric`` is the primary tool, and it is **text-to-metric, not
text-to-SQL**: the agent selects a governed metric from the catalogue rather
than authoring SQL. That single design choice is what makes agent answers
reproducible, auditable, and identical to the dashboard's — and it is why an
agent cannot invent a metric definition even if it wants to.

``run_sql_guarded`` exists for genuine ad-hoc needs, passes through the SQL
guard, and is **disabled by default** for non-admin roles (§12.3).

Every tool result carries its provenance so the agent can quote freshness and
sources alongside a figure (R5, R7).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from snowobs_common.errors import AppError
from snowobs_common.logging import get_logger
from snowobs_llm.base import ToolSpec
from snowobs_semantics.compiler import (
    Filter,
    FilterOperator,
    MetricRequest,
    SemanticCompiler,
    TimeRange,
)
from snowobs_semantics.model import SemanticModel, TimeGrain
from snowobs_semantics.registry import SourceRegistry, default_registry
from snowobs_semantics.scope import Scope, ScopeRequest, assess

logger = get_logger(__name__)

DEFAULT_LOOKBACK_DAYS = 30
MAX_ROWS_TO_AGENT = 200


class ToolError(AppError):
    """A tool failed in a way the agent should see and can act on."""

    status_code = 400
    title = "Tool error"
    problem_type = "https://snowobs.dev/problems/tool"


@dataclass
class ToolContext:
    """Everything a tool needs, injected rather than reached for."""

    engine: Any
    compiler: SemanticCompiler
    model: SemanticModel
    tenant: str = "default"
    actor: str = "anonymous"
    roles: frozenset[str] = frozenset()
    #: RLS predicates applied to every metric query, server-side (§17).
    rls_filters: list[Filter] = field(default_factory=list)
    allow_adhoc_sql: bool = False
    #: Pinned context the user set in the UI (time range, team) — §12.1.
    default_time_range: TimeRange | None = None
    coverage: Any = None
    #: Accounts this deployment can answer for, so `query_metric` can scope a
    #: question to one of them — and refuse an account that does not exist
    #: rather than returning the organization's figure under its name.
    accounts: tuple[str, ...] = ()
    #: The organization these accounts belong to, for labelling.
    organization: str | None = None
    #: Whether an organization-wide roll-up here spans every account. False
    #: when billing names an account whose own detail never landed, which makes
    #: every organization figure an under-count the agent must qualify.
    missing_accounts: tuple[str, ...] = ()
    #: Which engine is answering; the scope rules differ because LIVE reads one
    #: account per connection and OFFLINE holds every account in one lake.
    mode: str = "offline"
    registry: SourceRegistry | None = None

    def source_registry(self) -> SourceRegistry:
        return self.registry or default_registry()


@dataclass
class ToolOutcome:
    """A tool's answer plus the provenance the agent must quote."""

    content: str
    metrics: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    sql: str = ""
    latency_floor_minutes: int = 0
    provisional: bool = False
    row_count: int = 0
    is_error: bool = False

    def to_json(self) -> str:
        return self.content


@dataclass
class Tool:
    """One registered tool: its schema and its implementation."""

    spec: ToolSpec
    run: Callable[[ToolContext, dict[str, Any]], ToolOutcome]
    #: Roles permitted to call it. Empty means everyone.
    required_roles: frozenset[str] = frozenset()
    #: Write proposals go to the review queue, never to production (R8).
    is_proposal: bool = False


# ═══════════════════════════════════════════════════════ query_metric ════════
def _resolve_time_range(context: ToolContext, arguments: dict[str, Any]) -> TimeRange | None:
    start, end = arguments.get("start"), arguments.get("end")
    if start and end:
        return TimeRange(start=date.fromisoformat(start), end=date.fromisoformat(end))
    if context.default_time_range:
        return context.default_time_range
    days = max(int(arguments.get("last_days") or DEFAULT_LOOKBACK_DAYS), 1)
    end_date = date.today()  # noqa: DTZ011 — account-date granularity
    # Both ends are inclusive, so "the last 30 days" spans 30 dates ending
    # today — subtracting a full 30 would quietly return 31 days of data under
    # a label that says 30.
    return TimeRange(start=end_date - timedelta(days=days - 1), end=end_date)


def _to_filters(raw: Any) -> list[Filter]:
    if not raw:
        return []
    filters: list[Filter] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        filters.append(
            Filter(
                dimension=str(item.get("dimension", "")),
                operator=FilterOperator(str(item.get("operator", "eq"))),
                value=item.get("value"),
            )
        )
    return filters


def _org_label(context: ToolContext) -> str:
    return context.organization or "Organization"


def _resolve_scope(
    context: ToolContext, arguments: dict[str, Any], metric_ids: list[str]
) -> tuple[ScopeRequest, ToolOutcome | None]:
    """Which account this question is about, and whether it can be answered there.

    Naming no account means the organization, which for a single-account
    deployment is the same thing. Naming one runs the same verdict the
    dashboards run, so a metric the UI refuses to scope to an account cannot be
    scoped to one by asking an agent instead — the refusal carries the reason,
    which is what the agent tells the user rather than answering at a scope it
    was not asked for.
    """
    raw = arguments.get("account")
    if not raw:
        return ScopeRequest(), None

    account = str(raw).strip()
    if context.accounts and account not in context.accounts:
        return ScopeRequest(), ToolOutcome(
            content=(
                f"'{account}' is not an account this deployment has data for. "
                f"Accounts available: {', '.join(context.accounts)}. "
                "Omit `account` to answer for the whole organization."
            ),
            is_error=True,
        )

    scope = ScopeRequest(scope=Scope.ACCOUNT, account=account)
    registry = context.source_registry()
    for metric_id in metric_ids:
        verdict = assess(
            context.model.metric(metric_id),
            scope,
            model=context.model,
            registry=registry,
            mode=context.mode,
        )
        if not verdict.available:
            return scope, ToolOutcome(content=str(verdict.reason), is_error=True)
    return scope, None


def _query_metric(context: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:
    """Run a governed metric query. The agent picks metrics, never SQL."""
    metric_ids = arguments.get("metrics") or []
    if isinstance(metric_ids, str):
        metric_ids = [metric_ids]
    if not metric_ids:
        return ToolOutcome(
            content="No metric was requested. Call describe_metric or list_metrics first.",
            is_error=True,
        )

    unknown = [m for m in metric_ids if m not in context.model.metrics]
    if unknown:
        suggestions = _suggest(context.model, unknown[0])
        return ToolOutcome(
            content=(
                f"Unknown metric(s): {', '.join(unknown)}. "
                f"Closest matches: {', '.join(suggestions) or 'none'}. "
                "Use list_metrics to see the catalogue."
            ),
            is_error=True,
        )

    scope, scope_error = _resolve_scope(context, arguments, metric_ids)
    if scope_error is not None:
        return scope_error

    request = MetricRequest(
        metrics=list(metric_ids),
        dimensions=list(arguments.get("dimensions") or []),
        filters=_to_filters(arguments.get("filters")),
        time_range=_resolve_time_range(context, arguments),
        grain=TimeGrain(arguments["grain"]) if arguments.get("grain") else None,
        limit=min(int(arguments.get("limit") or 100), MAX_ROWS_TO_AGENT),
        bucket_time=bool(arguments.get("by_time", True)),
        rls_filters=context.rls_filters,
        account=scope.account_filter,
    )

    from snowobs_semantics.dialect_shims import Dialect

    dialect = getattr(context.engine, "dialect", Dialect.DUCKDB)
    try:
        compiled = context.compiler.compile(request, dialect)
        result = context.engine.execute(compiled)
    except AppError as exc:
        return ToolOutcome(content=f"The query could not run: {exc.detail or exc}", is_error=True)

    rows = [
        {column: _plain(value) for column, value in zip(result.columns, row, strict=True)}
        for row in result.rows
    ]
    payload = {
        "metrics": list(metric_ids),
        # The window that was actually applied. An answer opening "over the last
        # 30 days" is quoting a figure like any other, and without this in the
        # result there is nothing for it to quote: the grounding check would
        # read that 30 as a number the agent made up.
        "window": {
            "start": request.time_range.start.isoformat() if request.time_range else None,
            "end": request.time_range.end.isoformat() if request.time_range else None,
            "days": (
                (request.time_range.end - request.time_range.start).days + 1
                if request.time_range
                else None
            ),
        },
        # The scope travels with the figure. An agent that says "spend is
        # $40k" without saying whether that is one account or twelve has
        # mis-quoted the tool even though the number is exact — and a
        # roll-up that is missing an account says so here rather than
        # presenting an under-count as the total (R3).
        "scope": scope.scope.value,
        "scope_account": scope.account_filter,
        "scope_label": scope.label() if scope.account_filter else _org_label(context),
        "contributing_accounts": (
            [scope.account] if scope.account_filter else list(context.accounts)
        ),
        "missing_accounts": ([] if scope.account_filter else list(context.missing_accounts)),
        "rows": rows,
        "row_count": result.row_count,
        "truncated": result.truncated,
        "as_of": result.as_of.isoformat(),
        "latency_floor_minutes": result.latency_floor_minutes,
        "provisional": result.provisional,
        "sources": result.sources,
    }
    return ToolOutcome(
        content=json.dumps(payload, indent=2),
        metrics=list(metric_ids),
        sources=result.sources,
        sql=result.executed_sql,
        latency_floor_minutes=result.latency_floor_minutes,
        provisional=result.provisional,
        row_count=result.row_count,
    )


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _suggest(model: SemanticModel, wanted: str) -> list[str]:
    """Nearest metric ids and synonyms, so a near-miss is recoverable."""
    wanted_lower = wanted.lower()
    scored: list[tuple[int, str]] = []
    for metric in model.metrics.values():
        haystack = " ".join([metric.id, metric.name, *metric.synonyms]).lower()
        overlap = sum(1 for token in wanted_lower.replace(".", " ").split() if token in haystack)
        if overlap:
            scored.append((overlap, metric.id))
    scored.sort(reverse=True)
    return [metric_id for _, metric_id in scored[:5]]


# ══════════════════════════════════════════════════════ catalogue tools ══════
def _list_metrics(context: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:
    domain = arguments.get("domain")
    search = str(arguments.get("search") or "").lower()
    metrics = context.model.metrics.values()
    if domain:
        metrics = [m for m in metrics if m.domain == domain]  # type: ignore[assignment]
    if search:
        metrics = [  # type: ignore[assignment]
            m for m in metrics if search in " ".join([m.id, m.name, *m.synonyms]).lower()
        ]
    listing = [
        {
            "id": metric.id,
            "name": metric.name,
            "domain": metric.domain,
            "dimensions": metric.dimensions,
            "freshness_floor_minutes": metric.latency_floor_minutes,
        }
        for metric in sorted(metrics, key=lambda m: m.id)[:60]
    ]
    return ToolOutcome(content=json.dumps({"metrics": listing}, indent=2))


def _describe_metric(context: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:
    metric_id = str(arguments.get("metric_id", ""))
    if metric_id not in context.model.metrics:
        return ToolOutcome(
            content=(
                f"Unknown metric '{metric_id}'. Closest: "
                f"{', '.join(_suggest(context.model, metric_id)) or 'none'}."
            ),
            is_error=True,
        )
    metric = context.model.metric(metric_id)
    return ToolOutcome(
        content=json.dumps(
            {
                "id": metric.id,
                "name": metric.name,
                "domain": metric.domain,
                "description": metric.description.strip(),
                "dimensions": metric.dimensions,
                "synonyms": metric.synonyms,
                "requires_sources": metric.requires_sources,
                "freshness_floor_minutes": metric.latency_floor_minutes,
                "direction": metric.direction.value,
                "allocation_method": metric.allocation_method,
                "verified_queries": metric.verified_queries,
            },
            indent=2,
        ),
        metrics=[metric.id],
    )


def _get_coverage(context: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:
    """What data is present — so the agent can say what it cannot answer (R3)."""
    del arguments
    if context.coverage is None:
        return ToolOutcome(
            content="Coverage information is not available in this context.", is_error=True
        )
    sources = [
        {
            "source": source.source_id,
            "status": source.status.value
            if hasattr(source.status, "value")
            else str(source.status),
            "rows": source.rows,
            "window": [
                source.window_start.isoformat() if source.window_start else None,
                source.window_end.isoformat() if source.window_end else None,
            ],
            "remediation": source.remediation,
        }
        for source in context.coverage.sources
        if source.rows > 0 or source.criticality == "core"
    ]
    return ToolOutcome(content=json.dumps({"sources": sources}, indent=2))


# ══════════════════════════════════════════════════════ list_accounts ════════
def _list_accounts(context: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:
    """The accounts in this organization, and which of them have data.

    An agent asked "which account is driving the increase?" needs to know the
    fleet before it can slice by it. It also needs to know which accounts are
    *absent*: an organization roll-up that silently omits an account would let
    the agent report a total that is short by an account's worth of spend.
    """
    del arguments
    if not context.accounts and not context.missing_accounts:
        return ToolOutcome(
            content=(
                "This deployment has no per-account breakdown: no extract has been "
                "stamped with an account. Every figure is for the single account "
                "the platform is reading."
            )
        )
    return ToolOutcome(
        content=json.dumps(
            {
                "organization": _org_label(context),
                "mode": context.mode,
                "accounts_with_data": list(context.accounts),
                "accounts_missing_data": list(context.missing_accounts),
                "note": (
                    "Pass one of accounts_with_data as `account` to query_metric to "
                    "scope a figure to it. Organization-wide figures cover "
                    "accounts_with_data only; if accounts_missing_data is non-empty, "
                    "say so when quoting an organization total."
                ),
            },
            indent=2,
        )
    )


# ═══════════════════════════════════════════════════════ explain_delta ═══════
def _explain_delta(context: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:
    """Deterministic contribution analysis. The tool computes; the agent narrates."""
    from snowobs_analytics.anomaly import explain_delta

    metric_id = str(arguments.get("metric", ""))
    dimension = str(arguments.get("dimension", "team"))
    if metric_id not in context.model.metrics:
        return ToolOutcome(content=f"Unknown metric '{metric_id}'.", is_error=True)

    def _window(prefix: str) -> TimeRange | None:
        start, end = arguments.get(f"{prefix}_start"), arguments.get(f"{prefix}_end")
        if start and end:
            return TimeRange(start=date.fromisoformat(start), end=date.fromisoformat(end))
        return None

    period_a, period_b = _window("period_a"), _window("period_b")
    if period_a is None or period_b is None:
        return ToolOutcome(
            content=(
                "Both periods are required: period_a_start, period_a_end, "
                "period_b_start, period_b_end (YYYY-MM-DD)."
            ),
            is_error=True,
        )

    from snowobs_semantics.dialect_shims import Dialect

    dialect = getattr(context.engine, "dialect", Dialect.DUCKDB)
    totals: list[dict[str, Decimal]] = []
    sources: list[str] = []
    for window in (period_a, period_b):
        request = MetricRequest(
            metrics=[metric_id],
            dimensions=[dimension],
            time_range=window,
            bucket_time=False,
            limit=MAX_ROWS_TO_AGENT,
            rls_filters=context.rls_filters,
        )
        try:
            result = context.engine.execute(context.compiler.compile(request, dialect))
        except AppError as exc:
            return ToolOutcome(content=f"Could not compute the delta: {exc}", is_error=True)
        sources = result.sources
        column = dimension.upper()
        index = result.columns.index(column) if column in result.columns else 0
        totals.append(
            {
                str(row[index]): Decimal(str(row[-1] or 0))
                for row in result.rows
                if row[index] is not None
            }
        )

    contributions = explain_delta(totals[0], totals[1], dimension=dimension)
    payload = {
        "metric": metric_id,
        "dimension": dimension,
        "period_a": [period_a.start.isoformat(), period_a.end.isoformat()],
        "period_b": [period_b.start.isoformat(), period_b.end.isoformat()],
        # Span in days, reported for two reasons: a narrator saying "the last
        # 30 days against the 30 before" is quoting a figure like any other and
        # needs it grounded here, and comparing windows of unequal length is a
        # mistake a reader can only catch if the lengths are on the result.
        "period_a_days": (period_a.end - period_a.start).days + 1,
        "period_b_days": (period_b.end - period_b.start).days + 1,
        "period_a_total": str(sum(totals[0].values(), Decimal(0))),
        "period_b_total": str(sum(totals[1].values(), Decimal(0))),
        "contributions": [
            {
                "member": contribution.member,
                "delta": str(contribution.delta),
                "share_of_delta": str(contribution.share_of_delta),
            }
            for contribution in contributions
        ],
    }
    return ToolOutcome(content=json.dumps(payload, indent=2), metrics=[metric_id], sources=sources)


# ═══════════════════════════════════════════════════ run_sql_guarded ═════════
def _run_sql_guarded(context: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:
    """The escape hatch. Guarded, role-gated, and off by default (§12.3, R9)."""
    if not context.allow_adhoc_sql:
        return ToolOutcome(
            content=(
                "Ad-hoc SQL is disabled for this deployment. Use query_metric with a "
                "governed metric instead — every dashboard figure is available that way."
            ),
            is_error=True,
        )

    sql = str(arguments.get("sql", ""))
    from snowobs_sqlguard.guard import SqlGuardError, check

    policy = context.engine.policy()
    try:
        guarded = check(sql, policy, dialect=str(getattr(context.engine, "dialect", "duckdb")))
    except SqlGuardError as exc:
        return ToolOutcome(content=f"The SQL guard rejected this statement: {exc}", is_error=True)

    try:
        rows = context.engine.catalog.query(guarded.sql)
    except Exception as exc:
        return ToolOutcome(content=f"The statement failed: {type(exc).__name__}", is_error=True)

    return ToolOutcome(
        content=json.dumps(
            {"rows": [[_plain(cell) for cell in row] for row in rows[:MAX_ROWS_TO_AGENT]]},
            indent=2,
        ),
        sql=guarded.sql,
        row_count=len(rows),
    )


# ═══════════════════════════════════════════════════════════ registry ════════
def build_registry() -> dict[str, Tool]:
    """The tool registry every agent shares (§12.3)."""
    return {
        "query_metric": Tool(
            spec=ToolSpec(
                name="query_metric",
                description=(
                    "Run a governed metric query. This is the primary tool: select "
                    "metric ids from the catalogue rather than writing SQL. Returns "
                    "rows plus the compiled SQL, the source views, the as-of "
                    "timestamp, the freshness floor, and whether figures are "
                    "provisional. Always state the time range and freshness when "
                    "quoting a result."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "metrics": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Metric ids, e.g. ['cost.billed_credits'].",
                        },
                        "dimensions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Dimensions to slice by, e.g. ['team'].",
                        },
                        "filters": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "dimension": {"type": "string"},
                                    "operator": {"type": "string"},
                                    "value": {},
                                },
                                "required": ["dimension", "value"],
                            },
                        },
                        "start": {"type": "string", "description": "YYYY-MM-DD"},
                        "end": {"type": "string", "description": "YYYY-MM-DD"},
                        "last_days": {"type": "integer"},
                        "grain": {"type": "string", "enum": ["hour", "day", "week", "month"]},
                        "by_time": {
                            "type": "boolean",
                            "description": "False for a single total over the period.",
                        },
                        "limit": {"type": "integer"},
                        "account": {
                            "type": "string",
                            "description": (
                                "Answer for one Snowflake account instead of the whole "
                                "organization. Omit for an organization-wide figure. "
                                "Use list_accounts to see which accounts exist; a "
                                "metric that has no per-account meaning will say so "
                                "rather than answer."
                            ),
                        },
                    },
                    "required": ["metrics"],
                },
            ),
            run=_query_metric,
        ),
        "list_metrics": Tool(
            spec=ToolSpec(
                name="list_metrics",
                description=(
                    "Browse the governed metric catalogue, optionally filtered by "
                    "domain or a search term. Use this when unsure which metric "
                    "answers a question."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string"},
                        "search": {"type": "string"},
                    },
                },
            ),
            run=_list_metrics,
        ),
        "describe_metric": Tool(
            spec=ToolSpec(
                name="describe_metric",
                description=(
                    "Full definition of one metric: what it means, which sources it "
                    "needs, its freshness floor, and how it is allocated."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"metric_id": {"type": "string"}},
                    "required": ["metric_id"],
                },
            ),
            run=_describe_metric,
        ),
        "get_coverage": Tool(
            spec=ToolSpec(
                name="get_coverage",
                description=(
                    "Which source views are loaded, their row counts and windows, and "
                    "the remediation for anything missing. Call this before saying a "
                    "question cannot be answered."
                ),
                input_schema={"type": "object", "properties": {}},
            ),
            run=_get_coverage,
        ),
        "list_accounts": Tool(
            spec=ToolSpec(
                name="list_accounts",
                description=(
                    "List the Snowflake accounts in this organization, which of them "
                    "have landed data, and which have not. Call this before scoping a "
                    "question to an account, and before quoting an organization-wide "
                    "total you intend to describe as complete."
                ),
                input_schema={"type": "object", "properties": {}},
            ),
            run=_list_accounts,
        ),
        "explain_delta": Tool(
            spec=ToolSpec(
                name="explain_delta",
                description=(
                    "Deterministic contribution analysis between two periods: which "
                    "members of a dimension account for the change. Use this for any "
                    "'why did X change' question — never estimate contributions yourself."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "metric": {"type": "string"},
                        "dimension": {"type": "string"},
                        "period_a_start": {"type": "string"},
                        "period_a_end": {"type": "string"},
                        "period_b_start": {"type": "string"},
                        "period_b_end": {"type": "string"},
                    },
                    "required": [
                        "metric",
                        "period_a_start",
                        "period_a_end",
                        "period_b_start",
                        "period_b_end",
                    ],
                },
            ),
            run=_explain_delta,
        ),
        "run_sql_guarded": Tool(
            spec=ToolSpec(
                name="run_sql_guarded",
                description=(
                    "Execute a read-only SELECT through the SQL guard. Only for "
                    "genuine ad-hoc needs no governed metric covers; disabled in most "
                    "deployments. Prefer query_metric."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"sql": {"type": "string"}},
                    "required": ["sql"],
                },
            ),
            run=_run_sql_guarded,
            required_roles=frozenset({"platform_admin"}),
        ),
    }


def specs_for(registry: dict[str, Tool], roles: frozenset[str]) -> Sequence[ToolSpec]:
    """The tool specs this caller may use — role-gated at the schema level."""
    return [
        tool.spec
        for tool in registry.values()
        if not tool.required_roles or (roles & tool.required_roles)
    ]
