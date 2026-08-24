"""Cost attribution and the three-component compute cost (BUILD_PROMPT §10).

For each warehouse-day the compute bill splits into:

* **Direct** — the query's own attributed credits, from
  ``QUERY_ATTRIBUTION_HISTORY``.
* **Idle share** — ``metered − attributed``, spread across the teams active on
  that warehouse, pro-rata to their direct usage. *If you did not use it, you
  pay none of its idle.*
* **Cloud-services share** — account cloud-services credits net of the daily
  10% adjustment, spread pro-rata to compute.

Every figure is :class:`~decimal.Decimal`. Money is never float (§27.7), and
rounding happens once, at presentation, never during apportionment — otherwise
the components stop summing to the total and the reconciliation gate fails for
a reason that has nothing to do with the data.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

CENTS = Decimal("0.01")
CREDITS = Decimal("0.000000001")
UNATTRIBUTED = "UNATTRIBUTED"


class AllocationMethod(StrEnum):
    """Which waterfall rule attributed a cost, recorded on every figure (§9.3)."""

    QUERY_TAG = "query_tag"
    OBJECT_TAG = "object_tag"
    ROLE_MAP = "role_map"
    USER_MAP = "user_map"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class AttributionRule:
    """One step of the waterfall. First match wins."""

    id: str
    method: AllocationMethod
    description: str = ""
    enabled: bool = True
    json_path: str = "team"
    tag_name: str = "OWNER_TEAM"
    team: str = UNATTRIBUTED


@dataclass(frozen=True)
class TeamRegistry:
    """Role → team and user → team mappings (HR feed, CSV, or OIDC claim)."""

    role_to_team: dict[str, str] = field(default_factory=dict)
    user_to_team: dict[str, str] = field(default_factory=dict)
    warehouse_owner_team: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class QueryCost:
    """One query's attributed compute, with the signals the waterfall reads."""

    query_id: str
    warehouse: str
    usage_day: date
    credits: Decimal
    query_tag_team: str | None = None
    role: str | None = None
    user: str | None = None


@dataclass(frozen=True)
class WarehouseDay:
    """A warehouse's metered compute for one day."""

    warehouse: str
    usage_day: date
    metered_credits: Decimal


@dataclass(frozen=True)
class Attribution:
    """The team a cost was attributed to, and by which rule."""

    team: str
    method: AllocationMethod
    rule_id: str


DEFAULT_WATERFALL: tuple[AttributionRule, ...] = (
    AttributionRule(
        id="query_tag_team",
        method=AllocationMethod.QUERY_TAG,
        description="Team parsed from the query tag JSON.",
    ),
    AttributionRule(
        id="warehouse_owner_tag",
        method=AllocationMethod.OBJECT_TAG,
        description="OWNER_TEAM object tag on the warehouse.",
    ),
    AttributionRule(
        id="role_registry",
        method=AllocationMethod.ROLE_MAP,
        description="Role → team registry.",
    ),
    AttributionRule(
        id="user_registry",
        method=AllocationMethod.USER_MAP,
        description="User → team registry.",
    ),
    AttributionRule(
        id="unattributed",
        method=AllocationMethod.FALLBACK,
        description="No rule matched; reported as UNATTRIBUTED.",
        team=UNATTRIBUTED,
    ),
)


def attribute(
    query: QueryCost,
    registry: TeamRegistry,
    waterfall: Sequence[AttributionRule] = DEFAULT_WATERFALL,
) -> Attribution:
    """Run the waterfall for one query. First enabled rule that resolves wins."""
    for rule in waterfall:
        if not rule.enabled:
            continue
        team: str | None = None
        match rule.method:
            case AllocationMethod.QUERY_TAG:
                team = query.query_tag_team
            case AllocationMethod.OBJECT_TAG:
                team = registry.warehouse_owner_team.get(query.warehouse)
            case AllocationMethod.ROLE_MAP:
                team = registry.role_to_team.get(query.role or "")
            case AllocationMethod.USER_MAP:
                team = registry.user_to_team.get(query.user or "")
            case AllocationMethod.FALLBACK:
                team = rule.team
        if team:
            return Attribution(team=team, method=rule.method, rule_id=rule.id)
    return Attribution(team=UNATTRIBUTED, method=AllocationMethod.FALLBACK, rule_id="unattributed")


def apportion(
    total: Decimal, weights: dict[str, Decimal], *, quantum: Decimal = CREDITS
) -> dict[str, Decimal]:
    """Split ``total`` across keys pro-rata to ``weights``, losing nothing.

    Naive per-key rounding leaves a residue that makes the parts disagree with
    the whole — which shows up later as a reconciliation failure that looks like
    a data problem. The largest-remainder method assigns every quantum: the
    parts always sum exactly to the total.
    """
    if total == 0 or not weights:
        return {}
    positive = {key: weight for key, weight in weights.items() if weight > 0}
    if not positive:
        return {}

    weight_total = sum(positive.values(), Decimal(0))
    exact = {key: total * weight / weight_total for key, weight in positive.items()}
    floored = {key: value.quantize(quantum, rounding="ROUND_DOWN") for key, value in exact.items()}

    residue = total - sum(floored.values(), Decimal(0))
    if residue != 0:
        # Hand the remaining quanta to the largest fractional parts, in a stable
        # order so the same inputs always produce the same split.
        remainders = sorted(
            positive, key=lambda key: (exact[key] - floored[key], key), reverse=True
        )
        step = quantum if residue > 0 else -quantum
        index = 0
        while residue != 0 and remainders:
            key = remainders[index % len(remainders)]
            floored[key] += step
            residue -= step
            index += 1
    return floored


