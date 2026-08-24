"""Notification channels (§14): what goes out, and what must never go out.

The load-bearing test here is
:func:`test_a_payload_carrying_query_text_cannot_be_constructed`. Everything
else checks a channel is shaped correctly; that one checks the platform cannot
leak a customer's SQL into a chat workspace, which is the failure §14's "never
query text" clause exists to prevent.
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest

from snowobs_analytics.alerting import AlertEngine, AlertRule, AlertTier, Condition
from snowobs_analytics.channels import (
    AlertNotification,
    ChannelError,
    EmailChannel,
    NullChannel,
    QueryTextInPayloadError,
    UrllibWebhookTransport,
    WebhookChannel,
    build_channels,
    dispatch,
    looks_like_query_text,
)
from snowobs_analytics.rules import ChannelSpec
from snowobs_common.config import Settings
from snowobs_common.secrets import (
    EnvSecretResolver,
    FileSecretResolver,
    NullSecretResolver,
    SecretNotFoundError,
    build_resolver,
)

FIRED_AT = datetime(2026, 8, 20, 6, 30, tzinfo=UTC)


@dataclass
class RecordingWebhook:
    """A webhook transport that records instead of posting."""

    status: int = 200
    calls: list[tuple[str, bytes]] = field(default_factory=list)

    def post(self, url: str, body: bytes, headers: dict[str, str]) -> int:
        del headers
        self.calls.append((url, body))
        return self.status


@dataclass
class RecordingSmtp:
    messages: list[EmailMessage] = field(default_factory=list)

    def send(self, message: EmailMessage) -> None:
        self.messages.append(message)


@dataclass
class FailingSmtp:
    def send(self, message: EmailMessage) -> None:
        del message
        raise smtplib.SMTPServerDisconnected("relay went away")


def _notification(**overrides: Any) -> AlertNotification:
    base: dict[str, Any] = {
        "rule_id": "warehouse.queue_overload",
        "rule_name": "Warehouse queueing on overload",
        "kpi_name": "Queue overload share",
        "metric_id": "wh.queue_overload_pct",
        "tier": AlertTier.P2,
        "value": Decimal("0.22"),
        "threshold": Decimal("0.15"),
        "scope": {"warehouse": "WH_BI"},
        "runbook_url": "/docs/RUNBOOK.md#a-warehouse-is-queueing",
        "fired_at": FIRED_AT,
        "condition": "above 0.15 over 2 × day",
    }
    base.update(overrides)
    return AlertNotification(**base)


def _resolver(**secrets: str) -> EnvSecretResolver:
    return EnvSecretResolver(secrets)


# ══════════════════════════════════════════════════════ never any query text ══
def test_a_payload_carrying_query_text_cannot_be_constructed() -> None:
    """§14: outbound payloads carry the KPI, value, threshold, scope, runbook.

    Not the SQL. Enforced at construction so a future change that widens the
    payload fails here rather than in a customer's Slack workspace.
    """
    with pytest.raises(QueryTextInPayloadError, match="query text"):
        _notification(rule_name="SELECT SUM(CREDITS_COMPUTE) FROM FACT_WAREHOUSE")
    with pytest.raises(QueryTextInPayloadError):
        _notification(kpi_name="WITH base AS (SELECT 1)")
    with pytest.raises(QueryTextInPayloadError):
        _notification(condition="reads QUERY_TEXT for the offending fingerprint")


def test_a_real_payload_never_contains_query_text_on_any_channel() -> None:
    notification = _notification()
    resolver = _resolver(HOOK="https://hooks.example.invalid/abc")
    slack = WebhookChannel(
        channel_name="chat",
        flavour="slack",
        url_secret_ref="HOOK",
        resolver=resolver,
        transport=RecordingWebhook(),
    )
    teams = WebhookChannel(
        channel_name="teams",
        flavour="teams",
        url_secret_ref="HOOK",
        resolver=resolver,
        transport=RecordingWebhook(),
    )
    email = EmailChannel(
        channel_name="mail",
        sender="a@b.invalid",
        recipients=("c@d.invalid",),
        smtp_host="localhost",
        transport=RecordingSmtp(),
    )
    rendered = [str(slack.body(notification)), str(teams.body(notification))]
    rendered.append(email.message(notification).get_content())
    for document in rendered:
        assert not looks_like_query_text(document), document


def test_the_detector_does_not_fire_on_ordinary_english() -> None:
    """A rule legitimately named "failed logins from one IP" must still send."""
    assert not looks_like_query_text("Failed logins from one IP address")
    assert not looks_like_query_text("Credits burned with no queries")
    assert not looks_like_query_text("Select the runbook link to see what to do")
    assert looks_like_query_text("select credits from warehouse_metering_history")


# ═══════════════════════════════════════════════════════ the payload itself ═══
def test_every_payload_carries_the_five_required_fields() -> None:
    payload = _notification().as_dict()
    assert payload["kpi"] == "Queue overload share"
    assert payload["value"] == "0.22"
    assert payload["threshold"] == "0.15"
    assert payload["scope"] == "warehouse=WH_BI"
    assert payload["runbook"].endswith("#a-warehouse-is-queueing")


def test_an_account_wide_alert_says_so_rather_than_showing_an_empty_scope() -> None:
    assert _notification(scope={}).as_dict()["scope"] == "account-wide"


def test_a_scored_alert_carries_the_kpi_value_as_well_as_the_score() -> None:
    """An anomaly rule compares a z-score; the recipient still wants the credits."""
    payload = _notification(
        metric_id="cost.billed_credits",
        kpi_name="Billed credits",
        value=Decimal("4.2"),
        threshold=Decimal("3.5"),
        observed_value=Decimal("1840.5"),
        unit="credits",
    ).as_dict()
    assert payload["value"] == "4.2"
    assert payload["observed"] == "1840.5 credits"


def test_a_notification_is_built_from_the_engine_event() -> None:
    rule = AlertRule(
        id="cost.spike",
        name="Daily spend spike",
        metric_id="cost.billed_credits",
        condition=Condition.ABOVE,
        threshold=Decimal("500"),
        tier=AlertTier.P1,
        runbook_url="/docs/RUNBOOK.md#daily-spend-has-spiked",
        scope={"service_type": "WAREHOUSE_METERING"},
    )
    event = AlertEngine([rule]).evaluate(
        "cost.spike", Decimal("900"), scope=rule.scope, now=FIRED_AT
    )
    assert event is not None
    notification = AlertNotification.from_event(event, kpi_name="Billed credits", unit="credits")
    assert notification.tier is AlertTier.P1
    assert notification.as_dict()["scope"] == "service_type=WAREHOUSE_METERING"
    assert "Daily spend spike" in notification.headline()


# ═════════════════════════════════════════════════════════════════ webhook ════
def test_slack_body_is_a_block_message_carrying_the_runbook_link() -> None:
    transport = RecordingWebhook()
    channel = WebhookChannel(
        channel_name="chat",
        flavour="slack",
        url_secret_ref="HOOK",
        resolver=_resolver(HOOK="https://hooks.example.invalid/abc"),
        transport=transport,
    )
    result = channel.send(_notification())
    assert result.delivered
    body = channel.body(_notification())
    assert body["text"] == "[P2] Warehouse queueing on overload — Queue overload share"
    assert any("Runbook" in str(block) for block in body["blocks"])  # type: ignore[union-attr]
    posted_url, posted_body = transport.calls[0]
    assert posted_url == "https://hooks.example.invalid/abc"
    assert b"a-warehouse-is-queueing" in posted_body


def test_teams_body_is_a_message_card_with_facts_and_an_action() -> None:
    channel = WebhookChannel(
        channel_name="teams",
        flavour="teams",
        url_secret_ref="HOOK",
        resolver=_resolver(HOOK="https://outlook.example.invalid/x"),
        transport=RecordingWebhook(),
    )
    body = channel.body(_notification())
    assert body["@type"] == "MessageCard"
    facts = body["sections"][0]["facts"]  # type: ignore[index]
    assert {fact["name"] for fact in facts} >= {"KPI", "Value", "Threshold", "Scope"}
    action = body["potentialAction"][0]  # type: ignore[index]
    assert action["targets"][0]["uri"].endswith("#a-warehouse-is-queueing")


def test_an_unresolvable_webhook_secret_degrades_rather_than_raising() -> None:
    """R3 applied to dispatch: one broken channel must not stop the run."""
    channel = WebhookChannel(
        channel_name="chat",
        flavour="slack",
        url_secret_ref="MISSING",
        resolver=_resolver(),
        transport=RecordingWebhook(),
    )
    result = channel.send(_notification())
    assert not result.delivered
    assert "secret" in result.detail


def test_a_non_2xx_webhook_response_is_reported_as_undelivered() -> None:
    channel = WebhookChannel(
        channel_name="chat",
        flavour="slack",
        url_secret_ref="HOOK",
        resolver=_resolver(HOOK="https://hooks.example.invalid/abc"),
        transport=RecordingWebhook(status=404),
    )
    result = channel.send(_notification())
    assert not result.delivered
    assert "404" in result.detail


def test_a_plaintext_webhook_url_is_refused() -> None:
    """Alert payloads name teams, warehouses, and spend. Not over http."""
    with pytest.raises(ChannelError, match="https"):
        UrllibWebhookTransport().post("http://hooks.example.invalid/abc", b"{}", {})


# ═══════════════════════════════════════════════════════════════════ email ════
def test_email_carries_the_facts_and_the_acknowledgement_expectation() -> None:
    transport = RecordingSmtp()
    channel = EmailChannel(
        channel_name="oncall",
        sender="alerts@example.invalid",
        recipients=("oncall@example.invalid", "lead@example.invalid"),
        smtp_host="localhost",
        transport=transport,
    )
    result = channel.send(_notification(tier=AlertTier.P1))
    assert result.delivered
    message = transport.messages[0]
    assert message["Subject"].startswith("[P1]")
    assert message["X-Snowobs-Rule"] == "warehouse.queue_overload"
    body = message.get_content()
    assert "Acknowledge within 15 minutes" in body
    assert "a-warehouse-is-queueing" in body


def test_an_informational_email_asks_for_no_acknowledgement() -> None:
    transport = RecordingSmtp()
    channel = EmailChannel(
        channel_name="digest",
        sender="alerts@example.invalid",
        recipients=("finops@example.invalid",),
        smtp_host="localhost",
        transport=transport,
    )
    channel.send(_notification(tier=AlertTier.P4))
    assert "no acknowledgement expected" in transport.messages[0].get_content()


def test_an_smtp_failure_is_reported_rather_than_raised() -> None:
    channel = EmailChannel(
        channel_name="oncall",
        sender="a@b.invalid",
        recipients=("c@d.invalid",),
        smtp_host="localhost",
        transport=FailingSmtp(),
    )
    result = channel.send(_notification())
    assert not result.delivered
    assert "SMTP send failed" in result.detail


def test_an_email_channel_without_a_resolver_for_its_password_says_so() -> None:
    channel = EmailChannel(
        channel_name="oncall",
        sender="a@b.invalid",
        recipients=("c@d.invalid",),
        smtp_host="localhost",
        smtp_password_secret_ref="env://SMTP_PASSWORD",
    )
    result = channel.send(_notification())
    assert not result.delivered
    assert "credential" in result.detail


# ═════════════════════════════════════════════════════════ null and routing ═══
def test_the_null_channel_records_the_firing_and_reports_it_as_undelivered() -> None:
    result = NullChannel().send(_notification())
    assert not result.delivered
    assert result.channel == "null"


def test_dispatch_only_reaches_channels_that_accept_the_tier() -> None:
    paging = EmailChannel(
        channel_name="oncall",
        sender="a@b.invalid",
        recipients=("c@d.invalid",),
        smtp_host="localhost",
        tiers=(AlertTier.P1, AlertTier.P2),
        transport=RecordingSmtp(),
    )
    digest = EmailChannel(
        channel_name="digest",
        sender="a@b.invalid",
        recipients=("e@f.invalid",),
        smtp_host="localhost",
        tiers=(AlertTier.P4,),
        transport=RecordingSmtp(),
    )
    delivered = dispatch(_notification(tier=AlertTier.P2), [paging, digest])
    assert [d.channel for d in delivered] == ["oncall"]

    # And a tier nothing accepts still leaves a record rather than vanishing.
    orphan = dispatch(_notification(tier=AlertTier.P3), [paging, digest])
    assert [d.channel for d in orphan] == ["null"]
    assert not orphan[0].delivered


def test_dispatch_is_disabled_until_the_deployment_turns_it_on() -> None:
    spec = ChannelSpec(name="chat", kind="webhook", flavour="slack", url_secret_ref="env://HOOK")
    channels = build_channels([spec], resolver=_resolver(HOOK="https://x.invalid"), enabled=False)
    assert len(channels) == 1
    assert isinstance(channels[0], NullChannel)
    assert not channels[0].send(_notification()).delivered


def test_channels_are_built_from_their_declarations() -> None:
    specs = [
        ChannelSpec(name="chat", kind="webhook", flavour="teams", url_secret_ref="env://HOOK"),
        ChannelSpec(
            name="mail",
            kind="email",
            sender="a@b.invalid",
            recipients=("c@d.invalid",),
            smtp_host="smtp.invalid",
            tiers=(AlertTier.P1,),
        ),
    ]
    built = build_channels(specs, resolver=_resolver(HOOK="https://x.invalid"))
    assert [channel.name for channel in built] == ["chat", "mail"]
    assert isinstance(built[0], WebhookChannel)
    assert built[1].accepts(AlertTier.P1) and not built[1].accepts(AlertTier.P3)


def test_a_webhook_declared_without_a_secrets_adapter_is_refused() -> None:
    spec = ChannelSpec(name="chat", kind="webhook", flavour="slack", url_secret_ref="env://HOOK")
    with pytest.raises(ChannelError, match="secrets adapter"):
        build_channels([spec], resolver=None, enabled=True)


def test_no_declared_channels_means_the_null_channel() -> None:
    built = build_channels([], resolver=None, enabled=True)
    assert isinstance(built[0], NullChannel)


# ═════════════════════════════════════════════════════════ secrets adapter ════
def test_secrets_are_resolved_by_reference_and_the_value_never_appears_in_the_error() -> None:
    resolver = EnvSecretResolver({"HOOK": "https://hooks.example.invalid/s3cr3t"})
    assert resolver.resolve("env://HOOK").endswith("s3cr3t")
    with pytest.raises(SecretNotFoundError) as caught:
        resolver.resolve("env://ABSENT")
    assert "s3cr3t" not in str(caught.value)


def test_a_reference_naming_another_provider_is_refused() -> None:
    with pytest.raises(SecretNotFoundError, match="aws"):
        EnvSecretResolver({}).resolve("aws://snowobs/hook")


def test_the_file_provider_reads_a_json_object(tmp_path: Path) -> None:
    store = tmp_path / "secrets.json"
    store.write_text('{"webhook": "https://hooks.example.invalid/x"}', encoding="utf-8")
    resolver = FileSecretResolver(store)
    assert resolver.resolve("file://webhook").startswith("https://")
    with pytest.raises(SecretNotFoundError, match="absent"):
        resolver.resolve("file://missing")


def test_the_null_resolver_explains_itself() -> None:
    with pytest.raises(SecretNotFoundError, match="SECRETS__PROVIDER"):
        NullSecretResolver().resolve("env://HOOK")


def test_build_resolver_follows_the_configured_provider(tmp_path: Path) -> None:
    store = tmp_path / "secrets.json"
    store.write_text('{"k": "v"}', encoding="utf-8")
    settings = Settings(_env_file=None, secrets={"provider": "file", "file_path": str(store)})
    assert build_resolver(settings).resolve("file://k") == "v"

    env_settings = Settings(_env_file=None, secrets={"provider": "env"})
    assert build_resolver(env_settings, environ={"K": "v"}).resolve("env://K") == "v"
