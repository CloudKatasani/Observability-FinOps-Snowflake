"""Deterministic synthetic account topology.

Warehouses, teams, users, databases, and task graphs are laid out so that every
planted phenomenon (§7.5) has a concrete anchor object. Names are stable across
runs for a given config so ground-truth assertions can reference them directly.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from snowobs_fixtures.config import GeneratorConfig

# Credits per hour for a single running cluster, by warehouse size.
SIZE_CREDITS_PER_HOUR: dict[str, float] = {
    "X-Small": 1.0,
    "Small": 2.0,
    "Medium": 4.0,
    "Large": 8.0,
    "X-Large": 16.0,
    "2X-Large": 32.0,
    "3X-Large": 64.0,
    "4X-Large": 128.0,
}

TEAM_NAMES = [
    "TEAM_ANALYTICS",
    "TEAM_DATA_ENG",
    "TEAM_MARKETING",
    "TEAM_FINANCE",
    "TEAM_OPS",
    "TEAM_ML",
    "TEAM_SALES",
    "TEAM_PRODUCT",
]


@dataclass(frozen=True)
class Warehouse:
    name: str
    size: str
    owner_team: str | None  # None → untagged (phenomenon: untagged spend)
    auto_suspend: int
    min_clusters: int = 1
    max_clusters: int = 1
    # busy_hours: expected hours of active compute per weekday (drives metering)
    busy_hours: float = 6.0
    weekend_factor: float = 0.3
    workload: str = "bi"  # bi | elt | adhoc | training | zombie

    @property
    def credits_per_hour(self) -> float:
        return SIZE_CREDITS_PER_HOUR[self.size]


@dataclass(frozen=True)
class User:
    name: str
    team: str
    dormant: bool = False  # phenomenon: no login in the last 90 days
    service_account: bool = False


@dataclass(frozen=True)
class Database:
    name: str
    environment: str  # prod | nonprod
    owner_team: str
    base_bytes: float
    daily_growth: float  # fraction/day
    time_travel_ratio: float  # phenomenon: excessive in non-prod


@dataclass(frozen=True)
class TaskGraph:
    root: str
    children: list[str]
    database: str
    schema: str
    schedule_hour: int


@dataclass
class Account:
    config: GeneratorConfig
    teams: list[str] = field(default_factory=list)
    warehouses: list[Warehouse] = field(default_factory=list)
    users: list[User] = field(default_factory=list)
    databases: list[Database] = field(default_factory=list)
    task_graphs: list[TaskGraph] = field(default_factory=list)

    def warehouse(self, name: str) -> Warehouse:
        return next(w for w in self.warehouses if w.name == name)

    def team_users(self, team: str, *, include_dormant: bool = False) -> list[User]:
        return [u for u in self.users if u.team == team and (include_dormant or not u.dormant)]


# Anchor objects for planted phenomena — stable names referenced by ground truth.
WH_OVERSIZED = "WH_ADHOC_OVERSIZED"
WH_QUEUED = "WH_BI_PRIMARY"
WH_SPILL = "WH_ELT_STAGING"
WH_SPIKE = "WH_DS_TRAINING"
WH_REGRESSION = "WH_ELT_CORE"
WH_UNTAGGED_1 = "WH_LEGACY_UNTAGGED"
WH_UNTAGGED_2 = "WH_SANDBOX_UNTAGGED"
WH_ZOMBIE = "WH_SANDBOX_UNTAGGED"
SPIKE_TEAM = "TEAM_ML"
REGRESSION_FINGERPRINT = "fp-pruning-regression-0001"
SPILL_FINGERPRINT = "fp-remote-spill-elt-0001"
DRIFT_USER = "SVC_ETL_LEGACY"
DT_LAGGING = "DB_ANALYTICS.MARTS.DT_ORDERS_SUMMARY"
CLONE_TABLE = "TBL_ORDERS_CLONE"


def build_account(config: GeneratorConfig) -> Account:
    rng = random.Random(config.seed)  # noqa: S311 — synthetic data, not crypto
    teams = TEAM_NAMES[: config.teams]

    base_warehouses = [
        Warehouse(WH_REGRESSION, "Large", "TEAM_DATA_ENG", 60, busy_hours=10, workload="elt"),
        Warehouse(WH_SPILL, "Medium", "TEAM_DATA_ENG", 60, busy_hours=8, workload="elt"),
        Warehouse(
            WH_QUEUED,
            "Medium",
            "TEAM_ANALYTICS",
            300,
            min_clusters=1,
            max_clusters=3,
            busy_hours=12,
            workload="bi",
        ),
        Warehouse("WH_BI_EXEC", "Small", "TEAM_ANALYTICS", 300, busy_hours=6, workload="bi"),
        Warehouse(
            WH_OVERSIZED,
            "2X-Large",
            "TEAM_OPS",
            600,
            busy_hours=5,
            weekend_factor=0.1,
            workload="adhoc",
        ),
        Warehouse(
            WH_SPIKE,
            "X-Large",
            SPIKE_TEAM,
            300,
            min_clusters=1,
            max_clusters=8,
            busy_hours=3,
            workload="training",
        ),
        Warehouse("WH_FINANCE_RPT", "Small", "TEAM_FINANCE", 300, busy_hours=5),
        Warehouse("WH_MARKETING", "Medium", "TEAM_MARKETING", 300, busy_hours=6),
        Warehouse("WH_SALES_OPS", "Small", "TEAM_SALES", 300, busy_hours=5),
        Warehouse("WH_PRODUCT_ANALYTICS", "Small", "TEAM_PRODUCT", 300, busy_hours=5),
        Warehouse(
            WH_UNTAGGED_1, "X-Large", None, 3600, busy_hours=9, weekend_factor=0.5, workload="adhoc"
        ),
        Warehouse(WH_UNTAGGED_2, "Large", None, 3600, busy_hours=4, workload="zombie"),
    ]
    warehouses = base_warehouses[: max(config.warehouses, 12)]

    users: list[User] = []
    for team in teams:
        headcount = rng.randint(4, 8)
        for i in range(1, headcount + 1):
            users.append(User(name=f"{team.removeprefix('TEAM_')}_USER_{i:02d}", team=team))
    # Service accounts and the dormant cohort (phenomenon 9).
    users.append(User(name="SVC_ELT_LOADER", team="TEAM_DATA_ENG", service_account=True))
    users.append(User(name="SVC_BI_REFRESH", team="TEAM_ANALYTICS", service_account=True))
    users.append(User(name=DRIFT_USER, team="TEAM_OPS", service_account=True))
    for i in range(1, 7):
        users.append(User(name=f"CONTRACTOR_DORMANT_{i:02d}", team="TEAM_OPS", dormant=True))

    databases = [
        Database("DB_PROD", "prod", "TEAM_DATA_ENG", 8e12, 0.002, 0.06),
        Database("DB_ANALYTICS", "prod", "TEAM_ANALYTICS", 3e12, 0.02, 0.08),
        Database("DB_DEV", "nonprod", "TEAM_DATA_ENG", 1.5e12, 0.004, 0.45),
        Database("DB_SANDBOX", "nonprod", "TEAM_OPS", 4e11, 0.001, 0.30),
    ]

    task_graphs = [
        TaskGraph(
            root="TASK_LOAD_CORE",
            children=[f"TASK_TRANSFORM_{i:02d}" for i in range(1, 13)],
            database="DB_PROD",
            schema="ELT",
            schedule_hour=2,
        ),
        TaskGraph(
            root="TASK_MARTS_REFRESH",
            children=[f"TASK_MART_{i:02d}" for i in range(1, 5)],
            database="DB_ANALYTICS",
            schema="MARTS",
            schedule_hour=4,
        ),
    ]

    return Account(
        config=config,
        teams=teams,
        warehouses=warehouses,
        users=users,
        databases=databases,
        task_graphs=task_graphs,
    )


def business_factor(day: date) -> float:
    """Weekday activity multiplier with a gentle mid-week peak."""
    weekday = day.weekday()
    if weekday >= 5:
        return 0.0  # weekend handled by warehouse.weekend_factor
    return [0.92, 1.0, 1.05, 1.02, 0.9][weekday]


def days_iter(config: GeneratorConfig) -> list[date]:
    return [config.start_date + timedelta(days=i) for i in range(config.days)]
