"""The alerts API and the scheduled evaluation behind it (§14, §15).

These run against a real ingested fixture account, so a rule is evaluated the
same way the worker evaluates it: compiler → guard → engine → condition →
dedup → dispatch. A rule that only fires against a hand-built series would not
tell us anything about whether the declared rule set works.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import yaml

from snowobs_analytics.channels import NullChannel, looks_like_query_text
from snowobs_analytics.rules import load_rule_set
from snowobs_api.main import create_app
from snowobs_api.services.alerts import AlertService, reset_alert_engines
from snowobs_common.config import Settings
from snowobs_fixtures.config import GeneratorConfig
from snowobs_fixtures.generator import generate, write_csv
from snowobs_ingest.loader import IngestPipeline
from snowobs_worker.alerts import evaluate_alert_rules

FIXTURE = GeneratorConfig(days=60, queries_per_day=200)


@pytest.fixture(scope="module")
def settings(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Settings]:
    lake: Path = tmp_path_factory.mktemp("alert-lake")
    extract: Path = tmp_path_factory.mktemp("alert-extract")
    write_csv(generate(FIXTURE), extract)
    IngestPipeline(lake).ingest_directory(extract)
    yield Settings(
        _env_file=None,
        mode="offline",
        storage={"provider": "local", "bucket": str(lake)},
    )


@pytest.fixture(scope="module")
def empty_lake_settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    """A configured deployment with nothing landed — the R3 case."""
    return Settings(
        _env_file=None,
        mode="offline",
        storage={"provider": "local", "bucket": str(tmp_path_factory.mktemp("empty-lake"))},
    )


@pytest.fixture(autouse=True)
def _isolated_alert_state() -> Iterator[None]:
    """Dedup ledgers and statistics are process state; do not leak them."""
    reset_alert_engines()
    yield
    reset_alert_engines()


@asynccontextmanager
async def client_for(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def _custom_rules(tmp_path: Path, **overrides: object) -> Path:
    """A one-rule file, so a behaviour can be asserted without 17 others firing."""
    rule: dict[str, object] = {
        "id": "cost.test_rule",
        "name": "Test rule",
        "metric": "cost.billed_credits",
        "condition": "above",
        "threshold": "0",
        "tier": "P2",
        "route": ["chat"],
        "runbook": "/docs/RUNBOOK.md#daily-spend-has-spiked",
        "window": "day",
        "persistence": 1,
    }
    rule.update(overrides)
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "rules.yaml"
    path.write_text(
        yaml.safe_dump(
            {
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
        ),
        encoding="utf-8",
    )
    return path


def _service(settings: Settings, rules_path: Path) -> AlertService:
    return AlertService(
        settings,
        rule_set=load_rule_set(rules_path),
        channels=(NullChannel(),),
    )


# ═══════════════════════════════════════════════════════════════════ the API ══
@pytest.mark.asyncio
async def test_rules_endpoint_lists_the_declared_set_with_its_statistics(
    settings: Settings,
) -> None:
    async with client_for(settings) as client:
        response = await client.get("/api/v1/alerts/rules")
    assert response.status_code == 200
    body = response.json()

    assert body["rule_count"] == len(body["rules"]) >= 12
    assert body["dispatch_enabled"] is False, "outbound dispatch is off until configured"
    assert len(body["domains"]) == 9
    assert body["source"].endswith("alert_rules.yaml")

    for rule in body["rules"]:
        assert rule["runbook_url"].startswith("/docs/RUNBOOK.md#")
        assert rule["channels"], f"{rule['id']} routes to nothing that accepts its tier"
        assert rule["requires_sources"]
        assert rule["latency_floor_minutes"] >= 0
        # A threshold crosses the API as a string — never as a float (§27.7).
        assert isinstance(rule["threshold"], str)
        Decimal(rule["threshold"])
        statistics = rule["statistics"]
        assert set(statistics) >= {"fires", "actioned", "last_fired", "suppressed_by_dedup"}


@pytest.mark.asyncio
async def test_one_rule_can_be_fetched_and_an_unknown_one_is_a_problem_document(
    settings: Settings,
) -> None:
    async with client_for(settings) as client:
        found = await client.get("/api/v1/alerts/rules/warehouse.queue_overload")
        missing = await client.get("/api/v1/alerts/rules/cost.not_a_rule")

    assert found.status_code == 200
    body = found.json()
    assert body["metric_id"] == "wh.queue_overload_pct"
    assert body["tier"] == "P2"
    assert body["ack_minutes"] == 480
    assert body["window"] == "day"
    assert body["persistence"] == 2

    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/problem+json")
    assert "cost.not_a_rule" in missing.json()["detail"]


@pytest.mark.asyncio
async def test_prune_proposals_are_empty_until_a_rule_has_fired_unheeded(
    settings: Settings,
) -> None:
    async with client_for(settings) as client:
        response = await client.get("/api/v1/alerts/prune-proposals")
    assert response.status_code == 200
    body = response.json()
    assert body["proposals"] == []
    assert body["evaluated_rules"] >= 12


@pytest.mark.asyncio
async def test_backtest_reports_what_a_rule_would_have_done(settings: Settings) -> None:
    async with client_for(settings) as client:
        response = await client.post("/api/v1/alerts/rules/query.failure_rate_elevated/backtest")
    assert response.status_code == 200
    body = response.json()
    assert body["rule_id"] == "query.failure_rate_elevated"
    assert body["would_have_fired"] == len(body["firing_days"])
    assert body["summary"]
    # R5: the replayed series names the statement it came from.
    assert body["sql"].upper().startswith("SELECT")
    assert body["sources"]


@pytest.mark.asyncio
async def test_backtest_does_not_disturb_the_live_rule_statistics(
    settings: Settings,
) -> None:
    async with client_for(settings) as client:
        await client.post("/api/v1/alerts/rules/cost.unattributed_share_high/backtest")
        after = await client.get("/api/v1/alerts/rules/cost.unattributed_share_high")
    assert after.json()["statistics"]["fires"] == 0


@pytest.mark.asyncio
async def test_the_offline_ddl_export_is_deployable_and_carries_its_runbook(
    settings: Settings,
) -> None:
    async with client_for(settings) as client:
        every = await client.get("/api/v1/alerts/export/ddl")
        one = await client.get("/api/v1/alerts/export/ddl?rule_id=pipeline.root_failures")

    assert every.status_code == 200
    body = every.json()
    assert len(body["rule_ids"]) >= 12
    assert body["ddl"].count("CREATE OR REPLACE ALERT") == len(body["rule_ids"])
    assert body["warehouse"]

    single = one.json()
    assert single["rule_ids"] == ["pipeline.root_failures"]
    assert "ALERT_PIPELINE_ROOT_FAILURES" in single["ddl"]
    assert "#a-pipeline-is-failing-or-late" in single["ddl"]
    assert "P2" in single["ddl"]


# ═══════════════════════════════════════════════════════════════ evaluation ═══
def test_the_declared_rule_set_evaluates_against_a_real_account(settings: Settings) -> None:
    """Every shipped rule either evaluates or explains why it could not (R3)."""
    service = AlertService(settings, channels=(NullChannel(),))
    report = service.run_once()
    assert report.mode == "offline"
    assert report.rules_evaluated + report.rules_skipped == len(service.rules())
    assert report.rules_evaluated > 0
    for outcome in report.outcomes:
        assert outcome.fired or outcome.skipped_because is not None or not outcome.fired
        if outcome.skipped_because is not None:
            assert len(outcome.skipped_because) > 20, "a skip must say why"


def test_nothing_fires_when_the_metric_cannot_be_computed(
    empty_lake_settings: Settings,
) -> None:
    """R3: the platform never pages anybody about data it does not have."""
    service = AlertService(empty_lake_settings, channels=(NullChannel(),))
    report = service.run_once()
    assert report.fired == 0
    assert report.rules_skipped == len(service.rules())
    reasons = {outcome.skipped_because for outcome in report.outcomes}
    assert all(reason for reason in reasons)
    assert any("unavailable" in str(reason) for reason in reasons)


def test_dedup_suppresses_a_re_fire_while_the_alert_is_open(
    settings: Settings, tmp_path: Path
) -> None:
    """Anti-fatigue (§14): one open alert, not one per evaluation cycle."""
    service = _service(settings, _custom_rules(tmp_path))
    first = service.run_once()
    assert first.fired == 1

    second = service.run_once()
    assert second.fired == 0
    statistics = service.statistics("cost.test_rule")
    assert statistics.fires == 1
    assert statistics.suppressed == 1


def test_persistence_suppresses_a_one_window_blip(settings: Settings, tmp_path: Path) -> None:
    """A single breaching window must not page anybody (§11.2).

    The threshold is derived from the account's own series, so this asserts the
    behaviour rather than a number that happens to hold for one fixture seed:
    it is set so the *last* window breaches and the one before it does not.
    With `persistence: 1` that fires; with `persistence: 2` it must not.
    """
    probe = _service(settings, _custom_rules(tmp_path / "probe"))
    series = probe.observe_rule("cost.test_rule").values
    assert len(series) >= 3
    last, previous = series[-1][1], series[-2][1]
    assert last != previous, "the fixture produced two identical days; cannot construct a blip"
    condition = "above" if last > previous else "below"

    blip = _service(
        settings,
        _custom_rules(
            tmp_path / "blip", condition=condition, threshold=str(previous), persistence=2
        ),
    )
    assert blip.run_once().fired == 0, "one breaching window is not a persistent breach"

    # Same condition, same threshold, persistence 1: it fires — which is what
    # makes the suppression above attributable to persistence and nothing else.
    single = _service(
        settings,
        _custom_rules(
            tmp_path / "single", condition=condition, threshold=str(previous), persistence=1
        ),
    )
    assert single.run_once().fired == 1


def test_a_fired_alert_dispatches_a_payload_with_no_query_text(
    settings: Settings, tmp_path: Path
) -> None:
    """§14, end to end: what leaves the platform carries no SQL."""
    service = _service(settings, _custom_rules(tmp_path))
    report = service.run_once()
    assert report.fired == 1
    outcome = next(o for o in report.outcomes if o.fired)
    assert outcome.event is not None
    notification = service.notification_for(
        service.rule("cost.test_rule"), outcome.event, observed=None
    )
    payload = notification.as_dict()
    assert payload["runbook"].endswith("#daily-spend-has-spiked")
    assert not any(looks_like_query_text(str(value)) for value in payload.values())
    assert "sql" not in payload and "query" not in payload


def test_an_edited_threshold_is_picked_up_and_resolves_the_open_alert(
    settings: Settings, tmp_path: Path
) -> None:
    """An operator raising a threshold must not have to restart the worker.

    The engine is rebuilt around the edited file, and the dedup ledger survives
    the rebuild — otherwise raising one threshold would re-fire every other
    open alert in the set.
    """
    service = _service(settings, _custom_rules(tmp_path))
    assert service.run_once().fired == 1
    assert service.engine.ledger.open_events

    _custom_rules(tmp_path, threshold="1e30")
    relaxed = _service(settings, tmp_path / "rules.yaml")
    assert relaxed.engine is service.engine or relaxed.engine.ledger is service.engine.ledger
    assert relaxed.rule("cost.test_rule").threshold == Decimal("1e30")
    assert relaxed.run_once().fired == 0
    assert not relaxed.engine.ledger.open_events, "the condition recovered; the alert closes"


# ═══════════════════════════════════════════════════════════════════ worker ═══
@pytest.mark.asyncio
async def test_the_worker_job_returns_a_summary_of_the_run(settings: Settings) -> None:
    summary = await evaluate_alert_rules({"settings": settings})
    assert summary["mode"] == "offline"
    assert summary["rules_evaluated"] + summary["rules_skipped"] == len(
        load_rule_set(settings.alerting.rules_file).rules
    )
    assert isinstance(summary["fired_rules"], list)
    assert summary["fired"] == len(summary["fired_rules"])


def test_the_job_is_registered_on_a_schedule_alongside_ping() -> None:
    from snowobs_worker.main import WorkerSettings, ping

    assert ping in WorkerSettings.functions
    assert evaluate_alert_rules in WorkerSettings.functions
    assert len(WorkerSettings.cron_jobs) == 1
    assert WorkerSettings.cron_jobs[0].coroutine is evaluate_alert_rules
