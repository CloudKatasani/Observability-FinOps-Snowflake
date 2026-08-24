"""The declared rule set, and the links that make it actionable (§14, §27.10).

The most valuable assertion in this module is
:func:`test_every_runbook_link_resolves_to_a_real_section`. Every other check
here stops a rule from being *wrong*; that one stops the whole rule set from
quietly becoming decorative, which is how alerting dies in practice — the rules
stay, the runbook sections get renamed, and six months later nobody knows what
to do when one fires.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from snowobs_analytics.alerting import AlertRuleError, AlertTier, Condition
from snowobs_analytics.rules import (
    DEFAULT_RULES_FILE,
    WINDOW_GRAINS,
    ChannelSpec,
    RuleSpec,
    grain_for,
    load_rule_set,
    runbook_anchors,
    runbook_problems,
    slugify,
    window_name,
)
from snowobs_common.errors import ConfigurationError
from snowobs_semantics.model import default_model

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNBOOK = REPO_ROOT / "docs" / "RUNBOOK.md"

#: §14's four tiers and the nine KPI domains. The shipped set covers all of
#: both, because a rule set that watches only cost is a cost report.
DOMAINS = {
    "ai",
    "chargeback",
    "cost",
    "pipeline",
    "quality",
    "query",
    "security",
    "storage",
    "warehouse",
}


@pytest.fixture(scope="module")
def rule_set():  # type: ignore[no-untyped-def]
    return load_rule_set()


@pytest.fixture(scope="module")
def model():  # type: ignore[no-untyped-def]
    return default_model()


def _write(tmp_path: Path, document: dict[str, object]) -> Path:
    path = tmp_path / "alert_rules.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def _rule_document(**overrides: object) -> dict[str, object]:
    rule: dict[str, object] = {
        "id": "cost.spike",
        "name": "Daily spend spike",
        "metric": "cost.billed_credits",
        "condition": "above",
        "threshold": "500",
        "tier": "P2",
        "route": ["chat"],
        "runbook": "/docs/RUNBOOK.md#the-reconciliation-gate-is-red",
    }
    rule.update(overrides)
    return {
        "version": 1,
        "channels": [
            {
                "name": "chat",
                "kind": "webhook",
                "flavour": "slack",
                "url_secret_ref": "env://HOOK",
            }
        ],
        "rules": [rule],
    }


# ═══════════════════════════════════════════════════ the shipped rule set ════
def test_the_shipped_file_loads_and_declares_a_usable_number_of_rules(rule_set) -> None:  # type: ignore[no-untyped-def]
    assert rule_set.path == DEFAULT_RULES_FILE
    assert 12 <= len(rule_set.rules) <= 18, "§14 asks for a starter set, not a catalogue"
    assert len(set(rule_set.ids)) == len(rule_set.ids)


def test_every_declared_rule_watches_a_metric_that_exists(rule_set, model) -> None:  # type: ignore[no-untyped-def]
    """A rule on an undefined metric never fires and nobody ever notices (R1)."""
    for rule in rule_set.rules:
        metric = model.metric(rule.metric_id)  # raises if unknown
        assert metric.requires_sources, rule.id
        # And it must be sliceable the way the rule claims to slice it.
        assert set(rule.scope) <= set(metric.dimensions), rule.id


def test_the_rule_set_spans_the_four_tiers_and_the_nine_domains(rule_set, model) -> None:  # type: ignore[no-untyped-def]
    tiers = {rule.tier for rule in rule_set.rules}
    assert tiers == set(AlertTier), f"missing tiers: {set(AlertTier) - tiers}"
    domains = {model.metric(rule.metric_id).domain for rule in rule_set.rules}
    assert domains == DOMAINS, f"missing domains: {DOMAINS - domains}"


def test_all_three_condition_kinds_are_exercised(rule_set) -> None:  # type: ignore[no-untyped-def]
    """§14's rule model is threshold, delta, *and* anomaly score."""
    conditions = {rule.condition for rule in rule_set.rules}
    assert Condition.ABOVE in conditions or Condition.BELOW in conditions
    assert Condition.DELTA_ABOVE in conditions
    assert Condition.ANOMALY in conditions


def test_every_route_reaches_a_declared_channel_that_accepts_the_tier(rule_set) -> None:  # type: ignore[no-untyped-def]
    for rule in rule_set.rules:
        assert rule.routes, rule.id
        reached = rule_set.channels_for(rule)
        assert reached, f"{rule.id} ({rule.tier.value}) routes to nothing that accepts its tier"


def test_every_threshold_is_decimal_never_float(rule_set) -> None:  # type: ignore[no-untyped-def]
    """§27.7 — floating point for a credit figure, anywhere, is a defect."""
    for rule in rule_set.rules:
        assert isinstance(rule.threshold, Decimal), rule.id
        assert not isinstance(rule.threshold, float)


def test_every_rule_declares_a_window_the_compiler_can_bucket_by(rule_set) -> None:  # type: ignore[no-untyped-def]
    for rule in rule_set.rules:
        assert grain_for(rule) in WINDOW_GRAINS.values()
        assert window_name(rule.window_days) in WINDOW_GRAINS


# ═════════════════════════════════════════════════════════ runbook linkage ════
def test_runbook_anchors_are_parsed_from_headings_and_not_from_code(tmp_path: Path) -> None:
    """A `#` inside a fenced block is a shell comment, not a heading."""
    document = tmp_path / "R.md"
    document.write_text(
        "# The App Is Down\n"
        "\n"
        "```bash\n"
        "# restart the worker\n"
        "systemctl restart snowobs-worker\n"
        "```\n"
        "\n"
        "### A source has gone stale ###\n",
        encoding="utf-8",
    )
    assert runbook_anchors(document) == {"the-app-is-down", "a-source-has-gone-stale"}


