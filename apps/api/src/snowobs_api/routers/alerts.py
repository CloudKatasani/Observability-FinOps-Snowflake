"""Alert rules, their statistics, backtests, and the OFFLINE DDL export (§15).

Read-only by construction. Rules are *declared* in `config/alert_rules.yaml`
and reviewed like any other configuration change; there is no endpoint that
writes one, because an alert rule is a change to what wakes somebody at 03:00
and that belongs in version control rather than in a form (R8).

Every response carries the same provenance the metric endpoints carry: the
as-of timestamp, the freshness floor of the rule's slowest source, and the
sources themselves, so a rule's statistics can never be read without also
seeing how fresh the data behind them is (R5, R7).
"""

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel

from snowobs_analytics.rules import window_name
from snowobs_api.deps import SettingsDep
from snowobs_api.services.alerts import AlertService

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])

RuleId = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")]


class RuleStatisticsResponse(BaseModel):
    """Per-rule fire/action counts — the anti-fatigue view (§14)."""

    fires: int
    acknowledged: int
    actioned: int
    suppressed_by_dedup: int
    action_rate: str | None
    first_fired: datetime | None
    last_fired: datetime | None
    prune_recommendation: str | None


class AlertRuleResponse(BaseModel):
    id: str
    name: str
    description: str
    domain: str
    metric_id: str
    metric_name: str
    condition: str
    #: A string, never a float: a threshold on credits is Decimal end to end.
    threshold: str
    unit: str | None
    scope: dict[str, str]
    window: str
    persistence: int
    tier: str
    ack_minutes: int | None
    routes: list[str]
    #: The channels this rule's tier actually reaches, after tier filtering.
    channels: list[str]
    runbook_url: str
    enabled: bool
    latency_floor_minutes: int
    requires_sources: list[str]
    statistics: RuleStatisticsResponse


class RuleListResponse(BaseModel):
    rules: list[AlertRuleResponse]
    rule_count: int
    #: Rule ids by the domain of the metric each one watches.
    domains: dict[str, list[str]]
    #: Where the declarations were read from, so an operator can find them.
    source: str
    #: False until ALERTING__ENABLED is set. Rules still evaluate; nothing is
    #: sent anywhere.
    dispatch_enabled: bool
    channels: list[str]


class PruneProposalsResponse(BaseModel):
    """Rules that have fired repeatedly with nobody acting (§14)."""

    proposals: list[str]
    evaluated_rules: int
    generated_at: datetime


class BacktestResponse(BaseModel):
    rule_id: str
    window_start: date | None
    window_end: date | None
    would_have_fired: int
    firing_days: list[date]
    suppressed_by_dedup: int
    summary: str
    #: R3: set when the rule could not be replayed at all, with the reason.
    unavailable_reason: str | None
    as_of: datetime | None
    latency_floor_minutes: int
    sources: list[str]
    #: R5: the statement the replayed series came from.
    sql: str


class AlertDdlResponse(BaseModel):
    """OFFLINE mode cannot notify; a validated rule leaves as deployable DDL."""

    rule_ids: list[str]
    warehouse: str
    ddl: str


def _statistics(
    service: AlertService, rule_id: str, *, now: datetime | None = None
) -> RuleStatisticsResponse:
    statistics = service.statistics(rule_id)
    rate = statistics.action_rate
    return RuleStatisticsResponse(
        fires=statistics.fires,
        acknowledged=statistics.acknowledged,
        actioned=statistics.actioned,
        suppressed_by_dedup=statistics.suppressed,
        action_rate=str(rate) if rate is not None else None,
        first_fired=statistics.first_fired,
        last_fired=statistics.last_fired,
        prune_recommendation=statistics.prune_recommendation(now=now),
    )


