"""Alert rule evaluation, orchestrated over the governed metric layer (§14).

Services hold orchestration only (§5). Every figure a rule is judged against
comes from the semantic compiler and the configured engine — the same path a
dashboard tile takes — so a rule cannot fire on a number nobody can reproduce,
and "show the SQL" works for an alert exactly as it does for a chart (R5).

Three behaviours are load-bearing:

* **An unknown value never fires** (R3). A metric whose sources have not landed
  produces a skipped observation carrying the reason, not a breach. The most
  damaging thing an alerting system can do is page somebody about data it does
  not have.
* **Persistence is evaluated from the data, not from process memory.** Each run
  feeds the last ``persistence`` windows of the series into the engine in
  order, so the answer is the same whether the worker has been up for a month
  or started thirty seconds ago.
* **Dedup is process state, and is honest about it.** The ledger suppresses
  re-fires while an alert is open; a restart clears it (A-24).
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from snowobs_analytics.alerting import (
    AlertEngine,
    AlertEvent,
    AlertRule,
    BacktestResult,
    Condition,
    RuleStatistics,
    backtest,
    to_snowflake_alert_ddl,
)
from snowobs_analytics.anomaly import Point, detect
from snowobs_analytics.channels import (
    AlertNotification,
    DeliveryResult,
    NotificationChannel,
    build_channels,
    dispatch,
)
from snowobs_analytics.rules import RuleSet, grain_for, load_rule_set, window_name
from snowobs_api.services.engines import EngineChoice, open_engine
from snowobs_common.config import Settings
from snowobs_common.errors import NotFoundError
from snowobs_common.logging import get_logger
from snowobs_common.secrets import SecretResolver, build_resolver
from snowobs_semantics.compiler import Filter, MetricRequest, Order, SemanticCompiler, TimeRange
from snowobs_semantics.model import Metric, default_model

logger = get_logger(__name__)

#: Rows fetched per rule. A 400-day lookback at day grain is the widest a
#: declared window can ask for, and the guard forces a LIMIT regardless (R9).
SERIES_LIMIT = 500

#: The dedup ledger and per-rule statistics live in the process that evaluated
#: the rule. Keyed by rule-file path so a test using its own file does not
#: inherit another one's open alerts.
_ENGINES: dict[str, AlertEngine] = {}


def alert_engine_for(rule_set: RuleSet) -> AlertEngine:
    """The process-wide engine for a rule set.

    An edited rule file is picked up without losing state: the engine is rebuilt
    around the new definitions, and the dedup ledger and the statistics of every
    rule that survived the edit are carried across. Discarding them would mean
    that raising one threshold re-fires every other open alert in the set.
    """
    key = str(rule_set.path)
    engine = _ENGINES.get(key)
    if engine is not None and _same_definitions(engine, rule_set):
        return engine

    rebuilt = AlertEngine(list(rule_set.rules))
    if engine is not None:
        rebuilt.ledger = engine.ledger
        for rule_id, statistics in engine.statistics.items():
            if rule_id in rebuilt.statistics:
                rebuilt.statistics[rule_id] = statistics
    _ENGINES[key] = rebuilt
    return rebuilt


def _same_definitions(engine: AlertEngine, rule_set: RuleSet) -> bool:
    """Are the engine's rules the same objects the file now declares?

    Compared field by field rather than by identity: ``AlertRule`` carries a
    mutable ``scope`` mapping, so it is not hashable and cannot go in a set.
    """
    if list(engine.rules) != list(rule_set.ids):
        return False
    return all(engine.rules[rule.id] == rule for rule in rule_set.rules)


def reset_alert_engines() -> None:
    """Drop all in-process alert state. Used between test modules."""
    _ENGINES.clear()


@dataclass(frozen=True)
class Observation:
    """What the metric layer said about one rule, this run."""

    rule_id: str
    metric_id: str
    #: The figure the condition is compared against — the KPI value, or the
    #: anomaly score for a scored rule.
    values: tuple[tuple[date, Decimal], ...] = ()
    #: The KPI's own value per window, which differs from `values` only for a
    #: scored rule.
    observed: dict[date, Decimal] = field(default_factory=dict)
    #: Set when the rule could not be evaluated. R3: say why, never assume zero.
    skipped_because: str | None = None
    as_of: datetime | None = None
    latency_floor_minutes: int = 0
    sources: tuple[str, ...] = ()
    sql: str = ""

    @property
    def evaluated(self) -> bool:
        return self.skipped_because is None


@dataclass(frozen=True)
class RuleOutcome:
    """One rule's result for one evaluation run."""

    rule_id: str
    fired: bool
    event: AlertEvent | None
    skipped_because: str | None
    deliveries: tuple[DeliveryResult, ...] = ()


