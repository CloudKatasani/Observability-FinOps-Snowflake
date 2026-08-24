"""The declared rule set: loading, validating, and linking `config/alert_rules.yaml`.

:mod:`snowobs_analytics.alerting` is the *engine* — tiers, dedup, persistence,
statistics, pruning, backtest, DDL export. This module is what turns declared
configuration into rules that engine can run, and it refuses everything that
would produce noise:

* a rule naming a metric the semantic layer does not define;
* a scope key that is not a dimension of that metric (a filter on a column the
  entity does not have compiles to nothing useful, and the rule would evaluate
  something other than what it claims);
* a route naming a channel nobody declared;
* a threshold written as a bare YAML float — credits and currency are Decimal
  or they are wrong (§27.7);
* an anomaly condition on anything but a daily window, because the detector
  scores a daily series;
* a runbook link that does not resolve to a real heading in ``docs/RUNBOOK.md``
  (:func:`runbook_problems`, asserted in the test suite).

The last one is the point of the whole module. §27.10 says a metric without a
parity test and an alert without a runbook are the same defect: something that
looks finished and is not actionable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from snowobs_analytics.alerting import AlertRule, AlertRuleError, AlertTier, Condition
from snowobs_common.errors import ConfigurationError
from snowobs_semantics.model import SemanticModel, TimeGrain, default_model

#: The shipped rule set. Deployments override it with ``ALERTING__RULES_FILE``.
DEFAULT_RULES_FILE = Path(__file__).resolve().parents[4] / "config" / "alert_rules.yaml"

#: Declared window widths and the calendar grain each one evaluates over. A
#: month is a calendar month, not thirty days; ``window_days`` below is the
#: nominal width carried on the rule for display and for the DDL export, and
#: the grain is what the query is actually bucketed by.
WINDOW_GRAINS: dict[str, TimeGrain] = {
    "day": TimeGrain.DAY,
    "week": TimeGrain.WEEK,
    "month": TimeGrain.MONTH,
}
WINDOW_DAYS: dict[str, int] = {"day": 1, "week": 7, "month": 30}
_DAYS_TO_WINDOW: dict[int, str] = {days: name for name, days in WINDOW_DAYS.items()}


def window_name(window_days: int) -> str:
    """``"week"`` for 7. The inverse of :data:`WINDOW_DAYS`."""
    try:
        return _DAYS_TO_WINDOW[window_days]
    except KeyError:
        raise AlertRuleError(
            f"{window_days} is not a declarable window width; use one of "
            f"{', '.join(sorted(WINDOW_DAYS))}"
        ) from None


def grain_for(rule: AlertRule) -> TimeGrain:
    """The time grain this rule's evaluation windows are bucketed by."""
    return WINDOW_GRAINS[window_name(rule.window_days)]


class ChannelSpec(BaseModel):
    """A declared notification channel (§14). Secrets are held by reference."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    kind: Literal["webhook", "email"]
    description: str = ""
    #: Tiers this channel accepts. Empty means every tier.
    tiers: tuple[AlertTier, ...] = ()

    # webhook
    flavour: Literal["slack", "teams"] | None = None
    url_secret_ref: str | None = None

    # email
    sender: str | None = None
    recipients: tuple[str, ...] = ()
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password_secret_ref: str | None = None
    starttls: bool = True

    @model_validator(mode="after")
    def _complete_for_its_kind(self) -> ChannelSpec:
        if self.kind == "webhook":
            missing = [
                name
                for name, value in (
                    ("flavour", self.flavour),
                    ("url_secret_ref", self.url_secret_ref),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"webhook channel '{self.name}' is missing {', '.join(missing)}")
        else:
            missing = [
                name
                for name, value in (
                    ("sender", self.sender),
                    ("recipients", self.recipients),
                    ("smtp_host", self.smtp_host),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"email channel '{self.name}' is missing {', '.join(missing)}")
        return self

    def accepts(self, tier: AlertTier) -> bool:
        return not self.tiers or tier in self.tiers


class RuleSpec(BaseModel):
    """One declared rule, as written in YAML."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
    name: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    condition: Condition
    threshold: Decimal
    tier: AlertTier
    runbook: str = Field(min_length=1)
    route: tuple[str, ...] = ()
    scope: dict[str, str] = Field(default_factory=dict)
    window: Literal["day", "week", "month"] = "day"
    persistence: int = Field(default=1, ge=1, le=30)
    enabled: bool = True
    description: str = ""

    @field_validator("threshold", mode="before")
    @classmethod
    def _never_a_float(cls, value: Any) -> Any:
        """§27.7: no floating point for a credit, a dollar, or a limit on one.

        A YAML scalar written as ``0.15`` parses to a Python float and reaches
        Decimal already rounded. Quoting it in the file is the whole fix, so
        the error says exactly that.
        """
        if isinstance(value, float):
            raise ValueError(
                "threshold must be quoted in YAML so it parses exactly as Decimal — "
                f'write "{value!r}" rather than {value!r} (§27.7: never float for a '
                "credit or a currency figure)"
            )
        return value

    @model_validator(mode="after")
    def _anomaly_scores_a_daily_series(self) -> RuleSpec:
        if self.condition is Condition.ANOMALY and self.window != "day":
            raise ValueError(
                f"rule '{self.id}': an anomaly condition scores a daily series "
                f"(§11.2), so its window must be 'day', not '{self.window}'"
            )
        if not self.route:
            raise ValueError(f"rule '{self.id}' declares no route; it would fire into nothing")
        return self

    def to_alert_rule(self) -> AlertRule:
        """The engine's rule. Construction re-checks the runbook (§27.10)."""
        return AlertRule(
            id=self.id,
            name=self.name,
            metric_id=self.metric,
            condition=self.condition,
            threshold=self.threshold,
            tier=self.tier,
            runbook_url=self.runbook,
            scope=dict(self.scope),
            window_days=WINDOW_DAYS[self.window],
            persistence=self.persistence,
            routes=self.route,
            enabled=self.enabled,
            description=" ".join(self.description.split()),
        )