def _rule_response(service: AlertService, rule_id: str) -> AlertRuleResponse:
    rule = service.rule(rule_id)
    metric = service.metric_for(rule)
    return AlertRuleResponse(
        id=rule.id,
        name=rule.name,
        description=rule.description,
        domain=metric.domain,
        metric_id=metric.id,
        metric_name=metric.name,
        condition=rule.condition.value,
        threshold=str(rule.threshold),
        unit=metric.format.unit,
        scope=dict(rule.scope),
        window=window_name(rule.window_days),
        persistence=rule.persistence,
        tier=rule.tier.value,
        ack_minutes=rule.tier.ack_minutes,
        routes=list(rule.routes),
        channels=[channel.name for channel in service.rule_set.channels_for(rule)],
        runbook_url=rule.runbook_url,
        enabled=rule.enabled,
        latency_floor_minutes=metric.latency_floor_minutes,
        requires_sources=list(metric.requires_sources),
        statistics=_statistics(service, rule.id),
    )


@router.get("/rules", response_model=RuleListResponse)
async def list_rules(settings: SettingsDep) -> RuleListResponse:
    """Every declared rule, with the statistics that decide whether it earns its place."""
    service = AlertService(settings)
    return RuleListResponse(
        rules=[_rule_response(service, rule.id) for rule in service.rules()],
        rule_count=len(service.rules()),
        domains={
            domain: list(ids) for domain, ids in service.rule_set.domains(service.model).items()
        },
        source=str(service.rule_set.path),
        dispatch_enabled=settings.alerting.enabled,
        channels=[channel.name for channel in service.rule_set.channels],
    )


@router.get("/rules/{rule_id}", response_model=AlertRuleResponse)
async def get_rule(settings: SettingsDep, rule_id: RuleId) -> AlertRuleResponse:
    """One rule, its routing, and its firing history in this process."""
    return _rule_response(AlertService(settings), rule_id)


@router.get("/prune-proposals", response_model=PruneProposalsResponse)
async def prune_proposals(settings: SettingsDep) -> PruneProposalsResponse:
    """Rules that have fired repeatedly and changed nothing.

    A rule with five firings over sixty days and zero actions is proposed for
    disabling. This endpoint is the anti-fatigue loop's read side: the point of
    it is that the rule set shrinks over time rather than only growing.
    """
    service = AlertService(settings)
    return PruneProposalsResponse(
        proposals=service.prune_proposals(),
        evaluated_rules=len(service.rules()),
        generated_at=datetime.now(tz=UTC),
    )


@router.post("/rules/{rule_id}/backtest", response_model=BacktestResponse)
async def backtest_rule(
    settings: SettingsDep,
    rule_id: RuleId,
    start: date | None = Query(default=None, description="First window to replay."),
    end: date | None = Query(default=None, description="Last window to replay."),
) -> BacktestResponse:
    """Replay a rule over the landed window before anyone turns it on (§14).

    The replay is isolated: it never records a firing against the rule's
    statistics and never opens an alert in the dedup ledger.
    """
    service = AlertService(settings)
    result, observation = service.backtest(rule_id, start=start, end=end)
    return BacktestResponse(
        rule_id=result.rule_id,
        window_start=result.window_start if observation.evaluated else None,
        window_end=result.window_end if observation.evaluated else None,
        would_have_fired=result.would_have_fired,
        firing_days=list(result.firing_days),
        suppressed_by_dedup=result.suppressed_by_dedup,
        summary=(
            result.summary()
            if observation.evaluated
            else f"'{rule_id}' could not be replayed: {observation.skipped_because}"
        ),
        unavailable_reason=observation.skipped_because,
        as_of=observation.as_of,
        latency_floor_minutes=observation.latency_floor_minutes,
        sources=list(observation.sources),
        sql=observation.sql,
    )


@router.get("/export/ddl", response_model=AlertDdlResponse)
async def export_alert_ddl(
    settings: SettingsDep,
    rule_id: Annotated[str | None, Query(description="Export one rule instead of all.")] = None,
) -> AlertDdlResponse:
    """`CREATE ALERT` statements for the customer to review and run (§14, R8).

    The platform never executes these. OFFLINE mode cannot notify anybody, so a
    validated rule set leaves as DDL that runs in the customer's own account —
    each statement carrying its tier and its runbook URL in a comment.
    """
    service = AlertService(settings)
    rules = [service.rule(rule_id)] if rule_id else list(service.rules())
    return AlertDdlResponse(
        rule_ids=[rule.id for rule in rules],
        warehouse=settings.snowflake.warehouse or settings.alerting.ddl_warehouse,
        ddl=service.alert_ddl(rule_id),
    )
