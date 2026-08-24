"""Generator configuration (BUILD_PROMPT §7.5).

Everything is derived from a single integer seed and an anchor date, so a given
configuration always produces byte-identical output — tests and the demo rely
on that determinism.

The profile knobs added for organization-wide generation (``scale_factor``,
``compute_growth_per_day``, ``workload_mix``, ``untagged_warehouses``) all
default to the neutral value, so an unchanged ``GeneratorConfig()`` produces
exactly the data it produced before they existed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

ONE = Decimal("1")
ZERO = Decimal("0")
CREDIT_PLACES = Decimal("0.000000001")


class Scale(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


_SCALE_QUERIES_PER_DAY = {Scale.SMALL: 1_500, Scale.MEDIUM: 12_000, Scale.LARGE: 50_000}

#: Relative query volume per workload class in the base profile. An account
#: profile scales these (and the matching warehouses' busy hours) through
#: ``GeneratorConfig.workload_mix`` — 1.0 means "as the base profile".
DEFAULT_WORKLOAD_WEIGHTS: dict[str, float] = {
    "elt": 1.4,
    "bi": 2.2,
    "adhoc": 1.6,
    "training": 0.3,
    "zombie": 0.0,
}


@dataclass(frozen=True)
class GeneratorConfig:
    seed: int = 42
    days: int = 120
    warehouses: int = 12
    teams: int = 8
    scale: Scale = Scale.SMALL
    queries_per_day: int | None = None  # None → derived from scale
    end_date: date = date(2026, 8, 20)  # fixed anchor keeps output deterministic
    credit_price_usd: float = 3.0
    #: Relative compute size of this account against the base profile. Applied
    #: to metered credits, so attribution, metering, and billing scale together.
    scale_factor: Decimal = ONE
    #: Compounding daily growth on metered compute — the runaway-account knob.
    compute_growth_per_day: Decimal = ZERO
    #: Per-workload-class multipliers on busy hours and query share, as
    #: ``(("bi", 1.6), ("elt", 0.5))``. Empty → the base profile's mix.
    workload_mix: tuple[tuple[str, float], ...] = ()
    #: Warehouses stripped of their owner team in addition to the two the base
    #: profile always leaves untagged — the tagging-discipline knob.
    untagged_warehouses: tuple[str, ...] = ()

    @property
    def daily_queries(self) -> int:
        return self.queries_per_day or _SCALE_QUERIES_PER_DAY[self.scale]

    @property
    def start_date(self) -> date:
        from datetime import timedelta

        return self.end_date - timedelta(days=self.days - 1)

    @property
    def workload_multipliers(self) -> dict[str, float]:
        """Per-workload-class multipliers, defaulting to 1.0 (the base profile)."""
        return dict(self.workload_mix)

    def compute_multiplier(self, day: date) -> Decimal:
        """Relative metered-compute scale for one day.

        Size and growth are one multiplier so that a scaled account's metering,
        attribution, and billing all move together — an account that is twice
        the size is twice the size in every table, not only in the cost one.
        """
        if self.scale_factor == ONE and self.compute_growth_per_day == ZERO:
            return ONE
        elapsed = (day - self.start_date).days
        growth = (ONE + self.compute_growth_per_day) ** elapsed
        return self.scale_factor * growth