def test_slugify_matches_the_anchor_a_markdown_renderer_produces() -> None:
    assert slugify("The reconciliation gate is red") == "the-reconciliation-gate-is-red"
    assert slugify("Dashboards are *slow*") == "dashboards-are-slow"
    assert slugify("A warehouse is queueing  ") == "a-warehouse-is-queueing"


def test_every_runbook_link_resolves_to_a_real_section(rule_set) -> None:  # type: ignore[no-untyped-def]
    """The one that matters (§27.10).

    Every rule links to a section of RUNBOOK.md, and every one of those
    sections exists. If this fails, either a rule was added without writing
    down what to do about it, or a section was renamed out from under a rule
    that still points at it.
    """
    problems = runbook_problems(rule_set, RUNBOOK)
    assert not problems, "broken runbook links:\n  " + "\n  ".join(problems)


def test_a_runbook_link_to_a_missing_section_is_reported(tmp_path: Path) -> None:
    path = _write(tmp_path, _rule_document(runbook="/docs/RUNBOOK.md#no-such-section"))
    problems = runbook_problems(load_rule_set(path), RUNBOOK)
    assert len(problems) == 1
    assert "no-such-section" in problems[0]


def test_a_rule_with_no_runbook_does_not_load(tmp_path: Path) -> None:
    with pytest.raises(AlertRuleError, match="runbook"):
        load_rule_set(_write(tmp_path, _rule_document(runbook="the wiki")))


# ══════════════════════════════════════════════════════════════ validation ════
def test_an_unquoted_threshold_is_rejected_as_a_float(tmp_path: Path) -> None:
    """A YAML `0.15` reaches Decimal already rounded. The file must quote it."""
    with pytest.raises(AlertRuleError, match="never float"):
        load_rule_set(_write(tmp_path, _rule_document(threshold=0.15)))


def test_a_rule_on_an_undefined_metric_does_not_load(tmp_path: Path) -> None:
    with pytest.raises(AlertRuleError, match="semantic layer"):
        load_rule_set(_write(tmp_path, _rule_document(metric="cost.invented")))


def test_a_scope_on_a_dimension_the_metric_lacks_does_not_load(tmp_path: Path) -> None:
    document = _rule_document(scope={"fingerprint": "abc"})
    with pytest.raises(AlertRuleError, match="cannot be sliced by"):
        load_rule_set(_write(tmp_path, document))


def test_a_rule_on_a_snapshot_metric_does_not_load(tmp_path: Path) -> None:
    """A rule needs windows to compare, and a snapshot entity has none."""
    with pytest.raises(AlertRuleError, match="snapshot"):
        load_rule_set(_write(tmp_path, _rule_document(metric="storage.time_travel_ratio")))


def test_a_route_to_an_undeclared_channel_does_not_load(tmp_path: Path) -> None:
    with pytest.raises(AlertRuleError, match="undeclared channel"):
        load_rule_set(_write(tmp_path, _rule_document(route=["pagerduty"])))


def test_a_rule_with_no_route_does_not_load(tmp_path: Path) -> None:
    with pytest.raises(AlertRuleError, match="fire into nothing"):
        load_rule_set(_write(tmp_path, _rule_document(route=[])))


def test_an_anomaly_rule_must_score_a_daily_series(tmp_path: Path) -> None:
    document = _rule_document(condition="anomaly", threshold="3.5", window="week")
    with pytest.raises(AlertRuleError, match="daily series"):
        load_rule_set(_write(tmp_path, document))


def test_duplicate_rule_ids_do_not_load(tmp_path: Path) -> None:
    document = _rule_document()
    document["rules"] = [document["rules"][0], dict(document["rules"][0])]  # type: ignore[index]
    with pytest.raises(AlertRuleError, match="Duplicate alert rule id"):
        load_rule_set(_write(tmp_path, document))


def test_a_missing_rule_file_says_so_rather_than_loading_nothing(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        load_rule_set(tmp_path / "absent.yaml")


def test_an_incomplete_channel_declaration_does_not_load(tmp_path: Path) -> None:
    document = _rule_document()
    document["channels"] = [{"name": "chat", "kind": "webhook"}]
    with pytest.raises(AlertRuleError, match="flavour"):
        load_rule_set(_write(tmp_path, document))


def test_channels_filter_by_tier() -> None:
    channel = ChannelSpec(
        name="digest", kind="email", sender="a@b.invalid", recipients=("c@d.invalid",),
        smtp_host="localhost", tiers=(AlertTier.P4,),
    )
    assert channel.accepts(AlertTier.P4)
    assert not channel.accepts(AlertTier.P1)
    everything = ChannelSpec(
        name="all", kind="webhook", flavour="slack", url_secret_ref="env://HOOK"
    )
    assert all(everything.accepts(tier) for tier in AlertTier)


def test_a_rule_spec_round_trips_into_an_engine_rule() -> None:
    spec = RuleSpec(
        id="cost.spike",
        name="Spike",
        metric="cost.billed_credits",
        condition=Condition.ABOVE,
        threshold=Decimal("500"),
        tier=AlertTier.P2,
        runbook="/docs/RUNBOOK.md#the-reconciliation-gate-is-red",
        route=("chat",),
        window="week",
        persistence=3,
        description="  wrapped\n  across   lines ",
    )
    rule = spec.to_alert_rule()
    assert rule.window_days == 7
    assert rule.persistence == 3
    assert rule.description == "wrapped across lines"
    assert grain_for(rule).value == "week"
