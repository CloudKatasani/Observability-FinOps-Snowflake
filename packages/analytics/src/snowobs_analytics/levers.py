"""Optimisation levers (BUILD_PROMPT §11.3).

Each lever is a simulatable model: given observed evidence it produces a
modelled saving, a confidence, a risk note, the exact change statement, the
exact rollback, and a CAB-ready change record. Nothing is applied — the
recommendation goes to a human, who approves it (R8).

The models are deliberately simple arithmetic over observed telemetry rather
than fitted black boxes: a FinOps analyst has to defend the number to a
warehouse owner who does not want their warehouse resized.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

#: Credits per hour per cluster, by size. Each step down halves the rate.
SIZE_CREDITS_PER_HOUR: dict[str, Decimal] = {
    "X-Small": Decimal("1"),
    "Small": Decimal("2"),
    "Medium": Decimal("4"),
    "Large": Decimal("8"),
    "X-Large": Decimal("16"),
    "2X-Large": Decimal("32"),
    "3X-Large": Decimal("64"),
    "4X-Large": Decimal("128"),
}
SIZE_ORDER = list(SIZE_CREDITS_PER_HOUR)

#: Auto-suspend policy from §9.2 D2.
POLICY_AUTOSUSPEND_ELT = 60
POLICY_AUTOSUSPEND_BI = 300


class LeverId(StrEnum):
    RIGHTSIZE = "warehouse_rightsizing"
    AUTOSUSPEND = "autosuspend_tuning"
    ZOMBIE = "idle_zombie_elimination"
    MULTICLUSTER = "multicluster_policy"
    QUERY_OPTIMISATION = "query_optimisation"
    CLUSTERING_REVIEW = "clustering_search_optimisation_review"
    STORAGE_HYGIENE = "storage_hygiene"
    SCHEDULING = "scheduling_consolidation"
    RESULT_CACHE = "result_cache_utilisation"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Risk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class WarehouseEvidence:
    """What the metrics say about one warehouse over the observation window."""

    warehouse: str
    size: str
    auto_suspend_seconds: int
    min_clusters: int
    max_clusters: int
    days_observed: int
    metered_credits: Decimal
    attributed_credits: Decimal
    query_count: int
    queued_overload_ms: int
    elapsed_ms: int
    spill_remote_bytes: int = 0
    spill_local_bytes: int = 0
    workload_class: str = "bi"  # bi | elt | adhoc | training
    #: Measured seconds between the last query and suspension, averaged.
    observed_suspend_gap_seconds: float | None = None

    @property
    def utilisation(self) -> Decimal:
        if self.metered_credits == 0:
            return Decimal(0)
        return self.attributed_credits / self.metered_credits

    @property
    def idle_credits(self) -> Decimal:
        return max(self.metered_credits - self.attributed_credits, Decimal(0))

    @property
    def queue_share(self) -> Decimal:
        if self.elapsed_ms == 0:
            return Decimal(0)
        return Decimal(self.queued_overload_ms) / Decimal(self.elapsed_ms)

    @property
    def credits_per_hour(self) -> Decimal:
        return SIZE_CREDITS_PER_HOUR.get(self.size, Decimal("1"))

    @property
    def policy_autosuspend(self) -> int:
        return POLICY_AUTOSUSPEND_ELT if self.workload_class == "elt" else POLICY_AUTOSUSPEND_BI


@dataclass(frozen=True)
class Recommendation:
    """A recommendation card (§11.3): evidence, impact, risk, change, rollback."""

    lever: LeverId
    target: str
    title: str
    #: What the telemetry shows, in the words an owner will be shown.
    evidence: list[str]
    modelled_monthly_credits_saved: Decimal
    confidence: Confidence
    risk: Risk
    risk_note: str
    change_sql: str
    rollback_sql: str
    owner: str = "platform"
    observation_window_days: int = 14
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def monthly_usd(self, credit_price: Decimal | None) -> Decimal | None:
        if credit_price is None:
            return None
        return (self.modelled_monthly_credits_saved * credit_price).quantize(Decimal("0.01"))

    def change_record(self, credit_price: Decimal | None = None) -> str:
        """A CAB-ready change record (§11.3)."""
        saving = f"{self.modelled_monthly_credits_saved:.1f} credits/month"
        usd = self.monthly_usd(credit_price)
        if usd is not None:
            saving += f" (~${usd}/month)"
        evidence = "\n".join(f"  - {line}" for line in self.evidence)
        return (
            f"CHANGE RECORD — {self.title}\n"
            f"Target:        {self.target}\n"
            f"Lever:         {self.lever.value}\n"
            f"Modelled saving: {saving}\n"
            f"Confidence:    {self.confidence.value}\n"
            f"Risk:          {self.risk.value} — {self.risk_note}\n"
            f"Owner:         {self.owner}\n"
            f"Evidence:\n{evidence}\n"
            f"Change:\n  {self.change_sql}\n"
            f"Rollback:\n  {self.rollback_sql}\n"
            f"Verification:  re-measure after {self.observation_window_days} days; "
            f"the realised saving is tracked as a savings claim.\n"
        )


def _monthly(credits_in_window: Decimal, days: int) -> Decimal:
    """Scale a windowed figure to a month, without inventing precision."""
    if days <= 0:
        return Decimal(0)
    return (credits_in_window / Decimal(days) * Decimal(30)).quantize(Decimal("0.1"))


# ═══════════════════════════════════════════════════════════ the levers ══════
def rightsize(evidence: WarehouseEvidence) -> Recommendation | None:
    """Low utilisation with no queueing and no spill → a size step down.

    Spill is the veto: a warehouse spilling to remote storage is *under*-sized
    however idle it looks, and shrinking it would make a slow job catastrophic.
    """
    if evidence.metered_credits == 0 or evidence.days_observed == 0:
        return None
    if evidence.utilisation >= Decimal("0.35"):
        return None
    if evidence.queue_share > Decimal("0.02"):
        return None
    if evidence.spill_remote_bytes > 0:
        return None

    index = SIZE_ORDER.index(evidence.size) if evidence.size in SIZE_ORDER else 0
    if index == 0:
        return None  # already the smallest size

    # One step down halves the credit rate. Two steps only where utilisation is
    # very low and there is no local spill either.
    steps = 2 if evidence.utilisation < Decimal("0.15") and evidence.spill_local_bytes == 0 else 1
    steps = min(steps, index)
    new_size = SIZE_ORDER[index - steps]
    factor = Decimal(1) - (SIZE_CREDITS_PER_HOUR[new_size] / evidence.credits_per_hour)
    saved = _monthly(evidence.metered_credits * factor, evidence.days_observed)

    return Recommendation(
        lever=LeverId.RIGHTSIZE,
        target=evidence.warehouse,
        title=f"Resize {evidence.warehouse} from {evidence.size} to {new_size}",
        evidence=[
            f"Utilisation {evidence.utilisation:.0%} over {evidence.days_observed} days "
            f"({evidence.attributed_credits:.1f} of {evidence.metered_credits:.1f} credits "
            "attributable to queries)",
            f"Queue overload {evidence.queue_share:.2%} — no evidence of contention",
            "No remote spill observed, so the workload fits in memory at this size",
            f"{evidence.query_count:,} queries in the window",
        ],
        modelled_monthly_credits_saved=saved,
        confidence=Confidence.HIGH if evidence.days_observed >= 14 else Confidence.MEDIUM,
        risk=Risk.MEDIUM if steps > 1 else Risk.LOW,
        risk_note=(
            "Queries that currently fit in memory could begin to spill at the smaller "
            "size. Watch remote spill and p95 latency for the observation window; "
            "the rollback restores the original size immediately."
        ),
        change_sql=f"ALTER WAREHOUSE {evidence.warehouse} SET WAREHOUSE_SIZE = '{new_size}';",
        rollback_sql=(
            f"ALTER WAREHOUSE {evidence.warehouse} SET WAREHOUSE_SIZE = '{evidence.size}';"
        ),
    )


def tune_autosuspend(evidence: WarehouseEvidence) -> Recommendation | None:
    """Auto-suspend longer than policy burns idle credits between queries."""
    policy = evidence.policy_autosuspend
    if evidence.auto_suspend_seconds <= policy:
        return None
    if evidence.query_count == 0:
        return None  # a warehouse with no queries is the zombie lever, not this one

    # Each query leaves the warehouse running for the auto-suspend delay. The
    # saving is the excess delay, times how often it is paid.
    excess_seconds = Decimal(evidence.auto_suspend_seconds - policy)
    # A conservative floor on how many distinct idle windows occur: the observed
    # gap count cannot exceed the query count, and clustering means it is fewer.
    idle_windows = Decimal(min(evidence.query_count, evidence.days_observed * 24))
    saved_credits = excess_seconds / Decimal(3600) * evidence.credits_per_hour * idle_windows
    saved = min(
        _monthly(saved_credits, evidence.days_observed),
        _monthly(evidence.idle_credits, evidence.days_observed),
    )
    if saved <= 0:
        return None

    return Recommendation(
        lever=LeverId.AUTOSUSPEND,
        target=evidence.warehouse,
        title=(
            f"Reduce auto-suspend on {evidence.warehouse} from "
            f"{evidence.auto_suspend_seconds}s to {policy}s"
        ),
        evidence=[
            f"Configured auto-suspend {evidence.auto_suspend_seconds}s against a "
            f"{evidence.workload_class.upper()} policy of {policy}s",
            f"Idle credits {evidence.idle_credits:.1f} of {evidence.metered_credits:.1f} "
            f"over {evidence.days_observed} days",
            f"{evidence.query_count:,} queries, so the delay is paid frequently",
        ],
        modelled_monthly_credits_saved=saved,
        confidence=Confidence.MEDIUM,
        risk=Risk.LOW,
        risk_note=(
            "Shorter auto-suspend means more warehouse resumes. Resume latency is "
            "sub-second for a warm cache but can add a few seconds cold; for "
            "interactive BI, confirm the owner accepts that before applying."
        ),
        change_sql=f"ALTER WAREHOUSE {evidence.warehouse} SET AUTO_SUSPEND = {policy};",
        rollback_sql=(
            f"ALTER WAREHOUSE {evidence.warehouse} SET AUTO_SUSPEND = "
            f"{evidence.auto_suspend_seconds};"
        ),
    )


def eliminate_zombie(evidence: WarehouseEvidence) -> Recommendation | None:
    """Credits with no queries at all: the warehouse should not be running."""
    if evidence.query_count > 0 or evidence.metered_credits <= 0:
        return None

    saved = _monthly(evidence.metered_credits, evidence.days_observed)
    return Recommendation(
        lever=LeverId.ZOMBIE,
        target=evidence.warehouse,
        title=f"Suspend {evidence.warehouse} — no queries in {evidence.days_observed} days",
        evidence=[
            f"{evidence.metered_credits:.1f} credits consumed with zero queries over "
            f"{evidence.days_observed} days",
            f"Auto-suspend is {evidence.auto_suspend_seconds}s, which is not taking "
            "effect — a long-running session is likely holding it open",
        ],
        modelled_monthly_credits_saved=saved,
        confidence=Confidence.HIGH,
        risk=Risk.LOW,
        risk_note=(
            "Confirm no scheduled job targets this warehouse outside the observation "
            "window (a monthly close job would not appear in a 14-day window). "
            "Suspending is instantly reversible."
        ),
        change_sql=(
            f"ALTER WAREHOUSE {evidence.warehouse} SUSPEND;\n"
            f"  ALTER WAREHOUSE {evidence.warehouse} SET AUTO_SUSPEND = 60;"
        ),
        rollback_sql=f"ALTER WAREHOUSE {evidence.warehouse} RESUME;",
    )


def tune_multicluster(evidence: WarehouseEvidence) -> Recommendation | None:
    """A multi-cluster warehouse that never queues does not need its minimum."""
    if evidence.max_clusters <= 1 or evidence.min_clusters <= 1:
        return None
    if evidence.queue_share > Decimal("0.01"):
        return None

    # Dropping min_clusters to 1 saves the standing cost of the extra clusters.
    excess = Decimal(evidence.min_clusters - 1) / Decimal(evidence.min_clusters)
    saved = _monthly(evidence.metered_credits * excess, evidence.days_observed)
    return Recommendation(
        lever=LeverId.MULTICLUSTER,
        target=evidence.warehouse,
        title=f"Set MIN_CLUSTER_COUNT = 1 on {evidence.warehouse}",
        evidence=[
            f"MIN_CLUSTER_COUNT is {evidence.min_clusters}, so that many clusters run "
            "whenever the warehouse is active",
            f"Queue overload {evidence.queue_share:.2%} — the extra clusters are not "
            "absorbing contention",
            f"MAX_CLUSTER_COUNT stays {evidence.max_clusters}, so bursts still scale out",
        ],
        modelled_monthly_credits_saved=saved,
        confidence=Confidence.MEDIUM,
        risk=Risk.LOW,
        risk_note=(
            "Scale-out still happens on demand, but the first burst after an idle "
            "period pays a short provisioning delay."
        ),
        change_sql=f"ALTER WAREHOUSE {evidence.warehouse} SET MIN_CLUSTER_COUNT = 1;",
        rollback_sql=(
            f"ALTER WAREHOUSE {evidence.warehouse} SET MIN_CLUSTER_COUNT = {evidence.min_clusters};"
        ),
    )


@dataclass(frozen=True)
class FingerprintEvidence:
    """What the metrics say about one query fingerprint."""

    fingerprint: str
    warehouse: str
    credits: Decimal
    executions: int
    days_observed: int
    partitions_scanned: int
    partitions_total: int
    spill_remote_bytes: int = 0
    sample_text: str = ""

    @property
    def pruning_ratio(self) -> Decimal:
        """Fraction of partitions actually scanned. 1.0 means no pruning at all."""
        if self.partitions_total == 0:
            return Decimal(0)
        return Decimal(self.partitions_scanned) / Decimal(self.partitions_total)


def optimise_query(evidence: FingerprintEvidence) -> Recommendation | None:
    """A fingerprint scanning everything, or spilling, is the top lever by value."""
    poor_pruning = evidence.pruning_ratio >= Decimal("0.9") and evidence.partitions_total > 100
    spilling = evidence.spill_remote_bytes > 0
    if not (poor_pruning or spilling):
        return None
    if evidence.credits <= 0:
        return None

    # Restoring pruning typically recovers most of the scan cost; spill is a
    # sizing problem and recovers less. Both are deliberately conservative.
    recovery = Decimal("0.6") if poor_pruning else Decimal("0.3")
    saved = _monthly(evidence.credits * recovery, evidence.days_observed)

    diagnosis: list[str] = []
    if poor_pruning:
        diagnosis.append(
            f"Scans {evidence.pruning_ratio:.0%} of {evidence.partitions_total:,} "
            "micro-partitions — effectively a full scan, so the filter is not "
            "eliminating partitions (a non-sargable predicate, a cast on the "
            "clustering column, or missing clustering)"
        )
    if spilling:
        diagnosis.append(
            f"Spills {evidence.spill_remote_bytes / 1e9:.1f} GB to remote storage — "
            "orders of magnitude slower than memory, and a sign the join or sort "
            "exceeds the warehouse's capacity"
        )

    return Recommendation(
        lever=LeverId.QUERY_OPTIMISATION,
        target=evidence.fingerprint,
        title=f"Optimise fingerprint {evidence.fingerprint} on {evidence.warehouse}",
        evidence=[
            f"{evidence.credits:.2f} credits over {evidence.days_observed} days across "
            f"{evidence.executions:,} executions",
            *diagnosis,
        ],
        modelled_monthly_credits_saved=saved,
        confidence=Confidence.MEDIUM if poor_pruning else Confidence.LOW,
        risk=Risk.MEDIUM,
        risk_note=(
            "A query change alters results if the rewrite is not equivalent. This "
            "requires the owning team to review and test — it is not a platform-side "
            "change, and the modelled saving assumes the rewrite restores pruning."
        ),
        change_sql=(
            "-- Owner action: review the predicate and clustering for this fingerprint.\n"
            f"  -- SELECT SYSTEM$CLUSTERING_INFORMATION('<table>');  -- run manually\n"
            f"  -- Sample: {evidence.sample_text[:120]}"
        ),
        rollback_sql="-- Revert the query change in the owning repository.",
    )


def rank(
    warehouses: Sequence[WarehouseEvidence],
    fingerprints: Sequence[FingerprintEvidence] = (),
    *,
    credit_price: Decimal | None = None,
) -> list[Recommendation]:
    """Run every lever and rank the results by modelled saving (§11.3)."""
    recommendations: list[Recommendation] = []
    for evidence in warehouses:
        for lever in (eliminate_zombie, rightsize, tune_autosuspend, tune_multicluster):
            recommendation = lever(evidence)
            if recommendation is not None:
                recommendations.append(recommendation)
    for fingerprint in fingerprints:
        recommendation = optimise_query(fingerprint)
        if recommendation is not None:
            recommendations.append(recommendation)

    del credit_price  # ranking is by credits; currency is a presentation concern
    recommendations.sort(key=lambda r: r.modelled_monthly_credits_saved, reverse=True)
    return recommendations
