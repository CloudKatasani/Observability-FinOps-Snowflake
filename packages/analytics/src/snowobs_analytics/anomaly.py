"""Anomaly detection (BUILD_PROMPT §11.2) — thresholds first, models second.

Statistical scoring is applied only where seasonality genuinely defeats a static
threshold: daily spend, and optionally query-fingerprint cost. Everywhere else a
threshold is clearer, cheaper, and easier to defend in a review.

Two properties matter more than sensitivity:

* **Magnitude *and* persistence.** A single noisy point does not fire. This is
  what stops the 03:00 page for a blip that resolved itself.
* **Every anomaly is decomposed.** An alert that says "spend is up 40%" wastes
  an on-call hour; one that says "up 40%, of which 31 points are TEAM_ML on
  WH_DS_TRAINING" is a fix. The decomposition is deterministic — a greedy
  contribution search, not an LLM guess (R12).
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

#: Robust scale estimator: MAD * this ≈ one standard deviation for normal data.
MAD_TO_SIGMA = 1.4826
#: Fire only above this robust z-score...
DEFAULT_Z_THRESHOLD = 3.5
#: ...and only when the deviation is also this large relative to the baseline,
#: so a quiet series with a tiny absolute change does not page anyone.
DEFAULT_MIN_RELATIVE_CHANGE = Decimal("0.25")
#: Days of history required before scoring is meaningful.
MIN_BASELINE_DAYS = 14


class AnomalyDirection(StrEnum):
    SPIKE = "spike"
    DROP = "drop"


@dataclass(frozen=True)
class Point:
    day: date
    value: Decimal


@dataclass(frozen=True)
class Contribution:
    """One dimension combination's share of a delta."""

    dimension: str
    member: str
    delta: Decimal
    share_of_delta: Decimal

    def describe(self) -> str:
        return (
            f"{self.dimension}={self.member} contributes {self.delta:+.1f} "
            f"({self.share_of_delta:.0%} of the change)"
        )


@dataclass
class Anomaly:
    """A detection, with the evidence needed to act on it."""

    day: date
    value: Decimal
    baseline: Decimal
    direction: AnomalyDirection
    z_score: float
    relative_change: Decimal
    #: Ranked, greedy contribution search across the available dimensions.
    contributions: list[Contribution] = field(default_factory=list)
    persistence_days: int = 1

    @property
    def delta(self) -> Decimal:
        return self.value - self.baseline

    def narrative(self) -> str:
        """A deterministic sentence. The LLM may rephrase it; it never computes it."""
        headline = (
            f"{self.day}: {self.direction.value} to {self.value:,.1f} against a baseline of "
            f"{self.baseline:,.1f} ({self.relative_change:+.0%}, robust z={self.z_score:.1f})."
        )
        if not self.contributions:
            return headline
        top = self.contributions[0]
        rest = ""
        if len(self.contributions) > 1:
            rest = " Next: " + "; ".join(c.describe() for c in self.contributions[1:3]) + "."
        return f"{headline} Largest contributor: {top.describe()}.{rest}"


def _robust_scale(values: Sequence[float], centre: float) -> float:
    """Median absolute deviation, scaled. Resistant to the outliers we hunt."""
    deviations = [abs(value - centre) for value in values]
    mad = statistics.median(deviations) if deviations else 0.0
    return mad * MAD_TO_SIGMA


def _deseasonalise(points: Sequence[Point]) -> list[float]:
    """Remove weekday effects so Monday is compared with Mondays.

    Without this, every Monday in a weekday-heavy workload looks anomalous
    against a baseline that includes weekends.
    """
    values = [float(p.value) for p in points]
    by_weekday: dict[int, list[float]] = {}
    for point, value in zip(points, values, strict=True):
        by_weekday.setdefault(point.day.weekday(), []).append(value)

    effects = {
        weekday: statistics.median(group)
        for weekday, group in by_weekday.items()
        if len(group) >= 2
    }
    overall = statistics.median(values) if values else 0.0
    return [
        value - (effects.get(point.day.weekday(), overall) - overall)
        for point, value in zip(points, values, strict=True)
    ]


