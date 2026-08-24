"""Savings claims (BUILD_PROMPT §11.3) — closing the Inform → Optimize → Operate loop.

An accepted recommendation becomes a tracked claim, verified after an
observation window against actuals. **Realised vs claimed saving is itself a
KPI**, because a platform that only ever reports what it *hoped* to save
teaches its users to discount every future number it produces.

A claim can come back as *over*-delivered, under-delivered, or reversed. All
three are reported. Verification never silently rounds a miss up to the claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

DEFAULT_OBSERVATION_DAYS = 14
#: Within this band of the claim, the claim is considered met.
REALISATION_TOLERANCE = Decimal("0.20")


class ClaimStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"  # approved by a human (R8)
    APPLIED = "applied"  # the change is live; the clock is running
    VERIFIED = "verified"  # measured against actuals
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class Realisation(StrEnum):
    MET = "met"
    EXCEEDED = "exceeded"
    UNDER = "under"
    #: Cost went *up* after the change.
    REVERSED = "reversed"
    PENDING = "pending"


@dataclass
class SavingsClaim:
    """One accepted recommendation, tracked to a measured outcome."""

    id: str
    lever: str
    target: str
    claimed_monthly_credits: Decimal
    status: ClaimStatus = ClaimStatus.PROPOSED
    observation_days: int = DEFAULT_OBSERVATION_DAYS
    #: The measured run rate before the change — the baseline to compare against.
    baseline_daily_credits: Decimal | None = None
    applied_on: date | None = None
    #: Set by a human approval (R8). A claim never applies itself.
    approved_by: str | None = None
    approved_at: datetime | None = None
    realised_monthly_credits: Decimal | None = None
    realisation: Realisation = Realisation.PENDING
    notes: list[str] = field(default_factory=list)

    @property
    def verifiable_on(self) -> date | None:
        if self.applied_on is None:
            return None
        return self.applied_on + timedelta(days=self.observation_days)

    def approve(self, actor: str, *, baseline_daily_credits: Decimal, on: date) -> None:
        """Record the human approval that R8 requires before anything changes."""
        self.status = ClaimStatus.ACCEPTED
        self.approved_by = actor
        self.approved_at = datetime.now(tz=UTC)
        self.baseline_daily_credits = baseline_daily_credits
        self.applied_on = on

    def reject(self, actor: str, reason: str) -> None:
        self.status = ClaimStatus.REJECTED
        self.approved_by = actor
        self.notes.append(f"Rejected by {actor}: {reason}")

    def roll_back(self, actor: str, reason: str) -> None:
        self.status = ClaimStatus.ROLLED_BACK
        self.notes.append(f"Rolled back by {actor}: {reason}")

    @property
    def variance(self) -> Decimal | None:
        """Realised minus claimed. Negative means the claim overstated."""
        if self.realised_monthly_credits is None:
            return None
        return self.realised_monthly_credits - self.claimed_monthly_credits

    def summary(self) -> str:
        if self.realisation is Realisation.PENDING:
            when = self.verifiable_on
            return (
                f"{self.lever} on {self.target}: claimed "
                f"{self.claimed_monthly_credits:.1f} credits/month, verifiable "
                f"{when if when else 'once applied'}."
            )
        return (
            f"{self.lever} on {self.target}: claimed "
            f"{self.claimed_monthly_credits:.1f}, realised "
            f"{self.realised_monthly_credits:.1f} credits/month "
            f"({self.realisation.value})."
        )


def verify(
    claim: SavingsClaim,
    observed_daily_credits: Decimal,
    *,
    on: date | None = None,
) -> SavingsClaim:
    """Measure a claim against actuals after its observation window.

    ``observed_daily_credits`` is the post-change run rate for the same target,
    measured over the observation window. The comparison is against the
    *recorded baseline*, so a change in workload volume is visible as a missed
    claim rather than being quietly absorbed.
    """
    today = on or date.today()  # noqa: DTZ011
    if claim.baseline_daily_credits is None or claim.applied_on is None:
        claim.notes.append("Cannot verify: no baseline was recorded at approval time.")
        return claim
    if claim.verifiable_on and today < claim.verifiable_on:
        claim.notes.append(
            f"Too early to verify: the observation window ends {claim.verifiable_on}."
        )
        return claim

    daily_saving = claim.baseline_daily_credits - observed_daily_credits
    realised = (daily_saving * Decimal(30)).quantize(Decimal("0.1"))
    claim.realised_monthly_credits = realised
    claim.status = ClaimStatus.VERIFIED

    if realised <= 0:
        claim.realisation = Realisation.REVERSED
        claim.notes.append(
            "Consumption did not fall after the change. Check whether the change was "
            "applied, and whether workload volume changed in the same window."
        )
        return claim

    if claim.claimed_monthly_credits == 0:
        claim.realisation = Realisation.EXCEEDED
        return claim

    ratio = realised / claim.claimed_monthly_credits
    if ratio >= Decimal(1) + REALISATION_TOLERANCE:
        claim.realisation = Realisation.EXCEEDED
    elif ratio >= Decimal(1) - REALISATION_TOLERANCE:
        claim.realisation = Realisation.MET
    else:
        claim.realisation = Realisation.UNDER
        claim.notes.append(
            f"Realised {ratio:.0%} of the claim. The model over-estimated; "
            "the lever's assumptions should be reviewed."
        )
    return claim


@dataclass
class SavingsLedger:
    """All claims, and the honesty metric derived from them."""

    claims: list[SavingsClaim] = field(default_factory=list)

    def add(self, claim: SavingsClaim) -> None:
        self.claims.append(claim)

    @property
    def verified(self) -> list[SavingsClaim]:
        return [c for c in self.claims if c.status is ClaimStatus.VERIFIED]

    @property
    def total_claimed(self) -> Decimal:
        return sum((c.claimed_monthly_credits for c in self.verified), Decimal(0))

    @property
    def total_realised(self) -> Decimal:
        return sum((c.realised_monthly_credits or Decimal(0) for c in self.verified), Decimal(0))

    @property
    def realisation_rate(self) -> Decimal | None:
        """Realised ÷ claimed across verified claims — the KPI (§11.3).

        None, not zero, when nothing has been verified: an unmeasured programme
        is unmeasured, not unsuccessful (R3).
        """
        if not self.verified or self.total_claimed == 0:
            return None
        return (self.total_realised / self.total_claimed).quantize(Decimal("0.0001"))

    def summary(self) -> str:
        rate = self.realisation_rate
        if rate is None:
            pending = len([c for c in self.claims if c.realisation is Realisation.PENDING])
            return f"No claims verified yet ({pending} pending)."
        return (
            f"{len(self.verified)} claims verified: {self.total_realised:.0f} credits/month "
            f"realised against {self.total_claimed:.0f} claimed ({rate:.0%})."
        )
