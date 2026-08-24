"""Forecasting (BUILD_PROMPT §11.1) — transparent and explainable.

Finance must be able to follow the arithmetic, so the model is an explicit
decomposition rather than a black box:

    forecast = trend + day-of-week seasonality + day-of-month effect

The trend is a **robust** linear fit (Theil–Sen): the median of pairwise slopes,
which a single 4x spike day cannot drag. That matters here — the spend series
this reads is exactly the kind that contains one-off spikes, and an ordinary
least-squares line would chase them.

Every forecast is stored with the window it was fitted on, so accuracy can be
evaluated against what was knowable at the time rather than in hindsight.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

#: Below this many observations a seasonal component is noise, not signal.
MIN_DAYS_FOR_SEASONALITY = 14
MIN_DAYS_FOR_TREND = 7
#: 80% interval, per §11.1.
INTERVAL_Z = 1.2816


@dataclass(frozen=True)
class Observation:
    day: date
    value: Decimal


@dataclass(frozen=True)
class Components:
    """The decomposition, exposed so the UI can chart trend vs seasonality."""

    trend_intercept: float
    trend_slope: float
    #: weekday index (Mon=0) → additive effect
    weekday_effects: dict[int, float] = field(default_factory=dict)
    #: day-of-month → additive effect (month-end batch, billing cycles)
    month_day_effects: dict[int, float] = field(default_factory=dict)
    residual_sigma: float = 0.0

    def trend_at(self, index: int) -> float:
        return self.trend_intercept + self.trend_slope * index


@dataclass(frozen=True)
class ForecastPoint:
    day: date
    value: Decimal
    lower: Decimal
    upper: Decimal
    trend: Decimal
    seasonality: Decimal


@dataclass
class Forecast:
    """A stored forecast: what was predicted, from what, and when."""

    fitted_from: date
    fitted_to: date
    created_at: datetime
    components: Components
    points: list[ForecastPoint] = field(default_factory=list)
    #: Explicitly null when there is not enough history — never a fabricated line.
    insufficient_data_reason: str | None = None

    @property
    def usable(self) -> bool:
        return self.insufficient_data_reason is None and bool(self.points)

    def month_end_landing(self, *, actuals_to_date: Decimal, month_end: date) -> Decimal | None:
        """MTD actuals plus the forecast for the rest of the month."""
        if not self.usable:
            return None
        remaining = sum(
            (point.value for point in self.points if point.day <= month_end),
            Decimal(0),
        )
        return actuals_to_date + remaining

    def explain(self) -> str:
        if not self.usable:
            return f"No forecast: {self.insufficient_data_reason}"
        direction = "rising" if self.components.trend_slope > 0 else "falling"
        return (
            f"Trend {direction} by {abs(self.components.trend_slope):.2f} per day, "
            f"fitted on {(self.fitted_to - self.fitted_from).days + 1} days "
            f"({self.fitted_from} to {self.fitted_to}). "
            f"Weekday effects range "
            f"{min(self.components.weekday_effects.values(), default=0.0):+.1f} to "
            f"{max(self.components.weekday_effects.values(), default=0.0):+.1f}. "
            f"Residual sigma {self.components.residual_sigma:.2f}."
        )


def _theil_sen(values: Sequence[float]) -> tuple[float, float]:
    """Robust linear fit: median of pairwise slopes, median-anchored intercept.

    Chosen over least squares because a single spike day — which this data
    reliably contains — would otherwise tilt the whole forecast.
    """
    n = len(values)
    if n < 2:
        return (values[0] if values else 0.0), 0.0

    slopes = [(values[j] - values[i]) / (j - i) for i in range(n) for j in range(i + 1, n)]
    slope = statistics.median(slopes)
    intercept = statistics.median(value - slope * index for index, value in enumerate(values))
    return intercept, slope


def _seasonal_effects(
    residuals: Sequence[float], days: Sequence[date], key: str
) -> dict[int, float]:
    """Median residual per period bucket, centred so effects sum to ~zero."""
    buckets: dict[int, list[float]] = {}
    for residual, day in zip(residuals, days, strict=True):
        bucket = day.weekday() if key == "weekday" else day.day
        buckets.setdefault(bucket, []).append(residual)

    effects = {bucket: statistics.median(values) for bucket, values in buckets.items() if values}
    if not effects:
        return {}
    centre = statistics.median(effects.values())
    return {bucket: effect - centre for bucket, effect in effects.items()}


def fit(
    observations: Sequence[Observation],
    *,
    now: datetime | None = None,
) -> Forecast:
    """Fit the decomposition. Refuses rather than guesses on thin history."""
    created_at = now or datetime.now(tz=UTC)
    ordered = sorted(observations, key=lambda o: o.day)
    if len(ordered) < MIN_DAYS_FOR_TREND:
        return Forecast(
            fitted_from=ordered[0].day if ordered else date.today(),  # noqa: DTZ011
            fitted_to=ordered[-1].day if ordered else date.today(),  # noqa: DTZ011
            created_at=created_at,
            components=Components(trend_intercept=0.0, trend_slope=0.0),
            insufficient_data_reason=(
                f"needs at least {MIN_DAYS_FOR_TREND} days of history, has {len(ordered)}"
            ),
        )

    days = [o.day for o in ordered]
    values = [float(o.value) for o in ordered]
    intercept, slope = _theil_sen(values)
    residuals = [value - (intercept + slope * index) for index, value in enumerate(values)]

    weekday_effects: dict[int, float] = {}
    month_day_effects: dict[int, float] = {}
    if len(ordered) >= MIN_DAYS_FOR_SEASONALITY:
        weekday_effects = _seasonal_effects(residuals, days, "weekday")
        deseasonalised = [
            residual - weekday_effects.get(day.weekday(), 0.0)
            for residual, day in zip(residuals, days, strict=True)
        ]
        if len(ordered) >= 28:
            month_day_effects = _seasonal_effects(deseasonalised, days, "month_day")
        residuals = [
            value - month_day_effects.get(day.day, 0.0)
            for value, day in zip(deseasonalised, days, strict=True)
        ]

    sigma = statistics.pstdev(residuals) if len(residuals) > 1 else 0.0
    components = Components(
        trend_intercept=intercept,
        trend_slope=slope,
        weekday_effects=weekday_effects,
        month_day_effects=month_day_effects,
        residual_sigma=sigma,
    )
    return Forecast(
        fitted_from=days[0],
        fitted_to=days[-1],
        created_at=created_at,
        components=components,
    )


def project(forecast: Forecast, horizon_days: int, *, history_length: int) -> Forecast:
    """Extend a fitted forecast forward, with an 80% interval."""
    if not forecast.usable and forecast.insufficient_data_reason:
        return forecast

    components = forecast.components
    points: list[ForecastPoint] = []
    for step in range(1, horizon_days + 1):
        index = history_length - 1 + step
        day = forecast.fitted_to + timedelta(days=step)
        trend = components.trend_at(index)
        seasonality = components.weekday_effects.get(
            day.weekday(), 0.0
        ) + components.month_day_effects.get(day.day, 0.0)
        value = trend + seasonality
        # The interval widens with the horizon: uncertainty compounds.
        spread = INTERVAL_Z * components.residual_sigma * (step**0.5)
        points.append(
            ForecastPoint(
                day=day,
                value=_money(max(value, 0.0)),
                lower=_money(max(value - spread, 0.0)),
                upper=_money(max(value + spread, 0.0)),
                trend=_money(trend),
                seasonality=_money(seasonality),
            )
        )
    forecast.points = points
    return forecast


def forecast_series(
    observations: Sequence[Observation],
    horizon_days: int = 30,
    *,
    now: datetime | None = None,
) -> Forecast:
    """Fit and project in one call — the usual entry point."""
    fitted = fit(observations, now=now)
    return project(fitted, horizon_days, history_length=len(observations))


def _money(value: float) -> Decimal:
    return Decimal(str(round(value, 6)))


# ------------------------------------------------------------------ accuracy
@dataclass(frozen=True)
class AccuracyReport:
    """Rolling forecast accuracy, tracked as a first-class KPI (§11.1)."""

    mape: Decimal | None
    mae: Decimal | None
    observations: int
    #: Target from §11.1: under 8% by month 6.
    target_mape: Decimal = Decimal("8.0")

    @property
    def meets_target(self) -> bool | None:
        if self.mape is None:
            return None
        return self.mape <= self.target_mape


def evaluate(predicted: Sequence[ForecastPoint], actual: Sequence[Observation]) -> AccuracyReport:
    """Compare a stored forecast against what actually happened.

    Days with a zero actual are excluded from MAPE rather than treated as
    infinite error — the honest handling of an undefined percentage (R3).
    """
    actual_by_day = {observation.day: observation.value for observation in actual}
    errors: list[Decimal] = []
    percentages: list[Decimal] = []

    for point in predicted:
        truth = actual_by_day.get(point.day)
        if truth is None:
            continue
        error = abs(point.value - truth)
        errors.append(error)
        if truth != 0:
            percentages.append(error / abs(truth) * 100)

    if not errors:
        return AccuracyReport(mape=None, mae=None, observations=0)

    mae = sum(errors, Decimal(0)) / len(errors)
    mape = (
        (sum(percentages, Decimal(0)) / len(percentages)).quantize(Decimal("0.01"))
        if percentages
        else None
    )
    return AccuracyReport(
        mape=mape, mae=mae.quantize(Decimal("0.000001")), observations=len(errors)
    )


# --------------------------------------------------------------- commitment
@dataclass(frozen=True)
class CommitmentPosture:
    """Projected consumption against remaining balance (§11.1)."""

    remaining_balance: Decimal
    daily_burn: Decimal
    exhaustion_date: date | None
    #: True when the commitment will not be consumed before it expires.
    stranding_risk: bool
    contract_end: date | None = None

    def summary(self) -> str:
        if self.daily_burn <= 0:
            return "No consumption in the window; burn-down cannot be projected."
        if self.exhaustion_date is None:
            return (
                f"At {self.daily_burn:.1f} credits/day the remaining "
                f"{self.remaining_balance:.0f} credits outlast the contract."
            )
        return (
            f"At {self.daily_burn:.1f} credits/day the remaining "
            f"{self.remaining_balance:.0f} credits are exhausted on "
            f"{self.exhaustion_date}."
        )


def commitment_posture(
    remaining_balance: Decimal,
    recent: Sequence[Observation],
    *,
    contract_end: date | None = None,
    today: date | None = None,
) -> CommitmentPosture:
    """Project when the commitment runs out — or that it will be stranded."""
    reference = today or (recent[-1].day if recent else date.today())  # noqa: DTZ011
    if not recent:
        return CommitmentPosture(
            remaining_balance=remaining_balance,
            daily_burn=Decimal(0),
            exhaustion_date=None,
            stranding_risk=False,
            contract_end=contract_end,
        )

    burn = sum((o.value for o in recent), Decimal(0)) / len(recent)
    if burn <= 0:
        return CommitmentPosture(
            remaining_balance=remaining_balance,
            daily_burn=Decimal(0),
            exhaustion_date=None,
            stranding_risk=contract_end is not None,
            contract_end=contract_end,
        )

    days_left = int(remaining_balance / burn)
    exhaustion = reference + timedelta(days=days_left)
    stranding = contract_end is not None and exhaustion > contract_end
    return CommitmentPosture(
        remaining_balance=remaining_balance,
        daily_burn=burn.quantize(Decimal("0.01")),
        exhaustion_date=None if stranding else exhaustion,
        stranding_risk=stranding,
        contract_end=contract_end,
    )
