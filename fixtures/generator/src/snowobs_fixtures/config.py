"""Generator configuration (BUILD_PROMPT §7.5).

Everything is derived from a single integer seed and an anchor date, so a given
configuration always produces byte-identical output — tests and the demo rely
on that determinism.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Scale(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


_SCALE_QUERIES_PER_DAY = {Scale.SMALL: 1_500, Scale.MEDIUM: 12_000, Scale.LARGE: 50_000}


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

    @property
    def daily_queries(self) -> int:
        return self.queries_per_day or _SCALE_QUERIES_PER_DAY[self.scale]

    @property
    def start_date(self) -> date:
        from datetime import timedelta

        return self.end_date - timedelta(days=self.days - 1)