class RuleDocument(BaseModel):
    """The file as a whole."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = 1
    channels: tuple[ChannelSpec, ...] = ()
    rules: tuple[RuleSpec, ...] = ()


@dataclass(frozen=True)
class RuleSet:
    """The loaded, cross-validated rule set."""

    rules: tuple[AlertRule, ...]
    channels: tuple[ChannelSpec, ...]
    path: Path

    def rule(self, rule_id: str) -> AlertRule:
        for rule in self.rules:
            if rule.id == rule_id:
                return rule
        raise AlertRuleError(f"No alert rule with id '{rule_id}' is declared in {self.path}")

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(rule.id for rule in self.rules)

    def enabled_rules(self) -> tuple[AlertRule, ...]:
        return tuple(rule for rule in self.rules if rule.enabled)

    def channel(self, name: str) -> ChannelSpec:
        for channel in self.channels:
            if channel.name == name:
                return channel
        raise AlertRuleError(f"No notification channel named '{name}' is declared in {self.path}")

    def channels_for(self, rule: AlertRule) -> tuple[ChannelSpec, ...]:
        """The declared channels a firing of this rule should reach.

        A route whose channel does not accept the rule's tier is dropped here
        rather than at send time, so a P4 cannot end up on the paging path by
        being routed at it.
        """
        return tuple(
            channel for name in rule.routes if (channel := self.channel(name)).accepts(rule.tier)
        )

    def domains(self, model: SemanticModel | None = None) -> dict[str, tuple[str, ...]]:
        """Rule ids by the domain of the metric each one watches."""
        resolved = model or default_model()
        by_domain: dict[str, list[str]] = {}
        for rule in self.rules:
            by_domain.setdefault(resolved.metric(rule.metric_id).domain, []).append(rule.id)
        return {domain: tuple(ids) for domain, ids in sorted(by_domain.items())}


def load_rule_set(path: str | Path | None = None, *, model: SemanticModel | None = None) -> RuleSet:
    """Parse and validate a rule file. Raises rather than half-loading.

    A rule set is either wholly valid or it does not load: partially applying
    an alerting configuration produces an operator who believes they are
    covered for something nobody is watching.
    """
    file = Path(path) if path is not None else DEFAULT_RULES_FILE
    if not file.is_file():
        raise ConfigurationError(
            f"Alert rule file not found: {file}. Point ALERTING__RULES_FILE at one, "
            f"or restore the shipped {DEFAULT_RULES_FILE.name}."
        )
    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Alert rule file {file} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError(f"Alert rule file {file} must be a mapping, not {type(raw)}")

    try:
        document = RuleDocument.model_validate(raw)
    except Exception as exc:
        raise AlertRuleError(f"Invalid alert rule file {file}: {exc}") from exc

    rule_set = RuleSet(
        rules=tuple(spec.to_alert_rule() for spec in document.rules),
        channels=document.channels,
        path=file,
    )
    _cross_validate(rule_set, model or default_model())
    return rule_set


def _cross_validate(rule_set: RuleSet, model: SemanticModel) -> None:
    """The checks YAML schema alone cannot make."""
    seen: set[str] = set()
    channel_names = {channel.name for channel in rule_set.channels}
    duplicate_channels = [
        channel.name
        for index, channel in enumerate(rule_set.channels)
        if channel.name in {other.name for other in rule_set.channels[:index]}
    ]
    if duplicate_channels:
        raise AlertRuleError(f"Duplicate channel name(s): {', '.join(sorted(duplicate_channels))}")

    for rule in rule_set.rules:
        if rule.id in seen:
            raise AlertRuleError(f"Duplicate alert rule id: {rule.id}")
        seen.add(rule.id)

        try:
            metric = model.metric(rule.metric_id)
        except ConfigurationError as exc:
            raise AlertRuleError(
                f"Rule '{rule.id}' watches '{rule.metric_id}', which the semantic layer "
                f"does not define. Rules run through the governed metric layer or they "
                f"do not run at all (R1)."
            ) from exc

        if model.entity(metric.entity).time_column is None:
            raise AlertRuleError(
                f"Rule '{rule.id}' watches '{metric.id}', which sits on the snapshot "
                f"entity '{metric.entity}'. A rule needs windows to compare, and a "
                f"snapshot has none — watch a metric on a time-series entity instead."
            )

        unknown_scope = sorted(set(rule.scope) - set(metric.dimensions))
        if unknown_scope:
            raise AlertRuleError(
                f"Rule '{rule.id}' scopes on {unknown_scope}, which "
                f"'{metric.id}' cannot be sliced by. Available: "
                f"{', '.join(metric.dimensions) or 'none'}."
            )

        unknown_routes = sorted(set(rule.routes) - channel_names)
        if unknown_routes:
            raise AlertRuleError(
                f"Rule '{rule.id}' routes to undeclared channel(s): {', '.join(unknown_routes)}"
            )


# ---------------------------------------------------------------- runbooks --
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_SLUG_STRIP = re.compile(r"[^\w\- ]", flags=re.UNICODE)


def slugify(heading: str) -> str:
    """GitHub's heading-anchor slug: lowercase, punctuation dropped, spaces hyphenated."""
    text = _SLUG_STRIP.sub("", heading.strip().lower())
    return re.sub(r"\s+", "-", text).strip("-")


