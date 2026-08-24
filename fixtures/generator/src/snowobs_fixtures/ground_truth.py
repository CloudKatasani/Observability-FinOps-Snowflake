"""Ground truth for planted phenomena (BUILD_PROMPT §7.5).

Every phenomenon the application is supposed to detect is declared here with
the object it is anchored to and the window it occupies. Tests assert detection
against this file, so it is written alongside the data by the generator and is
the contract between the generator and the analytics engines.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from snowobs_fixtures.account import (
    CLONE_TABLE,
    DRIFT_USER,
    DT_LAGGING,
    REGRESSION_FINGERPRINT,
    SPIKE_TEAM,
    SPILL_FINGERPRINT,
    WH_OVERSIZED,
    WH_QUEUED,
    WH_REGRESSION,
    WH_SPIKE,
    WH_SPILL,
    WH_UNTAGGED_1,
    WH_UNTAGGED_2,
    WH_ZOMBIE,
)
from snowobs_fixtures.config import GeneratorConfig

PhenomenonKind = Literal[
    "oversized_warehouse",
    "queueing_saturation",
    "fingerprint_regression",
    "remote_spill",
    "spend_spike",
    "task_root_failure",
    "dynamic_table_lag",
    "untagged_spend",
    "dormant_users",
    "privilege_drift",
    "storage_clone_growth",
    "time_travel_excess",
    "ai_spend_growth",
    "zombie_warehouse",
    # Organization-wide phenomena (see ``organization.py``).
    "runaway_account",
    "stranded_commitment",
    "cross_region_egress",
    "account_untagged_spend",
    "effective_rate_outlier",
]


class Phenomenon(BaseModel):
    """One plantable, detectable condition."""

    id: str
    kind: PhenomenonKind
    description: str
    #: Objects the detector must name (warehouse, fingerprint, user, table…).
    subjects: list[str] = Field(default_factory=list)
    window_start: date | None = None
    window_end: date | None = None
    #: Detector-checkable expectations, e.g. {"min_ratio": 4.0}.
    expectations: dict[str, float | int | str] = Field(default_factory=dict)


class GroundTruth(BaseModel):
    seed: int
    days: int
    start_date: date
    end_date: date
    phenomena: list[Phenomenon]

    def by_kind(self, kind: PhenomenonKind) -> list[Phenomenon]:
        return [p for p in self.phenomena if p.kind == kind]

    def get(self, phenomenon_id: str) -> Phenomenon:
        return next(p for p in self.phenomena if p.id == phenomenon_id)


def build_ground_truth(config: GeneratorConfig) -> GroundTruth:
    start, end = config.start_date, config.end_date
    # Phenomena anchored to specific days of the generated window.
    regression_day = start + timedelta(days=min(60, config.days - 5))
    spike_day = start + timedelta(days=min(85, config.days - 3))
    dt_lag_start = start + timedelta(days=min(95, config.days - 8))
    ai_start = start + timedelta(days=min(70, config.days - 10))
    drift_day = start + timedelta(days=min(100, config.days - 4))
    failure_day = start + timedelta(days=min(75, config.days - 6))

    # A profile that deliberately degrades tagging discipline moves the untagged
    # share well outside the base profile's calibrated band, so the expectation
    # follows the profile rather than pretending the band still applies.
    untagged_subjects = [WH_UNTAGGED_1, WH_UNTAGGED_2, *config.untagged_warehouses]
    untagged_expectations: dict[str, float | int | str] = (
        {"min_untagged_pct": 25.0, "max_untagged_pct": 95.0}
        if config.untagged_warehouses
        else {"min_untagged_pct": 12.0, "max_untagged_pct": 25.0}
    )
    untagged_description = (
        f"Degraded tagging discipline: {len(untagged_subjects)} warehouses carry no owner team."
        if config.untagged_warehouses
        else "~18% of spend is untagged, concentrated in two warehouses."
    )

    return GroundTruth(
        seed=config.seed,
        days=config.days,
        start_date=start,
        end_date=end,
        phenomena=[
            Phenomenon(
                id="ph-oversized-wh",
                kind="oversized_warehouse",
                description=(
                    "Persistently over-sized warehouse: low utilisation, no queueing, "
                    "a size reduction is safe."
                ),
                subjects=[WH_OVERSIZED],
                window_start=start,
                window_end=end,
                expectations={"max_utilisation_pct": 25.0, "recommended_size_steps_down": 2},
            ),
            Phenomenon(
                id="ph-queueing",
                kind="queueing_saturation",
                description="Sustained queueing with multi-cluster saturation at max_clusters.",
                subjects=[WH_QUEUED],
                window_start=start,
                window_end=end,
                expectations={"min_queue_time_pct": 5.0},
            ),
            Phenomenon(
                id="ph-fingerprint-regression",
                kind="fingerprint_regression",
                description=(
                    "Query fingerprint regresses via pruning collapse and becomes the top "
                    "cost offender."
                ),
                subjects=[REGRESSION_FINGERPRINT, WH_REGRESSION],
                window_start=regression_day,
                window_end=end,
                expectations={
                    "min_cost_increase_ratio": 3.0,
                    "pruning_ratio_before": 0.08,
                    "pruning_ratio_after": 0.95,
                },
            ),
            Phenomenon(
                id="ph-remote-spill",
                kind="remote_spill",
                description="An ELT job spills to remote storage on every run.",
                subjects=[SPILL_FINGERPRINT, WH_SPILL],
                window_start=start,
                window_end=end,
                expectations={"min_remote_spill_bytes": 5e10},
            ),
            Phenomenon(
                id="ph-spend-spike",
                kind="spend_spike",
                description=(
                    "A single-day 4x spend spike attributable to one team on one warehouse."
                ),
                subjects=[WH_SPIKE, SPIKE_TEAM],
                window_start=spike_day,
                window_end=spike_day,
                expectations={"min_spike_ratio": 3.5},
            ),
            Phenomenon(
                id="ph-task-root-failure",
                kind="task_root_failure",
                description=(
                    "A root task failure fans out to 12 downstream failures — one alert "
                    "at the root, not twelve."
                ),
                subjects=["TASK_LOAD_CORE"],
                window_start=failure_day,
                window_end=failure_day,
                expectations={"downstream_failures": 12},
            ),
            Phenomenon(
                id="ph-dt-lag",
                kind="dynamic_table_lag",
                description="A dynamic table misses its TARGET_LAG for three consecutive days.",
                subjects=[DT_LAGGING],
                window_start=dt_lag_start,
                window_end=dt_lag_start + timedelta(days=2),
                expectations={"consecutive_days": 3, "target_lag_seconds": 3600},
            ),
            Phenomenon(
                id="ph-untagged-spend",
                kind="untagged_spend",
                description=untagged_description,
                subjects=untagged_subjects,
                window_start=start,
                window_end=end,
                expectations=untagged_expectations,
            ),
            Phenomenon(
                id="ph-dormant-users",
                kind="dormant_users",
                description="A cohort of users with no login in the last 90 days.",
                subjects=[f"CONTRACTOR_DORMANT_{i:02d}" for i in range(1, 7)],
                window_start=start,
                window_end=end,
                expectations={"cohort_size": 6},
            ),
            Phenomenon(
                id="ph-privilege-drift",
                kind="privilege_drift",
                description="A new ACCOUNTADMIN-adjacent grant appears in the grant graph.",
                subjects=[DRIFT_USER],
                window_start=drift_day,
                window_end=drift_day,
                expectations={"granted_role": "SECURITYADMIN"},
            ),
            Phenomenon(
                id="ph-clone-growth",
                kind="storage_clone_growth",
                description="Storage growth from an un-dropped clone retained for its clone group.",
                subjects=[CLONE_TABLE],
                window_start=start,
                window_end=end,
                expectations={"min_retained_for_clone_bytes": 4e11},
            ),
            Phenomenon(
                id="ph-time-travel-excess",
                kind="time_travel_excess",
                description="Excessive Time Travel retention in non-prod databases.",
                subjects=["DB_DEV", "DB_SANDBOX"],
                window_start=start,
                window_end=end,
                expectations={"min_time_travel_ratio": 0.25, "policy_max_ratio": 0.10},
            ),
            Phenomenon(
                id="ph-ai-spend-growth",
                kind="ai_spend_growth",
                description="Cortex/AI spend appears in week 10 and grows steadily.",
                subjects=["AI_SERVICES"],
                window_start=ai_start,
                window_end=end,
                expectations={"min_growth_ratio": 2.0},
            ),
            Phenomenon(
                id="ph-zombie-warehouse",
                kind="zombie_warehouse",
                description="A warehouse consuming credits with no queries in the last 30 days.",
                subjects=[WH_ZOMBIE],
                window_start=end - timedelta(days=30),
                window_end=end,
                expectations={"idle_days": 30},
            ),
        ],
    )