@dataclass(frozen=True)
class EvaluationReport:
    """A whole run, in a shape that survives being written to a job result."""

    ran_at: datetime
    rules_evaluated: int
    rules_skipped: int
    fired: int
    outcomes: tuple[RuleOutcome, ...]
    mode: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "ran_at": self.ran_at.isoformat(),
            "mode": self.mode,
            "rules_evaluated": self.rules_evaluated,
            "rules_skipped": self.rules_skipped,
            "fired": self.fired,
            "fired_rules": [o.rule_id for o in self.outcomes if o.fired],
            "skipped": {
                o.rule_id: o.skipped_because for o in self.outcomes if o.skipped_because is not None
            },
            "deliveries": {
                o.rule_id: [
                    {"channel": d.channel, "delivered": d.delivered, "detail": d.detail}
                    for d in o.deliveries
                ]
                for o in self.outcomes
                if o.deliveries
            },
        }


class AlertService:
    """Loads the declared rules and runs them against the metric layer."""

    def __init__(
        self,
        settings: Settings,
        tenant: str = "default",
        *,
        rule_set: RuleSet | None = None,
        channels: tuple[NotificationChannel, ...] | None = None,
        resolver: SecretResolver | None = None,
    ) -> None:
        self.settings = settings
        self.tenant = tenant
        self.rule_set = rule_set or load_rule_set(settings.alerting.rules_file)
        self.model = default_model()
        self.compiler = SemanticCompiler(self.model)
        self.engine = alert_engine_for(self.rule_set)
        self._resolver = resolver
        self._channels = channels

    # ------------------------------------------------------------- read side
    def rules(self) -> tuple[AlertRule, ...]:
        return self.rule_set.rules

    def rule(self, rule_id: str) -> AlertRule:
        for rule in self.rule_set.rules:
            if rule.id == rule_id:
                return rule
        raise NotFoundError(
            f"No alert rule '{rule_id}' is declared in {self.rule_set.path.name}. "
            f"Declared rules: {', '.join(self.rule_set.ids)}."
        )

    def metric_for(self, rule: AlertRule) -> Metric:
        return self.model.metric(rule.metric_id)

    def statistics(self, rule_id: str) -> RuleStatistics:
        return self.engine.statistics[rule_id]

    def prune_proposals(self, *, now: datetime | None = None) -> list[str]:
        return self.engine.prune_proposals(now=now)

    def alert_ddl(self, rule_id: str | None = None) -> str:
        """OFFLINE export: deployable `CREATE ALERT` DDL (§14)."""
        warehouse = self.settings.snowflake.warehouse or self.settings.alerting.ddl_warehouse
        rules = [self.rule(rule_id)] if rule_id else list(self.rule_set.rules)
        return "\n".join(
            to_snowflake_alert_ddl(
                rule,
                warehouse=warehouse,
                schedule=_schedule_for(rule.window_days),
            )
            for rule in rules
        )

    # ------------------------------------------------------------ evaluation
    def _engine_context(self) -> AbstractContextManager[EngineChoice]:
        return open_engine(self.settings, tenant=self.tenant)

    def observe_rule(self, rule_id: str) -> Observation:
        """One rule's window series, opening and closing the engine itself."""
        rule = self.rule(rule_id)
        with self._engine_context() as chosen:
            return self.observe(rule, chosen)

    def observe(self, rule: AlertRule, chosen: EngineChoice) -> Observation:
        """Fetch the rule's window series through the governed metric layer."""
        metric = self.metric_for(rule)
        available = chosen.engine.available_relations()
        missing = sorted(s for s in metric.requires_sources if s.upper() not in available)
        if missing:
            return Observation(
                rule_id=rule.id,
                metric_id=metric.id,
                skipped_because=(
                    f"{metric.id} is unavailable — requires {', '.join(missing)}. "
                    "A rule never fires on data the platform does not have (R3)."
                ),
            )

        window = _landed_window(chosen, metric, self.settings.alerting.lookback_days)
        if window is None:
            return Observation(
                rule_id=rule.id,
                metric_id=metric.id,
                skipped_because=(
                    f"No landed window for {', '.join(metric.requires_sources)}; "
                    "nothing to evaluate."
                ),
            )
        start, end = window

        request = MetricRequest(
            metrics=[metric.id],
            filters=[
                Filter(dimension=key, value=value) for key, value in sorted(rule.scope.items())
            ],
            time_range=TimeRange(start=start, end=end),
            grain=grain_for(rule),
            limit=SERIES_LIMIT,
            order=[Order(field="TIME_BUCKET", descending=False)],
            bucket_time=True,
        )
        compiled = self.compiler.compile(request, chosen.dialect)
        result = chosen.engine.execute(compiled)

        series: list[tuple[date, Decimal]] = []
        for row in result.rows:
            when = _as_date(row[0])
            value = _as_decimal(row[-1])
            if when is None or value is None:
                # A null aggregate is an unknown, not a zero (R3, §27.11).
                continue
            series.append((when, value))
        series.sort(key=lambda pair: pair[0])

        if not series:
            return Observation(
                rule_id=rule.id,
                metric_id=metric.id,
                skipped_because=(
                    f"{metric.id} returned no rows between {start} and {end}; "
                    "there is nothing to compare against a threshold."
                ),
                as_of=result.as_of,
                latency_floor_minutes=result.latency_floor_minutes,
                sources=tuple(result.sources),
                sql=result.executed_sql,
            )

        observed = dict(series)
        if rule.condition is Condition.ANOMALY:
            series = _anomaly_scores(series, rule)

        return Observation(
            rule_id=rule.id,
            metric_id=metric.id,
            values=tuple(series),
            observed=observed,
            as_of=result.as_of,
            latency_floor_minutes=result.latency_floor_minutes,
            sources=tuple(result.sources),
            sql=result.executed_sql,
        )

    def evaluate(self, rule: AlertRule, observation: Observation) -> AlertEvent | None:
        """Feed the last `persistence` windows to the engine, in order.

        Replaying exactly as many windows as the rule requires makes the
        decision a function of the data: the streak that fires a rule is the one
        visible in the series, not one accumulated by however many times this
        job happened to run today.
        """
        if not observation.evaluated or not observation.values:
            return None
        tail = observation.values[-rule.persistence :]
        previous_by_day = {
            day: observation.values[index - 1][1] if index else None
            for index, (day, _) in enumerate(observation.values)
        }
        event: AlertEvent | None = None
        for day, value in tail:
            moment = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
            event = self.engine.evaluate(
                rule.id,
                value,
                scope=dict(rule.scope),
                previous=previous_by_day[day],
                now=moment,
            )
        return event

    def notification_for(
        self, rule: AlertRule, event: AlertEvent, observed: Decimal | None
    ) -> AlertNotification:
        metric = self.metric_for(rule)
        return AlertNotification.from_event(
            event,
            kpi_name=metric.name,
            unit=metric.format.unit,
            condition=(
                f"{rule.condition.value} {rule.threshold} over "
                f"{rule.persistence} × {window_name(rule.window_days)}"
            ),
            observed_value=observed if rule.condition is Condition.ANOMALY else None,
        )

    def channels(self) -> tuple[NotificationChannel, ...]:
        if self._channels is not None:
            return self._channels
        resolver = self._resolver
        if resolver is None and self.settings.alerting.enabled:
            resolver = build_resolver(self.settings)
        self._channels = build_channels(
            self.rule_set.channels,
            resolver=resolver,
            enabled=self.settings.alerting.enabled,
        )
        return self._channels

    def run_once(self) -> EvaluationReport:
        """Evaluate every enabled rule and dispatch what fires."""
        ran_at = datetime.now(tz=UTC)
        outcomes: list[RuleOutcome] = []
        evaluated = skipped = fired = 0

        with self._engine_context() as chosen:
            for rule in self.rule_set.enabled_rules():
                observation = self.observe(rule, chosen)
                if not observation.evaluated:
                    skipped += 1
                    logger.info(
                        "alert_rule_skipped",
                        rule_id=rule.id,
                        metric_id=rule.metric_id,
                        reason=observation.skipped_because,
                    )
                    outcomes.append(RuleOutcome(rule.id, False, None, observation.skipped_because))
                    continue

                evaluated += 1
                event = self.evaluate(rule, observation)
                if event is None:
                    outcomes.append(RuleOutcome(rule.id, False, None, None))
                    continue

                fired += 1
                notification = self.notification_for(
                    rule, event, observation.observed.get(event.fired_at.date())
                )
                deliveries = tuple(dispatch(notification, self.channels()))
                logger.info(
                    "alert_fired",
                    rule_id=rule.id,
                    tier=rule.tier.value,
                    metric_id=rule.metric_id,
                    dedup_key=event.dedup_key,
                    delivered=[d.channel for d in deliveries if d.delivered],
                )
                outcomes.append(RuleOutcome(rule.id, True, event, None, deliveries))

            mode = chosen.mode

        return EvaluationReport(
            ran_at=ran_at,
            rules_evaluated=evaluated,
            rules_skipped=skipped,
            fired=fired,
            outcomes=tuple(outcomes),
            mode=mode,
        )

    # -------------------------------------------------------------- backtest
    def backtest(
        self, rule_id: str, *, start: date | None = None, end: date | None = None
    ) -> tuple[BacktestResult, Observation]:
        """Replay a rule over history — "it would have fired 4 times" (§14).

        The backtest runs on its own engine instance, so validating a rule never
        touches the live dedup ledger or the rule's statistics.
        """
        rule = self.rule(rule_id)
        with self._engine_context() as chosen:
            observation = self.observe(rule, chosen)
        if not observation.evaluated:
            return (
                BacktestResult(
                    rule_id=rule.id,
                    window_start=start or date.today(),  # noqa: DTZ011 — account-date granularity
                    window_end=end or date.today(),  # noqa: DTZ011
                    would_have_fired=0,
                ),
                observation,
            )
        series: list[tuple[date, Decimal | None]] = [
            (day, value)
            for day, value in observation.values
            if (start is None or day >= start) and (end is None or day <= end)
        ]
        return backtest(rule, series), observation


