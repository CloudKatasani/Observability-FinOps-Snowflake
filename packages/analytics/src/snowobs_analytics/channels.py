"""Notification channels (BUILD_PROMPT §14).

An alert that reaches nobody is the same defect as an alert nobody can action.
This module is the outbound half: an adapter protocol, a webhook channel shaped
for Teams and Slack, an SMTP channel (Amazon SES is configured as the relay in
AWS deployments), and a null channel for the very common case where a
deployment has configured nothing yet.

Two properties are enforced in code rather than left to reviewers:

**No query text, ever.** §14 says outbound payloads carry the KPI name, the
value, the threshold, the scope, and the runbook link — and nothing else.
:class:`AlertNotification` refuses to construct if any of its fields looks like
SQL, so a future change that widens the payload fails at the payload rather
than at a customer's Slack workspace. The compiled SQL stays behind the API,
where "show the SQL" is authenticated (R5).

**No secrets in the clear.** A webhook URL and an SMTP password are secrets
under §17. Channels hold *references*, resolved through the secrets adapter at
the moment of dispatch, and neither the reference's value nor the URL ever
reaches a log line — :meth:`DeliveryResult.detail` is written for an operator
reading structured logs, so it names the channel and never its endpoint.
"""

from __future__ import annotations

import json
import re
import smtplib
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from email.message import EmailMessage
from typing import Protocol, runtime_checkable

from snowobs_analytics.alerting import AlertEvent, AlertTier
from snowobs_common.errors import AppError
from snowobs_common.logging import get_logger
from snowobs_common.secrets import SecretNotFoundError, SecretResolver

logger = get_logger(__name__)

#: Seconds a single outbound delivery may take before it is abandoned. An
#: alerting path that blocks is worse than one that drops: the next rule in the
#: run never gets evaluated.
DELIVERY_TIMEOUT_S = 10

#: Shapes that mean "somebody put a query in the payload". Deliberately shape-
#: based rather than keyword-based: a rule legitimately named "Failed logins
#: from one IP" contains the word `from`, and a detector that fired on that
#: would be turned off within a week.
_QUERY_SHAPES: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?is)\bselect\b.{0,4000}?\bfrom\b"),
    re.compile(r"(?is)\bwith\b\s+\w+\s+as\s*\("),
    re.compile(r"(?is)\b(insert\s+into|delete\s+from|merge\s+into|truncate\s+table)\b"),
    re.compile(r"(?is)\b(create|alter|drop)\s+(or\s+replace\s+)?(table|view|schema|warehouse)\b"),
    re.compile(r"(?i)\bquery_text\b"),
)


class ChannelError(AppError):
    """A channel could not be built from its declaration."""

    status_code = 500
    title = "Notification channel misconfigured"
    problem_type = "https://snowobs.dev/problems/notification-channel"


class QueryTextInPayloadError(AppError):
    """A payload contained query text. §14 forbids it; this stops it leaving."""

    status_code = 500
    title = "Alert payload contained query text"
    problem_type = "https://snowobs.dev/problems/alert-payload"


def looks_like_query_text(value: str) -> bool:
    """Does this string contain a SQL statement?"""
    return any(shape.search(value) for shape in _QUERY_SHAPES)


def assert_no_query_text(payload: Mapping[str, str]) -> None:
    """Refuse a payload carrying SQL (§14). Raises, never trims."""
    for key, value in payload.items():
        if looks_like_query_text(str(value)):
            raise QueryTextInPayloadError(
                f"Alert payload field '{key}' contains query text. Outbound alerts "
                "carry the KPI, value, threshold, scope, and runbook link — never "
                "the SQL behind them (§14). Show the SQL in the app, not in chat."
            )


