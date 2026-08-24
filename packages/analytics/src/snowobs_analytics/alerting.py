"""Alerting (BUILD_PROMPT §14) — four tiers, with anti-fatigue built in.

The anti-fatigue machinery is not a nice-to-have: an alerting system people
have learned to ignore is worse than none, because it costs the same and
provides false assurance. So:

* a **deduplication ledger** suppresses re-fires while an alert is open;
* a rule that has fired for 60 days **without anyone acting** is proposed for
  pruning, with its statistics;
* every rule must carry a **runbook URL** — a rule without one fails validation
  (§27.10), because an alert nobody knows how to action is noise by design.

Outbound payloads carry the KPI, value, threshold, scope, and runbook link.
They never carry query text (§14).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from snowobs_common.errors import AppError

#: A rule that fires this many times with no action taken is proposed for pruning.
PRUNE_AFTER_DAYS = 60
PRUNE_MIN_FIRES = 5


class AlertTier(StrEnum):
    """The HLD's four tiers (§14)."""

    P1 = "P1"  # business impact now — page + chat, ack 15 min
    P2 = "P2"  # degraded or drifting — chat + ticket, same business day
    P3 = "P3"  # waste or early warning — team channel, weekly triage
    P4 = "P4"  # informational — monthly digest

    @property
    def ack_minutes(self) -> int | None:
        return {AlertTier.P1: 15, AlertTier.P2: 480}.get(self)

    @property
    def channels(self) -> tuple[str, ...]:
        return {
            AlertTier.P1: ("page", "chat"),
            AlertTier.P2: ("chat", "ticket"),
            AlertTier.P3: ("chat",),
            AlertTier.P4: ("digest",),
        }[self]


class Condition(StrEnum):
    ABOVE = "above"
    BELOW = "below"
    DELTA_ABOVE = "delta_above"  # change vs the previous window
    ANOMALY = "anomaly"  # a scored anomaly (§11.2)


class AlertRuleError(AppError):
    status_code = 400
    title = "Invalid alert rule"
    problem_type = "https://snowobs.dev/problems/alert-rule"


@dataclass(frozen=True)
class AlertRule:
    """metric + condition + scope + window + persistence + tier + route + runbook."""

    id: str
    name: str
    metric_id: str
    condition: Condition
    threshold: Decimal
    tier: AlertTier
    #: MANDATORY. A rule without a runbook fails validation (§27.10).
    runbook_url: str
    scope: dict[str, str] = field(default_factory=dict)
    window_days: int = 1
    #: Consecutive breaching windows required before firing.
    persistence: int = 1
    routes: tuple[str, ...] = ()
    enabled: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        if not self.runbook_url or not self.runbook_url.strip():
            raise AlertRuleError(
                f"Rule '{self.id}' has no runbook URL. Every rule must say what to do "
                "when it fires — a rule without a runbook is noise by construction."
            )
        if not self.runbook_url.startswith(("http://", "https://", "/")):
            raise AlertRuleError(
                f"Rule '{self.id}': runbook_url must be a URL or an in-app path, "
                f"got {self.runbook_url!r}"
            )
        if self.persistence < 1:
            raise AlertRuleError(f"Rule '{self.id}': persistence must be at least 1")

    def evaluate(self, value: Decimal | None, previous: Decimal | None = None) -> bool:
        """Does this observation breach? An unknown value never fires (R3)."""
        if value is None:
            return False
        match self.condition:
            case Condition.ABOVE:
                return value > self.threshold
            case Condition.BELOW:
                return value < self.threshold
            case Condition.DELTA_ABOVE:
                if previous is None or previous == 0:
                    return False
                return (value - previous) / abs(previous) > self.threshold
            case Condition.ANOMALY:
                return value >= self.threshold  # value is the anomaly score
        return False


@dataclass
class AlertEvent:
    """One firing. Carries no query text (§14)."""

    rule_id: str
    rule_name: str
    metric_id: str
    tier: AlertTier
    value: Decimal
    threshold: Decimal
    scope: dict[str, str]
    fired_at: datetime
    runbook_url: str
    #: The dedup key: the same rule on the same scope is one alert, not many.
    dedup_key: str = ""
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None
    resolved_at: datetime | None = None
    #: True when the operator did something as a result. Drives rule pruning.
    actioned: bool = False

    def __post_init__(self) -> None:
        if not self.dedup_key:
            scope = ",".join(f"{k}={v}" for k, v in sorted(self.scope.items()))
            self.dedup_key = f"{self.rule_id}|{scope}"

    @property
    def open(self) -> bool:
        return self.resolved_at is None

    def payload(self) -> dict[str, str]:
        """The outbound notification. KPI, value, threshold, scope, runbook — no SQL."""
        return {
            "tier": self.tier.value,
            "rule": self.rule_name,
            "metric": self.metric_id,
            "value": str(self.value),
            "threshold": str(self.threshold),
            "scope": ", ".join(f"{k}={v}" for k, v in sorted(self.scope.items())) or "account",
            "runbook": self.runbook_url,
            "fired_at": self.fired_at.isoformat(),
        }