@dataclass(frozen=True)
class TeamAllocation:
    """One team's fully allocated cost for one warehouse-day."""

    team: str
    warehouse: str
    usage_day: date
    direct_credits: Decimal
    idle_credits: Decimal
    cloud_services_credits: Decimal = Decimal(0)

    @property
    def total_credits(self) -> Decimal:
        return self.direct_credits + self.idle_credits + self.cloud_services_credits

    def cost_usd(self, credit_price: Decimal) -> Decimal:
        """Cost in currency, rounded once, at the end."""
        return (self.total_credits * credit_price).quantize(CENTS)


@dataclass
class AllocationResult:
    """Everything the chargeback workbench and the reconciliation gate need."""

    allocations: list[TeamAllocation] = field(default_factory=list)
    #: Why each team's direct cost was attributed the way it was.
    methods: dict[str, AllocationMethod] = field(default_factory=dict)

    @property
    def total_credits(self) -> Decimal:
        return sum((a.total_credits for a in self.allocations), Decimal(0))

    def by_team(self) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = {}
        for allocation in self.allocations:
            totals[allocation.team] = totals.get(allocation.team, Decimal(0)) + (
                allocation.total_credits
            )
        return dict(sorted(totals.items()))

    def by_team_usd(self, credit_price: Decimal) -> dict[str, Decimal]:
        return {
            team: (credits * credit_price).quantize(CENTS)
            for team, credits in self.by_team().items()
        }

    def unattributed_share(self) -> Decimal:
        totals = self.by_team()
        total = sum(totals.values(), Decimal(0))
        if total == 0:
            return Decimal(0)
        return totals.get(UNATTRIBUTED, Decimal(0)) / total


class AllocationEngine:
    """Applies the waterfall and the three-component split."""

    def __init__(
        self,
        registry: TeamRegistry | None = None,
        waterfall: Sequence[AttributionRule] = DEFAULT_WATERFALL,
    ) -> None:
        self.registry = registry or TeamRegistry()
        self.waterfall = tuple(waterfall)

    def allocate(
        self,
        warehouse_days: Iterable[WarehouseDay],
        queries: Iterable[QueryCost],
        *,
        cloud_services_credits: dict[date, Decimal] | None = None,
    ) -> AllocationResult:
        """Allocate compute (direct + idle) and, optionally, cloud services."""
        result = AllocationResult()
        cloud_services_credits = cloud_services_credits or {}

        # Group queries by the warehouse-day they ran on.
        direct: dict[tuple[str, date], dict[str, Decimal]] = {}
        for query in queries:
            attribution = attribute(query, self.registry, self.waterfall)
            key = (query.warehouse, query.usage_day)
            teams = direct.setdefault(key, {})
            teams[attribution.team] = teams.get(attribution.team, Decimal(0)) + query.credits
            result.methods[attribution.team] = attribution.method

        for warehouse_day in warehouse_days:
            key = (warehouse_day.warehouse, warehouse_day.usage_day)
            team_direct = direct.get(key, {})
            attributed = sum(team_direct.values(), Decimal(0))
            idle = warehouse_day.metered_credits - attributed

            if idle < 0:
                # Attribution can exceed metering only through data error; never
                # produce a negative idle figure, which would understate a team.
                idle = Decimal(0)

            if team_direct:
                idle_shares = apportion(idle, team_direct)
            else:
                # Nobody used the warehouse: all of it is unattributable idle,
                # reported rather than silently spread across innocent teams.
                idle_shares = {UNATTRIBUTED: idle} if idle > 0 else {}
                if idle > 0:
                    result.methods.setdefault(UNATTRIBUTED, AllocationMethod.FALLBACK)

            for team in sorted(set(team_direct) | set(idle_shares)):
                result.allocations.append(
                    TeamAllocation(
                        team=team,
                        warehouse=warehouse_day.warehouse,
                        usage_day=warehouse_day.usage_day,
                        direct_credits=team_direct.get(team, Decimal(0)),
                        idle_credits=idle_shares.get(team, Decimal(0)),
                    )
                )

        if cloud_services_credits:
            result = self._apply_cloud_services(result, cloud_services_credits)
        return result

    @staticmethod
    def _apply_cloud_services(
        result: AllocationResult, cloud_services: dict[date, Decimal]
    ) -> AllocationResult:
        """Spread each day's billed cloud services pro-rata to that day's compute.

        The 10% adjustment is an account-level daily calculation and cannot be
        attributed per warehouse (verified — see docs/ASSUMPTIONS.md §4), so the
        *net* account figure is what gets shared.
        """
        by_day: dict[date, list[int]] = {}
        for index, allocation in enumerate(result.allocations):
            by_day.setdefault(allocation.usage_day, []).append(index)

        updated: list[TeamAllocation] = list(result.allocations)
        for usage_day, indexes in by_day.items():
            total = cloud_services.get(usage_day)
            if not total:
                continue
            weights = {
                str(index): updated[index].direct_credits + updated[index].idle_credits
                for index in indexes
            }
            shares = apportion(total, weights)
            for key, share in shares.items():
                index = int(key)
                allocation = updated[index]
                updated[index] = TeamAllocation(
                    team=allocation.team,
                    warehouse=allocation.warehouse,
                    usage_day=allocation.usage_day,
                    direct_credits=allocation.direct_credits,
                    idle_credits=allocation.idle_credits,
                    cloud_services_credits=share,
                )
        result.allocations = updated
        return result
