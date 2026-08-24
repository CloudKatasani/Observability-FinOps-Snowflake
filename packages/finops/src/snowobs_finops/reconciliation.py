"""The reconciliation gate (BUILD_PROMPT §10.3, R6).

Allocated cost reconciles to the metered bill, or chargeback figures do not
publish. A failed gate surfaces as a banner and a P2 alert with the variance
decomposed by warehouse-day — never as a quietly wrong dashboard.

Every run is stored with its inputs and its outcome. That record is the artifact
finance asks for when they question a chargeback line, so it is written whether
the gate passed or failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

from snowobs_finops.allocation import AllocationResult

DEFAULT_TOLERANCE_PCT = Decimal("0.5")


class GateOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    #: Nothing to reconcile — reported honestly rather than as a pass.
    NO_DATA = "no_data"


@dataclass(frozen=True)
class DayVariance:
    """One day's contribution to the variance, for the decomposition banner."""

    usage_day: date
    allocated_credits: Decimal
    metered_credits: Decimal

    @property
    def variance_credits(self) -> Decimal:
        return self.allocated_credits - self.metered_credits

    @property
    def variance_pct(self) -> Decimal | None:
        if self.metered_credits == 0:
            return None  # an unknown ratio stays unknown (R3)
        return self.variance_credits / self.metered_credits * 100


@dataclass
class ReconciliationRun:
    """A stored reconciliation, pass or fail."""

    period_start: date
    period_end: date
    allocated_credits: Decimal
    metered_credits: Decimal
    tolerance_pct: Decimal
    outcome: GateOutcome
    ran_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    #: Days ranked by absolute variance — the "which warehouse-days drift" view.
    worst_days: list[DayVariance] = field(default_factory=list)
    note: str = ""

    @property
    def variance_credits(self) -> Decimal:
        return self.allocated_credits - self.metered_credits

    @property
    def variance_pct(self) -> Decimal | None:
        if self.metered_credits == 0:
            return None
        return self.variance_credits / self.metered_credits * 100

    @property
    def publication_allowed(self) -> bool:
        """R6: chargeback figures publish only behind a green gate."""
        return self.outcome is GateOutcome.PASSED

    def banner(self) -> str:
        """The message shown to the user. States the number, never hides it."""
        if self.outcome is GateOutcome.NO_DATA:
            return (
                "Reconciliation could not run: no metered credits for "
                f"{self.period_start}–{self.period_end}."
            )
        variance = self.variance_pct
        if self.outcome is GateOutcome.PASSED:
            return (
                f"Reconciled: allocated {self.allocated_credits:.2f} credits vs metered "
                f"{self.metered_credits:.2f} ({variance:+.3f}%), within ±{self.tolerance_pct}%."
            )
        worst = ", ".join(
            f"{day.usage_day} ({day.variance_credits:+.2f})" for day in self.worst_days[:3]
        )
        return (
            f"Chargeback blocked: allocated {self.allocated_credits:.2f} credits vs metered "
            f"{self.metered_credits:.2f} ({variance:+.3f}%), outside ±{self.tolerance_pct}%. "
            f"Largest daily variances: {worst}."
        )


def reconcile(
    allocation: AllocationResult,
    metered_by_day: dict[date, Decimal],
    *,
    tolerance_pct: Decimal = DEFAULT_TOLERANCE_PCT,
    period_start: date | None = None,
    period_end: date | None = None,
) -> ReconciliationRun:
    """Compare allocated cost to the metered bill for a period."""
    allocated_by_day: dict[date, Decimal] = {}
    for entry in allocation.allocations:
        allocated_by_day[entry.usage_day] = (
            allocated_by_day.get(entry.usage_day, Decimal(0)) + entry.total_credits
        )

    days = sorted(set(allocated_by_day) | set(metered_by_day))
    start = period_start or (days[0] if days else date.today())  # noqa: DTZ011
    end = period_end or (days[-1] if days else start)

    allocated_total = sum(allocated_by_day.values(), Decimal(0))
    metered_total = sum(metered_by_day.values(), Decimal(0))

    variances = [
        DayVariance(
            usage_day=day,
            allocated_credits=allocated_by_day.get(day, Decimal(0)),
            metered_credits=metered_by_day.get(day, Decimal(0)),
        )
        for day in days
    ]
    worst = sorted(variances, key=lambda v: abs(v.variance_credits), reverse=True)

    if metered_total == 0:
        outcome = GateOutcome.NO_DATA
    else:
        variance_pct = abs(allocated_total - metered_total) / metered_total * 100
        outcome = GateOutcome.PASSED if variance_pct <= tolerance_pct else GateOutcome.FAILED

    return ReconciliationRun(
        period_start=start,
        period_end=end,
        allocated_credits=allocated_total,
        metered_credits=metered_total,
        tolerance_pct=tolerance_pct,
        outcome=outcome,
        worst_days=worst,
    )