@dataclass
class DedupLedger:
    """Suppresses re-fires while an alert is open (§14, anti-fatigue)."""

    open_events: dict[str, AlertEvent] = field(default_factory=dict)
    suppressed_count: dict[str, int] = field(default_factory=dict)

    def should_fire(self, event: AlertEvent) -> bool:
        existing = self.open_events.get(event.dedup_key)
        if existing is not None and existing.open:
            self.suppressed_count[event.dedup_key] = (
                self.suppressed_count.get(event.dedup_key, 0) + 1
            )
            return False
        return True

    def record(self, event: AlertEvent) -> None:
        self.open_events[event.dedup_key] = event

    def resolve(self, dedup_key: str, *, at: datetime | None = None) -> None:
        event = self.open_events.get(dedup_key)
        if event is not None:
            event.resolved_at = at or datetime.now(tz=UTC)
            del self.open_events[dedup_key]

    def suppressed(self, dedup_key: str) -> int:
        return self.suppressed_count.get(dedup_key, 0)


@dataclass
class RuleStatistics:
    """Per-rule fire/action counts, shown in the UI (§14)."""

    rule_id: str
    fires: int = 0
    acknowledged: int = 0
    actioned: int = 0
    suppressed: int = 0
    first_fired: datetime | None = None
    last_fired: datetime | None = None

    @property
    def action_rate(self) -> Decimal | None:
        if self.fires == 0:
            return None
        return Decimal(self.actioned) / Decimal(self.fires)

    def prune_recommendation(self, *, now: datetime | None = None) -> str | None:
        """Propose pruning a rule nobody acts on (§14, required)."""
        reference = now or datetime.now(tz=UTC)
        if self.fires < PRUNE_MIN_FIRES or self.first_fired is None:
            return None
        age_days = (reference - self.first_fired).days
        if age_days < PRUNE_AFTER_DAYS:
            return None
        if self.actioned > 0:
            return None
        return (
            f"Rule '{self.rule_id}' has fired {self.fires} times over {age_days} days "
            f"and nobody has acted on any of them. Either the threshold is wrong, the "
            f"runbook does not lead anywhere, or the condition does not matter — "
            f"propose disabling it."
        )


class AlertEngine:
    """Evaluates rules against metric observations, with dedup and statistics."""

    def __init__(self, rules: Sequence[AlertRule]) -> None:
        self.rules = {rule.id: rule for rule in rules}
        self.ledger = DedupLedger()
        self.statistics: dict[str, RuleStatistics] = {
            rule.id: RuleStatistics(rule_id=rule.id) for rule in rules
        }
        self._streaks: dict[str, int] = {}

    def evaluate(
        self,
        rule_id: str,
        value: Decimal | None,
        *,
        scope: dict[str, str] | None = None,
        previous: Decimal | None = None,
        now: datetime | None = None,
    ) -> AlertEvent | None:
        """Evaluate one observation. Returns an event only when it should fire."""
        rule = self.rules[rule_id]
        if not rule.enabled:
            return None

        scope = scope or {}
        key = f"{rule_id}|" + ",".join(f"{k}={v}" for k, v in sorted(scope.items()))
        breaching = rule.evaluate(value, previous)

        if not breaching:
            self._streaks[key] = 0
            self.ledger.resolve(key, at=now)
            return None

        self._streaks[key] = self._streaks.get(key, 0) + 1
        if self._streaks[key] < rule.persistence:
            # Magnitude without persistence is noise (§11.2).
            return None

        if value is None:  # pragma: no cover — breaching implies a value
            return None
        event = AlertEvent(
            rule_id=rule.id,
            rule_name=rule.name,
            metric_id=rule.metric_id,
            tier=rule.tier,
            value=value,
            threshold=rule.threshold,
            scope=scope,
            fired_at=now or datetime.now(tz=UTC),
            runbook_url=rule.runbook_url,
        )

        statistics = self.statistics[rule.id]
        if not self.ledger.should_fire(event):
            statistics.suppressed += 1
            return None

        self.ledger.record(event)
        statistics.fires += 1
        statistics.last_fired = event.fired_at
        if statistics.first_fired is None:
            statistics.first_fired = event.fired_at
        return event

    def acknowledge(self, event: AlertEvent, actor: str, *, actioned: bool = False) -> None:
        event.acknowledged_at = datetime.now(tz=UTC)
        event.acknowledged_by = actor
        event.actioned = actioned
        statistics = self.statistics[event.rule_id]
        statistics.acknowledged += 1
        if actioned:
            statistics.actioned += 1

    def prune_proposals(self, *, now: datetime | None = None) -> list[str]:
        return [
            proposal
            for statistics in self.statistics.values()
            if (proposal := statistics.prune_recommendation(now=now)) is not None
        ]