@dataclass(frozen=True)
class AlertNotification:
    """What goes out. Every field is checked before it can be constructed."""

    rule_id: str
    rule_name: str
    #: The human-readable KPI name, resolved from the semantic layer.
    kpi_name: str
    metric_id: str
    tier: AlertTier
    #: The figure the condition compared. For an anomaly rule this is the
    #: robust z-score, which is what the threshold is expressed in.
    value: Decimal
    threshold: Decimal
    scope: dict[str, str]
    runbook_url: str
    fired_at: datetime
    #: The KPI's own value on the breaching window, when the condition compared
    #: something else (an anomaly score). Carried so the recipient sees the
    #: number they recognise as well as the one that fired.
    observed_value: Decimal | None = None
    unit: str | None = None
    condition: str = ""

    def __post_init__(self) -> None:
        assert_no_query_text(self.as_dict())

    @classmethod
    def from_event(
        cls,
        event: AlertEvent,
        *,
        kpi_name: str,
        unit: str | None = None,
        condition: str = "",
        observed_value: Decimal | None = None,
    ) -> AlertNotification:
        return cls(
            rule_id=event.rule_id,
            rule_name=event.rule_name,
            kpi_name=kpi_name,
            metric_id=event.metric_id,
            tier=event.tier,
            value=event.value,
            threshold=event.threshold,
            scope=dict(event.scope),
            runbook_url=event.runbook_url,
            fired_at=event.fired_at,
            observed_value=observed_value,
            unit=unit,
            condition=condition,
        )

    @property
    def scope_text(self) -> str:
        return ", ".join(f"{k}={v}" for k, v in sorted(self.scope.items())) or "account-wide"

    def headline(self) -> str:
        return f"[{self.tier.value}] {self.rule_name} — {self.kpi_name}"

    def as_dict(self) -> dict[str, str]:
        """The flat payload. Same fields on every channel, so they agree."""
        payload = {
            "tier": self.tier.value,
            "rule_id": self.rule_id,
            "rule": self.rule_name,
            "kpi": self.kpi_name,
            "metric": self.metric_id,
            "condition": self.condition,
            "value": _plain(self.value, self.unit if self.observed_value is None else None),
            "threshold": _plain(self.threshold, None),
            "scope": self.scope_text,
            "runbook": self.runbook_url,
            "fired_at": self.fired_at.isoformat(),
        }
        if self.observed_value is not None:
            payload["observed"] = _plain(self.observed_value, self.unit)
        return payload

    def facts(self) -> list[tuple[str, str]]:
        """Label/value pairs, in the order a human reads them."""
        payload = self.as_dict()
        ordered = [
            ("KPI", payload["kpi"]),
            ("Value", payload["value"]),
            ("Threshold", payload["threshold"]),
            ("Scope", payload["scope"]),
        ]
        if "observed" in payload:
            ordered.insert(2, ("Observed", payload["observed"]))
        ordered += [("Fired at", payload["fired_at"]), ("Runbook", payload["runbook"])]
        return ordered


def _plain(value: Decimal, unit: str | None) -> str:
    """A Decimal rendered as text. Never a float, at any point in the path."""
    text = format(value.normalize(), "f") if value == value.to_integral_value() else str(value)
    return f"{text} {unit}" if unit else text


@dataclass(frozen=True)
class DeliveryResult:
    """One channel's outcome for one notification. Safe to log verbatim."""

    channel: str
    delivered: bool
    detail: str


@runtime_checkable
class NotificationChannel(Protocol):
    """What every outbound adapter implements."""

    @property
    def name(self) -> str: ...

    def accepts(self, tier: AlertTier) -> bool: ...

    def send(self, notification: AlertNotification) -> DeliveryResult: ...


# ------------------------------------------------------------------ webhook --
@runtime_checkable
class WebhookTransport(Protocol):
    """The HTTP POST a webhook channel makes. Injected so the I/O is testable."""

    def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> int: ...


