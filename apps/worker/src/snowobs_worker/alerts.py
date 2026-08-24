"""The scheduled alert evaluation job (BUILD_PROMPT §14).

The job itself is deliberately thin. It loads the declared rule set, hands it
to :class:`~snowobs_api.services.alerts.AlertService`, and returns a summary —
all of the semantics (governed metric queries, persistence, dedup, dispatch)
live in the service, so the worker and the API can never disagree about what a
rule means.

Two things this job must never do:

* **Fire on data it does not have.** A metric whose sources have not landed is
  skipped with a logged reason, never treated as zero (R3). The summary counts
  those separately, so "nothing fired" and "nothing could be evaluated" are
  distinguishable in the job result rather than looking identical.
* **Change anything in Snowflake.** Evaluation is read-only. Guardrail
  management — resource monitors, statement timeouts, auto-suspend policy —
  is approval-gated and lives outside the scheduler entirely (R8, §27.8: a
  production monitor is notify-only, and nothing here suspends a warehouse).
"""

from __future__ import annotations

from typing import Any

from snowobs_api.services.alerts import AlertService
from snowobs_common.config import Settings, load_settings
from snowobs_common.logging import get_logger

logger = get_logger(__name__)


async def evaluate_alert_rules(ctx: dict[str, Any]) -> dict[str, Any]:
    """Evaluate every enabled rule once and dispatch whatever fires.

    Registered on a schedule in :class:`~snowobs_worker.main.WorkerSettings`.
    The dedup ledger and per-rule statistics live in this worker process, so a
    re-fire is suppressed while an alert stays open and a restart starts the
    ledger empty (A-24).
    """
    settings: Settings = ctx.get("settings") or load_settings()
    service = AlertService(settings)
    report = service.run_once()
    summary = report.as_dict()
    logger.info(
        "alert_rules_evaluated",
        mode=report.mode,
        evaluated=report.rules_evaluated,
        skipped=report.rules_skipped,
        fired=report.fired,
        fired_rules=summary["fired_rules"],
    )
    return summary


__all__ = ["evaluate_alert_rules"]