# ------------------------------------------------------------------ backtest
@dataclass
class BacktestResult:
    """What a rule *would* have done over the uploaded window (§14, OFFLINE)."""

    rule_id: str
    window_start: date
    window_end: date
    would_have_fired: int
    firing_days: list[date] = field(default_factory=list)
    suppressed_by_dedup: int = 0

    def summary(self) -> str:
        if self.would_have_fired == 0:
            return (
                f"'{self.rule_id}' would not have fired between {self.window_start} "
                f"and {self.window_end}. Either the condition is well-behaved or the "
                "threshold is too loose to be useful."
            )
        days = ", ".join(str(day) for day in self.firing_days[:5])
        more = "…" if len(self.firing_days) > 5 else ""
        return (
            f"'{self.rule_id}' would have fired {self.would_have_fired} time(s) between "
            f"{self.window_start} and {self.window_end}: {days}{more}."
        )


def backtest(
    rule: AlertRule,
    series: Sequence[tuple[date, Decimal | None]],
) -> BacktestResult:
    """Replay a rule over historical data before anyone turns it on."""
    engine = AlertEngine([rule])
    ordered = sorted(series, key=lambda pair: pair[0])
    firing_days: list[date] = []
    previous: Decimal | None = None

    for day, value in ordered:
        moment = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
        event = engine.evaluate(rule.id, value, previous=previous, now=moment)
        if event is not None:
            firing_days.append(day)
            # A backtest measures how often the condition arises, so each firing
            # is resolved immediately rather than suppressing the ones after it.
            engine.ledger.resolve(event.dedup_key, at=moment + timedelta(seconds=1))
        previous = value

    return BacktestResult(
        rule_id=rule.id,
        window_start=ordered[0][0] if ordered else date.today(),  # noqa: DTZ011
        window_end=ordered[-1][0] if ordered else date.today(),  # noqa: DTZ011
        would_have_fired=len(firing_days),
        firing_days=firing_days,
        suppressed_by_dedup=sum(engine.ledger.suppressed_count.values()),
    )


def to_snowflake_alert_ddl(rule: AlertRule, *, warehouse: str, schedule: str = "60 MINUTE") -> str:
    """Export a rule as Snowflake ALERT DDL (§14, OFFLINE mode).

    OFFLINE mode cannot notify, so a validated rule leaves as deployable DDL the
    customer runs in their own account.
    """
    comparison = {
        Condition.ABOVE: ">",
        Condition.BELOW: "<",
        Condition.DELTA_ABOVE: ">",
        Condition.ANOMALY: ">=",
    }[rule.condition]
    scope_predicate = "".join(
        f"\n      AND {key} = '{value}'" for key, value in sorted(rule.scope.items())
    )
    return (
        f"-- {rule.name} ({rule.tier.value})\n"
        f"-- Runbook: {rule.runbook_url}\n"
        f"CREATE OR REPLACE ALERT ALERT_{rule.id.upper().replace('.', '_')}\n"
        f"  WAREHOUSE = {warehouse}\n"
        f"  SCHEDULE = '{schedule}'\n"
        f"  IF (EXISTS (\n"
        f"    SELECT 1 FROM OBSERVABILITY.PUBLISHED.V_{rule.metric_id.upper().replace('.', '_')}\n"
        f"    WHERE METRIC_VALUE {comparison} {rule.threshold}{scope_predicate}\n"
        f"  ))\n"
        f"  THEN CALL SYSTEM$SEND_EMAIL(\n"
        f"    'snowobs_alerts',\n"
        f"    '<recipient>',\n"
        f"    '[{rule.tier.value}] {rule.name}',\n"
        f"    'Metric {rule.metric_id} breached {rule.threshold}. "
        f"Runbook: {rule.runbook_url}'\n"
        f"  );\n"
    )