# ------------------------------------------------------------------ helpers
def _schedule_for(window_days: int) -> str:
    """Snowflake ALERT `SCHEDULE` matching the rule's declared window."""
    return {1: "1440 MINUTE", 7: "USING CRON 0 6 * * MON UTC", 30: "USING CRON 0 6 1 * * UTC"}[
        window_days
    ]


def _anomaly_scores(
    series: list[tuple[date, Decimal]], rule: AlertRule
) -> list[tuple[date, Decimal]]:
    """Replace KPI values with robust z-scores from the shared detector.

    The scoring lives in `analytics.anomaly` and is not reimplemented here:
    persistence is applied by the alert engine instead, so a day is scored on
    its own merits and the streak is counted once rather than twice.
    """
    anomalies = detect(
        [Point(day=day, value=value) for day, value in series],
        z_threshold=float(rule.threshold),
        require_persistence=1,
    )
    scores = {anomaly.day: Decimal(str(abs(anomaly.z_score))) for anomaly in anomalies}
    return [(day, scores.get(day, Decimal(0))) for day, _ in series]


def _landed_window(
    chosen: EngineChoice, metric: Metric, lookback_days: int
) -> tuple[date, date] | None:
    """The period to evaluate, per mode.

    OFFLINE has a landed window: evaluating past the end of an extract compares
    a threshold against a day that simply was not exported. LIVE has no such
    boundary, so it looks back a fixed number of complete days.
    """
    if chosen.mode == "live":
        end = date.today() - timedelta(days=1)  # noqa: DTZ011 — account-date granularity
        return end - timedelta(days=lookback_days - 1), end

    catalog = getattr(chosen.engine, "catalog", None)
    if catalog is None:  # pragma: no cover - defensive
        return None

    window = _window_across(catalog, metric.requires_sources)
    if window is None:
        # Not every source carries a date column the loader can profile —
        # GRANTS_TO_USERS and USERS are keyed by object, not by day — so a
        # metric built on them has a time column while its inputs report no
        # window. Falling back to the lake's own window evaluates those rules
        # over the period the extract actually covers, rather than declaring a
        # landed source unevaluable.
        window = _window_across(catalog, catalog.landed_sources(), overlap=False)
    if window is None:
        return None
    start, end = window
    return max(start, end - timedelta(days=lookback_days - 1)), end


def _window_across(
    catalog: Any, source_ids: Sequence[str], *, overlap: bool = True
) -> tuple[date, date] | None:
    """The landed window across these sources, if any of them reports one.

    ``overlap=True`` intersects: a day only one of a metric's two inputs covers
    cannot be evaluated. ``overlap=False`` unions, which is what "how much
    history does this lake hold" means.
    """
    starts: list[date] = []
    ends: list[date] = []
    for source_id in source_ids:
        stats = catalog.stats(source_id)
        if stats is not None and stats.window is not None:
            starts.append(stats.window[0])
            ends.append(stats.window[1])
    if not starts:
        return None
    if overlap:
        return max(starts), min(ends)
    return min(starts), max(ends)


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


__all__ = [
    "AlertService",
    "EvaluationReport",
    "Observation",
    "RuleOutcome",
    "alert_engine_for",
    "reset_alert_engines",
]
