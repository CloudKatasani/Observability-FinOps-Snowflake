"""Organization or account: which scope a figure is being asked for (§9, R3).

An enterprise reads the same catalogue at two levels, and the two are not
interchangeable:

* **Organization** — every account together. `ORGANIZATION_USAGE` answers this
  natively for billing, metering, storage and transfer. For account-scoped
  metrics OFFLINE it means "computed over every account landed in the lake",
  which is a genuine organization figure *for the accounts present* — and
  partial if an account has not been uploaded.
* **Account** — one account. OFFLINE this filters on the account ingest stamped
  on each row; LIVE it selects that account's connection, because there the
  account is the connection rather than a column.

Not every metric can answer at every scope, and the honest failures differ:

* A metric on a genuinely organization-only source — a contract, a commitment
  balance — has no account to filter by. Scoping it to one account would return
  the organization's figure under an account's label.
* An account-scoped metric at organization scope in LIVE would need one query
  per account and a merge. Summing a ratio or a percentile across accounts is
  wrong, so the platform declines rather than inventing a number (R12).

Both are reported as unavailable with the reason, never as a zero or a silently
mis-scoped figure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from snowobs_semantics.compiler import ACCOUNT_DIMENSION
from snowobs_semantics.model import Metric, SemanticModel
from snowobs_semantics.registry import SourceRegistry


class Scope(StrEnum):
    ORGANIZATION = "organization"
    ACCOUNT = "account"


@dataclass(frozen=True)
class ScopeRequest:
    """What the caller asked to see, and where."""

    scope: Scope = Scope.ORGANIZATION
    #: Required when `scope` is ACCOUNT; ignored otherwise.
    account: str | None = None

    @property
    def account_filter(self) -> str | None:
        return self.account if self.scope is Scope.ACCOUNT else None

    def label(self) -> str:
        if self.scope is Scope.ACCOUNT and self.account:
            return self.account
        return "Organization"


@dataclass(frozen=True)
class ScopeVerdict:
    """Whether a metric can be answered at a scope, and why not when it cannot."""

    available: bool
    reason: str | None = None
    #: True when the figure covers only part of the organization — and the
    #: platform can say which part is missing.
    partial: bool = False
    #: Accounts the organization is known to contain that have landed nothing
    #: at account level. `ORGANIZATION_USAGE` names every account in the
    #: organization, so an account billing knows about but whose detail has
    #: never been uploaded is exactly the gap that reads as a quiet account.
    missing_accounts: list[str] = field(default_factory=list)

    @classmethod
    def ok(
        cls, *, partial: bool = False, missing_accounts: list[str] | None = None
    ) -> ScopeVerdict:
        return cls(available=True, partial=partial, missing_accounts=missing_accounts or [])

    @classmethod
    def no(cls, reason: str) -> ScopeVerdict:
        return cls(available=False, reason=reason)


def metric_is_organization_only(metric: Metric, registry: SourceRegistry) -> bool:
    """Does this metric describe the organization rather than any account?

    True when every source it reads is organization-scoped *and* the metric
    carries no account dimension — a contract's value belongs to the
    organization, and there is no per-account version of it to show.
    """
    if ACCOUNT_DIMENSION in metric.dimensions:
        return False
    return all(registry.get(source).is_organization_scoped for source in metric.requires_sources)


def assess(
    metric: Metric,
    request: ScopeRequest,
    *,
    model: SemanticModel,
    registry: SourceRegistry,
    mode: str,
    landed_accounts: list[str] | None = None,
    organization_roster: list[str] | None = None,
) -> ScopeVerdict:
    """Can this metric be answered at this scope, in this mode?

    ``organization_roster`` is every account the organization contains, which
    `ORGANIZATION_USAGE` knows even for accounts that have never uploaded their
    own detail. Without it the platform cannot tell a complete roll-up from a
    partial one, and says nothing rather than guessing.
    """
    entity = model.entity(metric.entity)
    has_account = ACCOUNT_DIMENSION in entity.dimension_names
    organization_only = metric_is_organization_only(metric, registry)

    if request.scope is Scope.ACCOUNT:
        if organization_only:
            return ScopeVerdict.no(
                f"{metric.name} describes the whole organization — it comes from "
                f"{', '.join(sorted(metric.requires_sources))}, which has no per-account "
                "breakdown. Switch to organization scope to see it."
            )
        if not has_account:
            return ScopeVerdict.no(
                f"{metric.name} cannot be narrowed to one account: {entity.id} does not "
                "record which account its rows came from."
            )
        return ScopeVerdict.ok()

    # Organization scope.
    reads_organization_sources = any(
        registry.get(source).is_organization_scoped for source in metric.requires_sources
    )
    if reads_organization_sources:
        # ORGANIZATION_USAGE is organization-wide by construction, whichever
        # account's connection reads it.
        return ScopeVerdict.ok()

    if mode == "live":
        return ScopeVerdict.no(
            f"{metric.name} reads ACCOUNT_USAGE, which returns one account per "
            "connection. An organization figure would need one query per account and "
            "a merge — and averaging a rate or a percentile across accounts would be "
            "wrong — so select an account, or use an ORGANIZATION_USAGE metric."
        )

    # OFFLINE: one lake, every landed account, computed over the union of rows.
    # That is the organization's figure for the accounts present, which is the
    # whole organization only if every account has been uploaded.
    #
    # "Partial" means the platform can *name* an account it is missing, not
    # merely that a roll-up happened. Flagging every roll-up made the warning
    # permanent, and a warning that is always on is one nobody reads.
    missing = sorted(set(organization_roster or []) - set(landed_accounts or []))
    return ScopeVerdict.ok(partial=bool(missing), missing_accounts=missing)


__all__ = [
    "Scope",
    "ScopeRequest",
    "ScopeVerdict",
    "assess",
    "metric_is_organization_only",
]