def runbook_anchors(path: str | Path) -> set[str]:
    """Every anchor a markdown file's headings expose.

    Fenced code blocks are skipped: a shell comment (``# restart the worker``)
    inside a fence is not a heading, and treating it as one would let a rule
    link to an anchor that does not exist in the rendered document.
    """
    file = Path(path)
    if not file.is_file():
        raise ConfigurationError(f"Runbook not found: {file}")
    anchors: set[str] = set()
    in_fence = False
    for line in file.read_text(encoding="utf-8").splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING.match(line)
        if match is not None and match.group(2):
            anchors.add(slugify(match.group(2)))
    return anchors


def runbook_problems(rule_set: RuleSet, runbook_path: str | Path) -> list[str]:
    """Rules whose runbook link does not land on a real section.

    Returned rather than raised so a caller can report every broken link at
    once; the test suite asserts the list is empty.
    """
    anchors = runbook_anchors(runbook_path)
    document = Path(runbook_path).name
    problems: list[str] = []
    for rule in rule_set.rules:
        target, _, fragment = rule.runbook_url.partition("#")
        if not target.endswith(document):
            # An external runbook (a wiki, a vendor page) is a deployment's
            # choice and cannot be verified from here.
            continue
        if not fragment:
            problems.append(f"{rule.id}: links to {document} with no section anchor")
        elif fragment not in anchors:
            problems.append(
                f"{rule.id}: '#{fragment}' is not a heading in {document} — "
                f"add the section or fix the link"
            )
    return problems


__all__ = [
    "DEFAULT_RULES_FILE",
    "WINDOW_DAYS",
    "WINDOW_GRAINS",
    "ChannelSpec",
    "RuleDocument",
    "RuleSet",
    "RuleSpec",
    "grain_for",
    "load_rule_set",
    "runbook_anchors",
    "runbook_problems",
    "slugify",
    "window_name",
]