@dataclass(frozen=True)
class UrllibWebhookTransport:
    """stdlib HTTP POST. Refuses anything that is not HTTPS."""

    timeout_s: int = DELIVERY_TIMEOUT_S

    def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> int:
        if not url.startswith("https://"):
            raise ChannelError(
                "A webhook URL must be https. Alert payloads name teams, warehouses, "
                "and spend; they do not travel in the clear."
            )
        # Bandit's S310 exists to catch an attacker-controlled scheme reaching
        # urlopen; the scheme is pinned to https three lines above, and the
        # URL itself comes from the secrets adapter, not from a request.
        request = urllib.request.Request(  # noqa: S310
            url, data=body, headers=dict(headers), method="POST"
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:  # noqa: S310
            status: int = response.status
            return status


@dataclass(frozen=True)
class WebhookChannel:
    """Teams- or Slack-shaped JSON posted to an incoming webhook."""

    channel_name: str
    flavour: str
    url_secret_ref: str
    resolver: SecretResolver
    tiers: tuple[AlertTier, ...] = ()
    transport: WebhookTransport = field(default_factory=UrllibWebhookTransport)

    @property
    def name(self) -> str:
        return self.channel_name

    def accepts(self, tier: AlertTier) -> bool:
        return not self.tiers or tier in self.tiers

    def body(self, notification: AlertNotification) -> dict[str, object]:
        """The provider-shaped document. Both carry the same facts."""
        assert_no_query_text(notification.as_dict())
        if self.flavour == "slack":
            return self._slack(notification)
        return self._teams(notification)

    def _slack(self, notification: AlertNotification) -> dict[str, object]:
        fields = [
            {"type": "mrkdwn", "text": f"*{label}*\n{value}"}
            for label, value in notification.facts()
            if label != "Runbook"
        ]
        return {
            "text": notification.headline(),
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": notification.headline()},
                },
                {"type": "section", "fields": fields},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"<{notification.runbook_url}|Runbook: what to do about this>",
                    },
                },
            ],
        }

    def _teams(self, notification: AlertNotification) -> dict[str, object]:
        return {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "themeColor": _TIER_COLOURS[notification.tier],
            "summary": notification.headline(),
            "title": notification.headline(),
            "sections": [
                {
                    "facts": [
                        {"name": label, "value": value}
                        for label, value in notification.facts()
                        if label != "Runbook"
                    ],
                    "markdown": False,
                }
            ],
            "potentialAction": [
                {
                    "@type": "OpenUri",
                    "name": "Open the runbook",
                    "targets": [{"os": "default", "uri": notification.runbook_url}],
                }
            ],
        }

    def send(self, notification: AlertNotification) -> DeliveryResult:
        try:
            url = self.resolver.resolve(self.url_secret_ref)
        except SecretNotFoundError as exc:
            # The reference is safe to log; its value never is.
            logger.warning(
                "alert_channel_secret_unresolved",
                channel=self.name,
                secret_ref=self.url_secret_ref,
                reason=exc.detail,
            )
            return DeliveryResult(self.name, False, "webhook URL secret could not be resolved")

        payload = json.dumps(self.body(notification)).encode("utf-8")
        try:
            status = self.transport.post(
                url, payload, {"Content-Type": "application/json; charset=utf-8"}
            )
        except (urllib.error.URLError, OSError, ChannelError) as exc:
            logger.warning(
                "alert_delivery_failed",
                channel=self.name,
                rule_id=notification.rule_id,
                reason=type(exc).__name__,
            )
            return DeliveryResult(self.name, False, f"webhook POST failed: {type(exc).__name__}")

        delivered = 200 <= status < 300
        logger.info(
            "alert_delivered" if delivered else "alert_delivery_rejected",
            channel=self.name,
            rule_id=notification.rule_id,
            tier=notification.tier.value,
            status=status,
        )
        return DeliveryResult(self.name, delivered, f"webhook responded {status}")


_TIER_COLOURS: dict[AlertTier, str] = {
    AlertTier.P1: "D93025",
    AlertTier.P2: "E8710A",
    AlertTier.P3: "1A73E8",
    AlertTier.P4: "5F6368",
}