def detect(
    series: Sequence[Point],
    *,
    z_threshold: float = DEFAULT_Z_THRESHOLD,
    min_relative_change: Decimal = DEFAULT_MIN_RELATIVE_CHANGE,
    require_persistence: int = 1,
) -> list[Anomaly]:
    """Score a daily series. Returns anomalies, ranked by absolute delta.

    ``require_persistence`` days of consecutive deviation are needed before an
    anomaly is reported, so a one-point blip stays quiet (§11.2).
    """
    ordered = sorted(series, key=lambda p: p.day)
    if len(ordered) < MIN_BASELINE_DAYS:
        return []

    adjusted = _deseasonalise(ordered)
    centre = statistics.median(adjusted)
    scale = _robust_scale(adjusted, centre)
    if scale == 0:
        # A perfectly flat series: any change is total, but scoring is undefined.
        # Fall back to a relative test rather than dividing by zero.
        scale = abs(centre) * 0.01 or 1.0

    flagged: list[Anomaly] = []
    streak = 0
    for index, (point, value) in enumerate(zip(ordered, adjusted, strict=True)):
        z = (value - centre) / scale
        baseline = Decimal(str(round(centre, 6)))
        relative = (point.value - baseline) / abs(baseline) if baseline != 0 else Decimal(0)

        if abs(z) >= z_threshold and abs(relative) >= min_relative_change:
            streak += 1
            if streak >= require_persistence:
                flagged.append(
                    Anomaly(
                        day=point.day,
                        value=point.value,
                        baseline=baseline,
                        direction=(AnomalyDirection.SPIKE if z > 0 else AnomalyDirection.DROP),
                        z_score=round(z, 2),
                        relative_change=relative.quantize(Decimal("0.0001")),
                        persistence_days=streak,
                    )
                )
        else:
            streak = 0
        del index

    flagged.sort(key=lambda a: abs(a.delta), reverse=True)
    return flagged


def decompose(
    anomaly: Anomaly,
    breakdown_on_day: Mapping[str, Mapping[str, Decimal]],
    breakdown_baseline: Mapping[str, Mapping[str, Decimal]],
    *,
    top_n: int = 5,
    min_share: Decimal = Decimal("0.05"),
) -> Anomaly:
    """Attribute the delta across dimensions — greedy contribution search (§11.2).

    ``breakdown_on_day`` and ``breakdown_baseline`` map dimension → member →
    value. The search is deterministic and explains the *change*, not the level:
    a large team that grew slightly is less interesting than a small one that
    quadrupled, and only the former shows up in a naive "top spenders" list.
    """
    total_delta = anomaly.delta
    if total_delta == 0:
        return anomaly

    contributions: list[Contribution] = []
    for dimension, members in breakdown_on_day.items():
        baseline_members = breakdown_baseline.get(dimension, {})
        for member, value in members.items():
            delta = value - baseline_members.get(member, Decimal(0))
            if delta == 0:
                continue
            share = delta / total_delta
            if abs(share) < min_share:
                continue
            contributions.append(
                Contribution(
                    dimension=dimension,
                    member=member,
                    delta=delta.quantize(Decimal("0.000001")),
                    share_of_delta=share.quantize(Decimal("0.0001")),
                )
            )
        # Members present in the baseline but absent on the day are also part of
        # the story — a workload that stopped is a real cause of a drop.
        for member, baseline_value in baseline_members.items():
            if member in members:
                continue
            delta = -baseline_value
            share = delta / total_delta
            if abs(share) < min_share:
                continue
            contributions.append(
                Contribution(
                    dimension=dimension,
                    member=member,
                    delta=delta.quantize(Decimal("0.000001")),
                    share_of_delta=share.quantize(Decimal("0.0001")),
                )
            )

    contributions.sort(key=lambda c: abs(c.share_of_delta), reverse=True)
    anomaly.contributions = contributions[:top_n]
    return anomaly


def explain_delta(
    period_a: Mapping[str, Decimal],
    period_b: Mapping[str, Decimal],
    *,
    dimension: str = "member",
    top_n: int = 10,
) -> list[Contribution]:
    """Deterministic period-over-period contribution analysis.

    This is what the agent's ``explain_delta`` tool calls: the tool computes,
    the agent narrates (R12).
    """
    total_delta = sum(period_b.values(), Decimal(0)) - sum(period_a.values(), Decimal(0))
    members = set(period_a) | set(period_b)

    contributions = []
    for member in members:
        delta = period_b.get(member, Decimal(0)) - period_a.get(member, Decimal(0))
        if delta == 0:
            continue
        share = delta / total_delta if total_delta != 0 else Decimal(0)
        contributions.append(
            Contribution(
                dimension=dimension,
                member=member,
                delta=delta.quantize(Decimal("0.000001")),
                share_of_delta=share.quantize(Decimal("0.0001")),
            )
        )
    contributions.sort(key=lambda c: abs(c.delta), reverse=True)
    return contributions[:top_n]