# -------------------------------------------------------------------- email --
@runtime_checkable
class EmailTransport(Protocol):
    """The SMTP send an email channel makes. Injected so the I/O is testable."""

    def send(self, message: EmailMessage) -> None: ...


@dataclass(frozen=True)
class SmtplibTransport:
    """stdlib SMTP with STARTTLS. Amazon SES is an SMTP relay like any other."""

    host: str
    port: int = 587
    username: str | None = None
    password: str | None = None
    starttls: bool = True
    timeout_s: int = DELIVERY_TIMEOUT_S

    def send(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout_s) as smtp:
            if self.starttls:
                smtp.starttls()
            if self.username and self.password:
                smtp.login(self.username, self.password)
            smtp.send_message(message)


@dataclass(frozen=True)
class EmailChannel:
    """Plain-text email. The body is the same facts every other channel sends."""

    channel_name: str
    sender: str
    recipients: tuple[str, ...]
    smtp_host: str
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password_secret_ref: str | None = None
    starttls: bool = True
    tiers: tuple[AlertTier, ...] = ()
    resolver: SecretResolver | None = None
    #: Injected in tests and by deployments that relay through their own agent.
    transport: EmailTransport | None = None

    @property
    def name(self) -> str:
        return self.channel_name

    def accepts(self, tier: AlertTier) -> bool:
        return not self.tiers or tier in self.tiers

    def message(self, notification: AlertNotification) -> EmailMessage:
        assert_no_query_text(notification.as_dict())
        message = EmailMessage()
        message["Subject"] = notification.headline()
        message["From"] = self.sender
        message["To"] = ", ".join(self.recipients)
        # Lets a mail client thread re-fires of the same rule and scope.
        message["X-Snowobs-Rule"] = notification.rule_id
        message["X-Snowobs-Tier"] = notification.tier.value
        body = "\n".join(f"{label + ':':<12} {value}" for label, value in notification.facts())
        ack = notification.tier.ack_minutes
        expectation = (
            f"\nAcknowledge within {ack} minutes.\n"
            if ack is not None
            else "\nInformational — no acknowledgement expected.\n"
        )
        message.set_content(f"{notification.headline()}\n\n{body}\n{expectation}")
        return message

    def _transport(self) -> EmailTransport:
        if self.transport is not None:
            return self.transport
        password: str | None = None
        if self.smtp_password_secret_ref:
            if self.resolver is None:
                raise ChannelError(
                    f"Email channel '{self.name}' references an SMTP password but no "
                    "secrets adapter is configured to resolve it (SECRETS__PROVIDER)."
                )
            password = self.resolver.resolve(self.smtp_password_secret_ref)
        return SmtplibTransport(
            host=self.smtp_host,
            port=self.smtp_port,
            username=self.smtp_username,
            password=password,
            starttls=self.starttls,
        )

    def send(self, notification: AlertNotification) -> DeliveryResult:
        try:
            transport = self._transport()
        except (SecretNotFoundError, ChannelError) as exc:
            logger.warning(
                "alert_channel_secret_unresolved",
                channel=self.name,
                secret_ref=self.smtp_password_secret_ref,
                reason=exc.detail,
            )
            return DeliveryResult(self.name, False, "SMTP credential could not be resolved")
        try:
            transport.send(self.message(notification))
        except (smtplib.SMTPException, OSError) as exc:
            logger.warning(
                "alert_delivery_failed",
                channel=self.name,
                rule_id=notification.rule_id,
                reason=type(exc).__name__,
            )
            return DeliveryResult(self.name, False, f"SMTP send failed: {type(exc).__name__}")
        logger.info(
            "alert_delivered",
            channel=self.name,
            rule_id=notification.rule_id,
            tier=notification.tier.value,
            recipients=len(self.recipients),
        )
        return DeliveryResult(self.name, True, f"emailed {len(self.recipients)} recipient(s)")


# --------------------------------------------------------------------- null --
@dataclass(frozen=True)
class NullChannel:
    """Used when nothing is configured, or when dispatch is switched off.

    It is not a no-op: it logs the firing in full, so a deployment with no
    webhook still leaves an operator-readable record of what would have been
    sent. R3 applied to notification — degrade, and say what degraded.
    """

    channel_name: str = "null"
    reason: str = "no notification channel is configured"

    @property
    def name(self) -> str:
        return self.channel_name

    def accepts(self, tier: AlertTier) -> bool:
        del tier
        return True

    def send(self, notification: AlertNotification) -> DeliveryResult:
        # The whole payload under one key: it is the record of what would
        # have been sent, and merging it into the log event's own fields would
        # collide with them.
        logger.info(
            "alert_not_dispatched",
            reason=self.reason,
            rule_id=notification.rule_id,
            tier=notification.tier.value,
            payload=notification.as_dict(),
        )
        return DeliveryResult(self.name, False, self.reason)


def dispatch(
    notification: AlertNotification, channels: Sequence[NotificationChannel]
) -> list[DeliveryResult]:
    """Send one notification to every channel that accepts its tier.

    A channel that fails does not stop the others: an SMTP outage must not cost
    the chat message that would have been the actual page.
    """
    accepted = [channel for channel in channels if channel.accepts(notification.tier)]
    if not accepted:
        return [NullChannel(reason="no configured channel accepts this tier").send(notification)]
    return [channel.send(notification) for channel in accepted]


def build_channels(
    specs: Iterable[object],
    *,
    resolver: SecretResolver | None = None,
    enabled: bool = True,
) -> tuple[NotificationChannel, ...]:
    """Build channels from :class:`~snowobs_analytics.rules.ChannelSpec` records.

    ``enabled=False`` — the shipped default — returns a single
    :class:`NullChannel`, so a deployment that has not yet chosen where alerts
    go still evaluates rules and records firings without mailing strangers.
    """
    from snowobs_analytics.rules import ChannelSpec

    if not enabled:
        return (NullChannel(reason="ALERTING__ENABLED is false; dispatch is switched off"),)

    built: list[NotificationChannel] = []
    for spec in specs:
        if not isinstance(spec, ChannelSpec):  # pragma: no cover - defensive
            raise ChannelError(f"Not a channel declaration: {spec!r}")
        if spec.kind == "webhook":
            if resolver is None:
                raise ChannelError(
                    f"Webhook channel '{spec.name}' needs a secrets adapter to resolve "
                    f"{spec.url_secret_ref}; set SECRETS__PROVIDER."
                )
            built.append(
                WebhookChannel(
                    channel_name=spec.name,
                    flavour=str(spec.flavour),
                    url_secret_ref=str(spec.url_secret_ref),
                    resolver=resolver,
                    tiers=spec.tiers,
                )
            )
        else:
            built.append(
                EmailChannel(
                    channel_name=spec.name,
                    sender=str(spec.sender),
                    recipients=spec.recipients,
                    smtp_host=str(spec.smtp_host),
                    smtp_port=spec.smtp_port,
                    smtp_username=spec.smtp_username,
                    smtp_password_secret_ref=spec.smtp_password_secret_ref,
                    starttls=spec.starttls,
                    tiers=spec.tiers,
                    resolver=resolver,
                )
            )
    if not built:
        return (NullChannel(),)
    return tuple(built)


__all__ = [
    "AlertNotification",
    "ChannelError",
    "DeliveryResult",
    "EmailChannel",
    "EmailTransport",
    "NotificationChannel",
    "NullChannel",
    "QueryTextInPayloadError",
    "SmtplibTransport",
    "UrllibWebhookTransport",
    "WebhookChannel",
    "WebhookTransport",
    "assert_no_query_text",
    "build_channels",
    "dispatch",
    "looks_like_query_text",
]
