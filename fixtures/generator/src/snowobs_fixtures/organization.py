"""Organization-wide synthetic generation (BUILD_PROMPT §7.5).

An enterprise does not have one Snowflake account; it has a fleet of them under
one organization, and the platform's job is to roll them up. This module adds
that layer on top of the single-account generator without changing it: each
account is produced by the very same ``generate()`` call, with a profile that
bends its size, growth, workload mix, and tagging discipline.

The ORGANIZATION_USAGE views are then **derived from the per-account data**
rather than invented alongside it. That is the whole point: the org roll-up
reconciles to the accounts to the cent because it is computed from them, so a
reconciliation test is a test of the platform's arithmetic rather than of the
fixture's luck.

Money model (fixture convention, recorded in ASSUMPTIONS A-30):

* Everything except data transfer is denominated in **credits** in
  ``USAGE_IN_CURRENCY_DAILY.USAGE``, and ``RATE_SHEET_DAILY.EFFECTIVE_RATE``
  for those usage types is the account's currency-per-credit rate. Storage is
  therefore expressed in credits too, via
  ``STORAGE_DAILY_HISTORY.CREDITS`` — which is what that view's ``CREDITS``
  column carries.
* Data transfer is denominated in **terabytes**, and its effective rate is
  currency per terabyte.
* ``USAGE_IN_CURRENCY = USAGE x EFFECTIVE_RATE``, rounded half-up to the cent,
  for every row. Nothing is float anywhere on this path (§27.7).
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from snowobs_fixtures.account import build_account, days_iter
from snowobs_fixtures.config import ONE, GeneratorConfig, Scale
from snowobs_fixtures.generator import (
    GeneratedAccount,
    generate,
    write_manifest,
    write_tables_csv,
)
from snowobs_fixtures.ground_truth import (
    GroundTruth,
    Phenomenon,
    build_ground_truth,
)
from snowobs_fixtures.workload import warehouse_credits

CREDIT_PLACES = Decimal("0.000000001")
MONEY = Decimal("0.01")
BYTES_PER_TB = Decimal("1000000000000")  # Snowflake bills TB as 10^12 bytes
#: Credits per terabyte-day of average storage. 0.25 credits/TB/day is 7.5
#: credits per TB-month, i.e. $22.50/TB-month at a $3 credit — an on-demand
#: storage rate rounded to a figure the burn-down arithmetic stays exact on.
STORAGE_CREDITS_PER_TB_DAY = Decimal("0.25")

#: Currency per credit before regional uplift, by Snowflake service level.
EDITION_CREDIT_RATE: dict[str, Decimal] = {
    "Standard": Decimal("2.00"),
    "Enterprise": Decimal("3.00"),
    "Business Critical": Decimal("4.00"),
}

#: Regional multiplier on the credit rate. Distant regions really are dearer,
#: and this is what makes one account's effective rate an outlier.
REGION_RATE_UPLIFT: dict[str, Decimal] = {
    "AWS_EU_WEST_1": Decimal("1.00"),
    "AWS_US_EAST_1": Decimal("1.00"),
    "GCP_US_CENTRAL1": Decimal("1.05"),
    "AZURE_AUSTRALIAEAST": Decimal("1.18"),
}

#: Currency per terabyte of egress, by whether the target leaves the region
#: and/or the cloud provider.
EGRESS_RATE_SAME_REGION = Decimal("0.00")
EGRESS_RATE_CROSS_REGION = Decimal("20.00")
EGRESS_RATE_CROSS_CLOUD = Decimal("90.00")

#: (usage type, service type) pairs emitted into USAGE_IN_CURRENCY_DAILY.
USAGE_TYPE_COMPUTE = "compute"
USAGE_TYPE_CLOUD_SERVICES = "cloud services"
USAGE_TYPE_SERVERLESS = "serverless tasks"
USAGE_TYPE_AI = "ai services"
USAGE_TYPE_STORAGE = "storage"
USAGE_TYPE_TRANSFER = "data transfer"

CREDIT_USAGE_TYPES: tuple[tuple[str, str], ...] = (
    (USAGE_TYPE_COMPUTE, "WAREHOUSE_METERING"),
    (USAGE_TYPE_CLOUD_SERVICES, "WAREHOUSE_METERING"),
    (USAGE_TYPE_SERVERLESS, "SERVERLESS_TASK"),
    (USAGE_TYPE_AI, "AI_SERVICES"),
    (USAGE_TYPE_STORAGE, "STORAGE"),
)

#: Every ORGANIZATION_USAGE source this module lands, in registry ids.
ORGANIZATION_SOURCE_IDS: tuple[str, ...] = (
    "contract_items",
    "data_transfer_daily_history",
    "org_warehouse_metering_history",
    "rate_sheet_daily",
    "remaining_balance_daily",
    "storage_daily_history",
    "usage_in_currency_daily",
)

CONTRACT_ITEM_CAPACITY = "Capacity"
CONTRACT_ITEM_ROLLOVER = "Rollover"
CONTRACT_ITEM_FREE_USAGE = "Free Usage"

BALANCE_SOURCE_FREE = "free usage"
BALANCE_SOURCE_ROLLOVER = "rollover"
BALANCE_SOURCE_CAPACITY = "capacity"
BALANCE_SOURCE_ON_DEMAND = "on demand"


# --------------------------------------------------------------------- profiles
@dataclass(frozen=True)
class AccountProfile:
    """One Snowflake account's character within the organization.

    The profile is deliberately small: it says how big the account is, where it
    runs, what it is used for, how fast it is growing, and how well its spend is
    tagged. Everything else — the warehouse topology, the planted per-account
    phenomena, the query mix — comes from the shared single-account generator,
    so every account in the fleet is recognisably the same product.
    """

    name: str
    locator: str
    region: str
    cloud: str
    edition: str
    #: Relative compute size against the base profile (1.0 = the base account).
    scale_factor: Decimal
    #: Per-workload-class multipliers, e.g. ``(("bi", 1.6), ("elt", 0.5))``.
    workload_mix: tuple[tuple[str, float], ...] = ()
    #: Target share of metered credits that carries a team tag.
    tagged_spend_share: Decimal = Decimal("0.82")
    #: Compounding daily growth on metered compute (0 = flat).
    compute_growth_per_day: Decimal = Decimal("0")
    #: Offset added to the organization seed so accounts differ from each other.
    seed_offset: int = 0
    warehouses: int = 12
    teams: int = 8
    queries_per_day: int | None = None
    #: Average terabytes egressed per day, and where they go.
    egress_tb_per_day: Decimal = Decimal("0")
    egress_target_region: str | None = None
    egress_target_cloud: str | None = None

    @property
    def credit_rate(self) -> Decimal:
        """Currency per credit for this account, after regional uplift."""
        try:
            base = EDITION_CREDIT_RATE[self.edition]
        except KeyError:  # pragma: no cover - guarded by profile construction
            raise ValueError(f"Unknown Snowflake edition: {self.edition!r}") from None
        uplift = REGION_RATE_UPLIFT.get(self.region, Decimal("1.00"))
        return (base * uplift).quantize(Decimal("0.0001"))

    @property
    def egress_rate(self) -> Decimal:
        """Currency per terabyte of egress for this account's target."""
        if self.egress_tb_per_day <= 0 or self.egress_target_region is None:
            return EGRESS_RATE_SAME_REGION
        if self.egress_target_cloud and self.egress_target_cloud != self.cloud:
            return EGRESS_RATE_CROSS_CLOUD
        if self.egress_target_region != self.region:
            return EGRESS_RATE_CROSS_REGION
        return EGRESS_RATE_SAME_REGION


#: The shipped fleet: four accounts with genuinely different characters, on
#: three clouds and four regions.
ACCOUNT_PROD = AccountProfile(
    name="ACME_PROD",
    locator="AB12345",
    region="AWS_EU_WEST_1",
    cloud="AWS",
    edition="Enterprise",
    scale_factor=ONE,
    tagged_spend_share=Decimal("0.82"),
    seed_offset=0,
)
ACCOUNT_ANALYTICS = AccountProfile(
    name="ACME_ANALYTICS",
    locator="XY98765",
    region="AWS_US_EAST_1",
    cloud="AWS",
    edition="Enterprise",
    scale_factor=Decimal("0.42"),
    workload_mix=(("bi", 1.7), ("elt", 0.45), ("adhoc", 0.8), ("training", 0.2)),
    tagged_spend_share=Decimal("0.88"),
    compute_growth_per_day=Decimal("0.013"),  # the runaway
    seed_offset=101,
    egress_tb_per_day=Decimal("0.4"),
    egress_target_region="AWS_US_EAST_1",
    egress_target_cloud="AWS",
)
ACCOUNT_SANDBOX = AccountProfile(
    name="ACME_SANDBOX",
    locator="DV55501",
    region="GCP_US_CENTRAL1",
    cloud="GCP",
    edition="Standard",
    scale_factor=Decimal("0.11"),
    workload_mix=(("adhoc", 1.5), ("bi", 0.35), ("elt", 0.25), ("training", 0.1)),
    tagged_spend_share=Decimal("0.35"),  # nobody tags anything in the sandbox
    seed_offset=202,
    queries_per_day=600,
)
ACCOUNT_APAC = AccountProfile(
    name="ACME_APAC",
    locator="PQ33210",
    region="AZURE_AUSTRALIAEAST",
    cloud="AZURE",
    edition="Business Critical",
    scale_factor=Decimal("0.36"),
    workload_mix=(("elt", 1.25), ("bi", 0.7), ("training", 0.4)),
    tagged_spend_share=Decimal("0.80"),
    seed_offset=303,
    egress_tb_per_day=Decimal("3.2"),  # replicating back to the EU primary
    egress_target_region="AWS_EU_WEST_1",
    egress_target_cloud="AWS",
)

DEFAULT_ACCOUNT_PROFILES: tuple[AccountProfile, ...] = (
    ACCOUNT_PROD,
    ACCOUNT_ANALYTICS,
    ACCOUNT_SANDBOX,
    ACCOUNT_APAC,
)


@dataclass(frozen=True)
class OrganizationConfig:
    """Everything needed to generate a whole organization deterministically."""

    organization_name: str = "ACME_GROUP"
    contract_number: str = "CN-100042"
    currency: str = "USD"
    accounts: tuple[AccountProfile, ...] = DEFAULT_ACCOUNT_PROFILES
    seed: int = 42
    days: int = 120
    end_date: date = date(2026, 8, 20)
    scale: Scale = Scale.SMALL
    #: Capacity purchased as a multiple of the generated window's spend. Sized
    #: above the window's run rate so the contract strands (phenomenon).
    commitment_multiple: Decimal = Decimal("5.0")
    free_usage_amount: Decimal = Decimal("50000.00")
    rollover_amount: Decimal = Decimal("25000.00")
    contract_days: int = 365

    @property
    def start_date(self) -> date:
        return self.end_date - timedelta(days=self.days - 1)

    @property
    def contract_start(self) -> date:
        return self.start_date

    @property
    def contract_end(self) -> date:
        return self.contract_start + timedelta(days=self.contract_days - 1)

    def account_profile(self, name: str) -> AccountProfile:
        return next(p for p in self.accounts if p.name == name)

    def account_config(self, profile: AccountProfile) -> GeneratorConfig:
        """The single-account generator configuration for one profile."""
        base = GeneratorConfig(
            seed=self.seed + profile.seed_offset,
            days=self.days,
            warehouses=profile.warehouses,
            teams=profile.teams,
            scale=self.scale,
            queries_per_day=profile.queries_per_day,
            end_date=self.end_date,
            scale_factor=profile.scale_factor,
            compute_growth_per_day=profile.compute_growth_per_day,
            workload_mix=profile.workload_mix,
        )
        untagged = _warehouses_to_untag(base, ONE - profile.tagged_spend_share)
        if not untagged:
            return base
        return GeneratorConfig(
            seed=base.seed,
            days=base.days,
            warehouses=base.warehouses,
            teams=base.teams,
            scale=base.scale,
            queries_per_day=base.queries_per_day,
            end_date=base.end_date,
            credit_price_usd=base.credit_price_usd,
            scale_factor=base.scale_factor,
            compute_growth_per_day=base.compute_growth_per_day,
            workload_mix=base.workload_mix,
            untagged_warehouses=untagged,
        )


def _warehouses_to_untag(config: GeneratorConfig, target_untagged: Decimal) -> tuple[str, ...]:
    """Pick the warehouses whose owner team must be stripped to hit a target.

    Untagging a warehouse does not change what it costs — only whether the cost
    can be attributed — so the share is computed exactly from the metered
    credits the account is about to generate, largest warehouse first. If the
    base profile already exceeds the target, nothing is stripped.
    """
    if target_untagged <= 0:
        return ()
    account = build_account(config)
    ground_truth = build_ground_truth(config)
    days = days_iter(config)
    totals = {
        wh.name: sum(
            (
                warehouse_credits(wh, day, ground_truth, config.compute_multiplier(day))
                for day in days
            ),
            Decimal(0),
        )
        for wh in account.warehouses
    }
    total = sum(totals.values(), Decimal(0))
    if total <= 0:
        return ()
    untagged = sum(
        (totals[wh.name] for wh in account.warehouses if wh.owner_team is None), Decimal(0)
    )
    selected: list[str] = []
    candidates = sorted(
        (wh for wh in account.warehouses if wh.owner_team is not None),
        key=lambda wh: (-totals[wh.name], wh.name),
    )
    for wh in candidates:
        if untagged / total >= target_untagged:
            break
        selected.append(wh.name)
        untagged += totals[wh.name]
    return tuple(selected)


# ------------------------------------------------------------------- the result
class GeneratedOrganization:
    """In-memory result of an organization-wide generation run."""

    def __init__(
        self,
        config: OrganizationConfig,
        accounts: dict[str, GeneratedAccount],
        org_tables: dict[str, list[dict[str, Any]]],
        ground_truth: GroundTruth,
    ) -> None:
        self.config = config
        self.organization_name = config.organization_name
        self.accounts = accounts
        self.org_tables = org_tables
        self.ground_truth = ground_truth

    @property
    def account_names(self) -> list[str]:
        return list(self.accounts)

    @property
    def org_source_ids(self) -> list[str]:
        return sorted(self.org_tables)

    def org_row_count(self, source_id: str) -> int:
        return len(self.org_tables.get(source_id, []))

    def row_counts(self) -> dict[str, dict[str, int]]:
        """Rows per source, per account, plus the organization-scoped tables."""
        counts: dict[str, dict[str, int]] = {
            name: {source_id: account.row_count(source_id) for source_id in account.source_ids}
            for name, account in self.accounts.items()
        }
        counts[self.organization_name] = {
            source_id: self.org_row_count(source_id) for source_id in self.org_source_ids
        }
        return counts


# ------------------------------------------------------------- derived org views
def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _org_identity(config: OrganizationConfig, profile: AccountProfile) -> dict[str, str]:
    return {
        "ORGANIZATION_NAME": config.organization_name,
        "ACCOUNT_NAME": profile.name,
        "ACCOUNT_LOCATOR": profile.locator,
        "REGION": profile.region,
    }


def _org_warehouse_metering(
    config: OrganizationConfig, profile: AccountProfile, account: GeneratedAccount
) -> list[dict[str, Any]]:
    """The org roll-up of one account's WAREHOUSE_METERING_HISTORY.

    The credit columns are carried across verbatim, so the org total is the sum
    of the account totals by construction rather than by coincidence.
    """
    identity = _org_identity(config, profile)
    return [
        {
            **identity,
            "START_TIME": row["START_TIME"],
            "END_TIME": row["END_TIME"],
            "WAREHOUSE_ID": row["WAREHOUSE_ID"],
            "WAREHOUSE_NAME": row["WAREHOUSE_NAME"],
            "CREDITS_USED": row["CREDITS_USED"],
            "CREDITS_USED_COMPUTE": row["CREDITS_USED_COMPUTE"],
            "CREDITS_USED_CLOUD_SERVICES": row["CREDITS_USED_CLOUD_SERVICES"],
        }
        for row in account.tables["warehouse_metering_history"]
    ]


def account_storage_bytes(account: GeneratedAccount) -> dict[str, Decimal]:
    """Average stored bytes per day, from the account's own STORAGE_USAGE."""
    totals: dict[str, Decimal] = {}
    for row in account.tables["storage_usage"]:
        day = str(row["USAGE_DATE"])[:10]
        totals[day] = (
            _decimal(row["STORAGE_BYTES"])
            + _decimal(row["STAGE_BYTES"])
            + _decimal(row["FAILSAFE_BYTES"])
        ).quantize(MONEY)
    return totals


def _org_storage_daily(
    config: OrganizationConfig, profile: AccountProfile, account: GeneratedAccount
) -> list[dict[str, Any]]:
    identity = _org_identity(config, profile)
    rows: list[dict[str, Any]] = []
    for day, average_bytes in account_storage_bytes(account).items():
        terabytes = average_bytes / BYTES_PER_TB
        credits = (terabytes * STORAGE_CREDITS_PER_TB_DAY).quantize(CREDIT_PLACES)
        rows.append(
            {
                **identity,
                "USAGE_DATE": day,
                "AVERAGE_BYTES": str(average_bytes),
                "CREDITS": str(credits),
            }
        )
    return rows


def _org_data_transfer(
    config: OrganizationConfig, profile: AccountProfile, days: list[date]
) -> list[dict[str, Any]]:
    """Daily egress per account. One account carries almost all of it."""
    identity = _org_identity(config, profile)
    rows: list[dict[str, Any]] = []
    target_region = profile.egress_target_region or profile.region
    target_cloud = profile.egress_target_cloud or profile.cloud
    transfer_type = "REPLICATION" if profile.egress_target_region else "COPY"

    for day in days:
        if profile.egress_tb_per_day <= 0:
            continue
        rng = random.Random(  # noqa: S311 — synthetic data, not crypto
            f"{config.seed}:transfer:{profile.name}:{day.isoformat()}"
        )
        # Replication traffic follows the ELT window, so weekends are lighter.
        weekday_factor = Decimal("0.45") if day.weekday() >= 5 else ONE
        jitter = Decimal(str(round(rng.uniform(0.85, 1.15), 4)))
        terabytes = (profile.egress_tb_per_day * weekday_factor * jitter).quantize(
            Decimal("0.000001")
        )
        rows.append(
            {
                **identity,
                "USAGE_DATE": day.isoformat(),
                "TARGET_CLOUD": target_cloud,
                "TARGET_REGION": target_region,
                "TRANSFER_TYPE": transfer_type,
                "BYTES_TRANSFERRED": str((terabytes * BYTES_PER_TB).quantize(Decimal("1"))),
            }
        )
    return rows


def _rate_sheet(
    config: OrganizationConfig, profile: AccountProfile, days: list[date]
) -> list[dict[str, Any]]:
    """RATE_SHEET_DAILY — one row per account-day-usage-type."""
    rows: list[dict[str, Any]] = []
    credit_rate = profile.credit_rate
    egress_rate = profile.egress_rate
    entries: list[tuple[str, str, Decimal]] = [
        (usage_type, service_type, credit_rate) for usage_type, service_type in CREDIT_USAGE_TYPES
    ]
    entries.append((USAGE_TYPE_TRANSFER, "DATA_TRANSFER", egress_rate))

    for day in days:
        for usage_type, service_type, rate in entries:
            rows.append(
                {
                    "RATE_SHEET_DATE": day.isoformat(),
                    "ORGANIZATION_NAME": config.organization_name,
                    "CONTRACT_NUMBER": config.contract_number,
                    "ACCOUNT_NAME": profile.name,
                    "ACCOUNT_LOCATOR": profile.locator,
                    "REGION": profile.region,
                    "SERVICE_LEVEL": profile.edition,
                    "SERVICE_TYPE": service_type,
                    "USAGE_TYPE": usage_type,
                    "EFFECTIVE_RATE": str(rate),
                    "CURRENCY": config.currency,
                }
            )
    return rows


def account_credit_components(account: GeneratedAccount) -> dict[str, dict[str, Decimal]]:
    """Billed credits per day, split by usage type, from METERING_DAILY_HISTORY.

    Cloud services is the **net** figure (raw usage plus the negative
    adjustment), which is what the account is actually billed for and what the
    reconciliation gate reconciles against (ASSUMPTIONS §4, A-2).
    """
    per_day: dict[str, dict[str, Decimal]] = {}
    for row in account.tables["metering_daily_history"]:
        day = str(row["USAGE_DATE"])[:10]
        bucket = per_day.setdefault(day, {})
        service_type = str(row["SERVICE_TYPE"])
        if service_type == "WAREHOUSE_METERING":
            bucket[USAGE_TYPE_COMPUTE] = _decimal(row["CREDITS_USED_COMPUTE"])
            bucket[USAGE_TYPE_CLOUD_SERVICES] = _decimal(
                row["CREDITS_USED_CLOUD_SERVICES"]
            ) + _decimal(row["CREDITS_ADJUSTMENT_CLOUD_SERVICES"])
        elif service_type == "SERVERLESS_TASK":
            bucket[USAGE_TYPE_SERVERLESS] = _decimal(row["CREDITS_BILLED"])
        elif service_type == "AI_SERVICES":
            bucket[USAGE_TYPE_AI] = _decimal(row["CREDITS_BILLED"])
    return per_day


def _usage_in_currency(
    config: OrganizationConfig,
    profile: AccountProfile,
    account: GeneratedAccount,
    storage_rows: list[dict[str, Any]],
    transfer_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """USAGE_IN_CURRENCY_DAILY for one account, priced off the rate sheet."""
    identity = _org_identity(config, profile)
    credit_rate = profile.credit_rate
    egress_rate = profile.egress_rate
    components = account_credit_components(account)
    storage_credits = {str(r["USAGE_DATE"]): _decimal(r["CREDITS"]) for r in storage_rows}
    transfer_bytes = {str(r["USAGE_DATE"]): _decimal(r["BYTES_TRANSFERRED"]) for r in transfer_rows}

    rows: list[dict[str, Any]] = []
    for day in sorted(set(components) | set(storage_credits) | set(transfer_bytes)):
        priced: list[tuple[str, Decimal, Decimal]] = []
        for usage_type, _service_type in CREDIT_USAGE_TYPES:
            if usage_type == USAGE_TYPE_STORAGE:
                usage = storage_credits.get(day, Decimal(0))
            else:
                usage = components.get(day, {}).get(usage_type, Decimal(0))
            if usage > 0:
                priced.append((usage_type, usage, credit_rate))
        egress_tb = transfer_bytes.get(day, Decimal(0)) / BYTES_PER_TB
        if egress_tb > 0 and egress_rate > 0:
            priced.append(
                (USAGE_TYPE_TRANSFER, egress_tb.quantize(Decimal("0.000001")), egress_rate)
            )

        for usage_type, usage, rate in priced:
            rows.append(
                {
                    **identity,
                    "CONTRACT_NUMBER": config.contract_number,
                    "SERVICE_LEVEL": profile.edition,
                    "USAGE_DATE": day,
                    "USAGE_TYPE": usage_type,
                    "CURRENCY": config.currency,
                    "USAGE": str(usage),
                    "USAGE_IN_CURRENCY": str((usage * rate).quantize(MONEY, ROUND_HALF_UP)),
                    "BALANCE_SOURCE": BALANCE_SOURCE_CAPACITY,
                }
            )
    # Column order must match the registry's declared order for the extract to
    # read like a real one; identity keys come first above, so reshape here.
    return [
        {
            "ORGANIZATION_NAME": row["ORGANIZATION_NAME"],
            "CONTRACT_NUMBER": row["CONTRACT_NUMBER"],
            "ACCOUNT_NAME": row["ACCOUNT_NAME"],
            "ACCOUNT_LOCATOR": row["ACCOUNT_LOCATOR"],
            "REGION": row["REGION"],
            "SERVICE_LEVEL": row["SERVICE_LEVEL"],
            "USAGE_DATE": row["USAGE_DATE"],
            "USAGE_TYPE": row["USAGE_TYPE"],
            "CURRENCY": row["CURRENCY"],
            "USAGE": row["USAGE"],
            "USAGE_IN_CURRENCY": row["USAGE_IN_CURRENCY"],
            "BALANCE_SOURCE": row["BALANCE_SOURCE"],
        }
        for row in rows
    ]


def _round_up(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).quantize(Decimal("1"), ROUND_CEILING) * step


@dataclass(frozen=True)
class Commitment:
    """The capacity contract the organization draws down."""

    contract_number: str
    currency: str
    free_usage: Decimal
    rollover: Decimal
    capacity: Decimal
    start_date: date
    end_date: date

    @property
    def total(self) -> Decimal:
        return self.free_usage + self.rollover + self.capacity


def _build_commitment(config: OrganizationConfig, window_spend: Decimal) -> Commitment:
    """Size a commitment that the window's run rate will not consume.

    The contract is sized as a multiple of the generated window's spend and
    then rounded up to a round enterprise figure, so the burn-down is a real
    drawdown of a real number rather than a curve fitted to look like one.
    """
    total = _round_up(window_spend * config.commitment_multiple, Decimal("100000"))
    capacity = total - config.free_usage_amount - config.rollover_amount
    if capacity <= 0:  # pragma: no cover - only for absurdly short windows
        capacity = _round_up(window_spend, Decimal("100000"))
        total = capacity + config.free_usage_amount + config.rollover_amount
    return Commitment(
        contract_number=config.contract_number,
        currency=config.currency,
        free_usage=config.free_usage_amount.quantize(MONEY),
        rollover=config.rollover_amount.quantize(MONEY),
        capacity=capacity.quantize(MONEY),
        start_date=config.contract_start,
        end_date=config.contract_end,
    )


def _contract_items(config: OrganizationConfig, commitment: Commitment) -> list[dict[str, Any]]:
    items = (
        (CONTRACT_ITEM_CAPACITY, commitment.capacity),
        (CONTRACT_ITEM_ROLLOVER, commitment.rollover),
        (CONTRACT_ITEM_FREE_USAGE, commitment.free_usage),
    )
    return [
        {
            "ORGANIZATION_NAME": config.organization_name,
            "CONTRACT_NUMBER": commitment.contract_number,
            "CONTRACT_ITEM": item,
            "AMOUNT": str(amount),
            "CURRENCY": commitment.currency,
            "START_DATE": commitment.start_date.isoformat(),
            "END_DATE": commitment.end_date.isoformat(),
        }
        for item, amount in items
    ]


def balance_source_for(commitment: Commitment, drawn_before: Decimal) -> str:
    """Which balance the next dollar of spend comes out of."""
    if drawn_before < commitment.free_usage:
        return BALANCE_SOURCE_FREE
    if drawn_before < commitment.free_usage + commitment.rollover:
        return BALANCE_SOURCE_ROLLOVER
    if drawn_before < commitment.total:
        return BALANCE_SOURCE_CAPACITY
    return BALANCE_SOURCE_ON_DEMAND


def _remaining_balance(
    config: OrganizationConfig,
    commitment: Commitment,
    daily_spend: dict[str, Decimal],
    days: list[date],
) -> list[dict[str, Any]]:
    """REMAINING_BALANCE_DAILY as an exact drawdown of the commitment.

    Buckets are consumed in order — free usage, then rollover, then capacity,
    then on demand — and every day's four balances satisfy
    ``free + rollover + capacity - on_demand == commitment.total - cumulative``
    to the cent. A burn-down that does not tie to spend would be worse than
    none at all, so this is asserted rather than assumed.
    """
    rows: list[dict[str, Any]] = []
    cumulative = Decimal("0.00")
    for day in days:
        cumulative += daily_spend.get(day.isoformat(), Decimal("0.00"))
        after_free = max(cumulative - commitment.free_usage, Decimal("0.00"))
        after_rollover = max(after_free - commitment.rollover, Decimal("0.00"))
        after_capacity = max(after_rollover - commitment.capacity, Decimal("0.00"))
        rows.append(
            {
                "DATE": day.isoformat(),
                "ORGANIZATION_NAME": config.organization_name,
                "CONTRACT_NUMBER": commitment.contract_number,
                "CURRENCY": commitment.currency,
                "FREE_USAGE_BALANCE": str(max(commitment.free_usage - cumulative, Decimal("0.00"))),
                "CAPACITY_BALANCE": str(max(commitment.capacity - after_rollover, Decimal("0.00"))),
                "ON_DEMAND_CONSUMPTION_BALANCE": str(after_capacity),
                "ROLLOVER_BALANCE": str(max(commitment.rollover - after_free, Decimal("0.00"))),
            }
        )
    return rows


# ------------------------------------------------------------ planted phenomena
#: Thresholds the generated fleet clears with margin. They are expectations a
#: detector must meet, not descriptions of the data, so they sit below what the
#: fixture actually produces.
RUNAWAY_MIN_GROWTH_RATIO = 2.5
RUNAWAY_MAX_PEER_RATIO = 1.6
STRANDED_MIN_UNUSED_PCT = 15.0
EGRESS_MIN_SHARE_OF_ORG_BYTES = 0.75
UNTAGGED_ACCOUNT_MIN_PCT = 45.0
UNTAGGED_PEER_MAX_PCT = 30.0
RATE_OUTLIER_MIN_PREMIUM = 1.25


def build_organization_ground_truth(
    config: OrganizationConfig, commitment: Commitment
) -> GroundTruth:
    """Declare the organization-wide phenomena the platform must detect."""
    start, end = config.start_date, config.end_date
    comparison_window = min(14, max(config.days // 4, 1))

    runaway = max(config.accounts, key=lambda p: p.compute_growth_per_day)
    egress = max(config.accounts, key=lambda p: p.egress_tb_per_day)
    worst_tagged = min(config.accounts, key=lambda p: p.tagged_spend_share)
    dearest = max(config.accounts, key=lambda p: p.credit_rate)

    return GroundTruth(
        seed=config.seed,
        days=config.days,
        start_date=start,
        end_date=end,
        phenomena=[
            Phenomenon(
                id="org-runaway-account",
                kind="runaway_account",
                description=(
                    f"{runaway.name} compounds its compute spend while every other account "
                    "in the organization stays flat."
                ),
                subjects=[runaway.name],
                window_start=start,
                window_end=end,
                expectations={
                    "min_growth_ratio": RUNAWAY_MIN_GROWTH_RATIO,
                    "max_peer_growth_ratio": RUNAWAY_MAX_PEER_RATIO,
                    "comparison_window_days": comparison_window,
                },
            ),
            Phenomenon(
                id="org-stranded-commitment",
                kind="stranded_commitment",
                description=(
                    "The capacity contract will not be consumed before it expires: at the "
                    "observed run rate a material share of the commitment is stranded."
                ),
                subjects=[commitment.contract_number],
                window_start=commitment.start_date,
                window_end=commitment.end_date,
                expectations={
                    "min_unused_pct_at_expiry": STRANDED_MIN_UNUSED_PCT,
                    "commitment_total": str(commitment.total),
                    "contract_days": config.contract_days,
                },
            ),
            Phenomenon(
                id="org-cross-region-egress",
                kind="cross_region_egress",
                description=(
                    f"{egress.name} replicates across cloud and region, concentrating the "
                    "organization's data-transfer cost on one account."
                ),
                subjects=[egress.name],
                window_start=start,
                window_end=end,
                expectations={
                    "min_share_of_org_bytes": EGRESS_MIN_SHARE_OF_ORG_BYTES,
                    "target_region": egress.egress_target_region or egress.region,
                    "target_cloud": egress.egress_target_cloud or egress.cloud,
                },
            ),
            Phenomenon(
                id="org-untagged-account",
                kind="account_untagged_spend",
                description=(
                    f"{worst_tagged.name} has materially worse tagging discipline than every "
                    "other account: most of its metered credits cannot be charged back."
                ),
                subjects=[worst_tagged.name],
                window_start=start,
                window_end=end,
                expectations={
                    "min_untagged_pct": UNTAGGED_ACCOUNT_MIN_PCT,
                    "max_peer_untagged_pct": UNTAGGED_PEER_MAX_PCT,
                },
            ),
            Phenomenon(
                id="org-effective-rate-outlier",
                kind="effective_rate_outlier",
                description=(
                    f"{dearest.name} pays a materially higher effective rate per credit than "
                    "its peers — a different service level in a dearer region."
                ),
                subjects=[dearest.name],
                window_start=start,
                window_end=end,
                expectations={
                    "min_premium_vs_median": RATE_OUTLIER_MIN_PREMIUM,
                    "service_level": dearest.edition,
                    "region": dearest.region,
                },
            ),
        ],
    )


# ------------------------------------------------------------------- generation
def generate_organization(config: OrganizationConfig | None = None) -> GeneratedOrganization:
    """Generate a whole organization: every account, plus the org roll-ups."""
    config = config or OrganizationConfig()
    if not config.accounts:
        raise ValueError("An organization needs at least one account profile.")

    accounts: dict[str, GeneratedAccount] = {}
    for profile in config.accounts:
        accounts[profile.name] = generate(config.account_config(profile))

    days = [config.start_date + timedelta(days=i) for i in range(config.days)]
    org_tables: dict[str, list[dict[str, Any]]] = {
        source_id: [] for source_id in ORGANIZATION_SOURCE_IDS
    }

    usage_rows: list[dict[str, Any]] = []
    for profile in config.accounts:
        account = accounts[profile.name]
        org_tables["org_warehouse_metering_history"].extend(
            _org_warehouse_metering(config, profile, account)
        )
        storage = _org_storage_daily(config, profile, account)
        org_tables["storage_daily_history"].extend(storage)
        transfer = _org_data_transfer(config, profile, days)
        org_tables["data_transfer_daily_history"].extend(transfer)
        org_tables["rate_sheet_daily"].extend(_rate_sheet(config, profile, days))
        usage_rows.extend(_usage_in_currency(config, profile, account, storage, transfer))

    daily_spend: dict[str, Decimal] = {}
    for row in usage_rows:
        usage_date = str(row["USAGE_DATE"])
        daily_spend[usage_date] = daily_spend.get(usage_date, Decimal("0.00")) + _decimal(
            row["USAGE_IN_CURRENCY"]
        )
    window_spend = sum(daily_spend.values(), Decimal("0.00"))

    commitment = _build_commitment(config, window_spend)

    # The balance source of a day's usage depends on how much of the contract
    # was already drawn, so it is stamped once the commitment exists.
    drawn = Decimal("0.00")
    source_by_day: dict[str, str] = {}
    for day in days:
        key = day.isoformat()
        source_by_day[key] = balance_source_for(commitment, drawn)
        drawn += daily_spend.get(key, Decimal("0.00"))
    for row in usage_rows:
        row["BALANCE_SOURCE"] = source_by_day.get(str(row["USAGE_DATE"]), BALANCE_SOURCE_CAPACITY)

    org_tables["usage_in_currency_daily"] = usage_rows
    org_tables["contract_items"] = _contract_items(config, commitment)
    org_tables["remaining_balance_daily"] = _remaining_balance(
        config, commitment, daily_spend, days
    )

    ground_truth = build_organization_ground_truth(config, commitment)
    return GeneratedOrganization(config, accounts, org_tables, ground_truth)


# ---------------------------------------------------------------------- writing
@dataclass(frozen=True)
class OrganizationLayout:
    """Where an organization's extracts landed on disk.

    The layout mirrors how an enterprise actually exports: one directory per
    account for its ACCOUNT_USAGE extracts, and one directory for the
    organization account's ORGANIZATION_USAGE extracts.
    """

    root: Path
    organization_dir: Path
    account_dirs: dict[str, Path] = field(default_factory=dict)
    organization_files: dict[str, Path] = field(default_factory=dict)
    account_files: dict[str, dict[str, Path]] = field(default_factory=dict)

    def directories(self) -> dict[str, Path]:
        """Every extract directory, keyed by the account it belongs to."""
        return dict(self.account_dirs)


ACCOUNTS_SUBDIR = "accounts"
ORGANIZATION_SUBDIR = "organization"


def write_organization_csv(
    organization: GeneratedOrganization, output_dir: Path, *, compress: bool = False
) -> OrganizationLayout:
    """Write an organization's extracts as one directory per account plus one
    directory for the organization-scoped views."""
    output_dir.mkdir(parents=True, exist_ok=True)
    accounts_root = output_dir / ACCOUNTS_SUBDIR
    organization_dir = output_dir / ORGANIZATION_SUBDIR

    account_dirs: dict[str, Path] = {}
    account_files: dict[str, dict[str, Path]] = {}
    for name, account in organization.accounts.items():
        directory = accounts_root / name
        # An account's export directory holds ACCOUNT_USAGE only. The
        # single-account generator also emits USAGE_IN_CURRENCY_DAILY, but that
        # is an ORGANIZATION_USAGE view: in a real fleet it is exported once,
        # from the organization account, and it names every account in its own
        # schema. Writing it into each account's directory as well would land
        # four contradictory copies of the same organization-wide table.
        account_tables = {
            source_id: rows
            for source_id, rows in account.tables.items()
            if source_id not in ORGANIZATION_SOURCE_IDS
        }
        written = write_tables_csv(account_tables, directory, compress=compress)
        write_manifest(directory, written, account_tables, account.ground_truth)
        account_dirs[name] = directory
        account_files[name] = written

    org_written = write_tables_csv(organization.org_tables, organization_dir, compress=compress)
    write_manifest(
        organization_dir,
        org_written,
        organization.org_tables,
        organization.ground_truth,
        extra={
            "organization_name": organization.organization_name,
            "scope": "organization",
            "accounts": sorted(organization.accounts),
        },
    )

    index = {
        "generated_by": "snowobs-fixtures",
        "organization_name": organization.organization_name,
        "contract_number": organization.config.contract_number,
        "currency": organization.config.currency,
        "seed": organization.config.seed,
        "window": {
            "start": organization.config.start_date.isoformat(),
            "end": organization.config.end_date.isoformat(),
        },
        "organization_extracts": ORGANIZATION_SUBDIR,
        "accounts": [
            {
                "account_name": profile.name,
                "account_locator": profile.locator,
                "region": profile.region,
                "cloud": profile.cloud,
                "service_level": profile.edition,
                "directory": f"{ACCOUNTS_SUBDIR}/{profile.name}",
                "rows": sum(
                    organization.accounts[profile.name].row_count(source_id)
                    for source_id in organization.accounts[profile.name].source_ids
                ),
            }
            for profile in organization.config.accounts
        ],
    }
    (output_dir / "organization.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8"
    )

    return OrganizationLayout(
        root=output_dir,
        organization_dir=organization_dir,
        account_dirs=account_dirs,
        organization_files=org_written,
        account_files=account_files,
    )


def summarise_organization(organization: GeneratedOrganization) -> dict[str, int]:
    """Total rows per account, plus the organization-scoped total."""
    summary = {
        name: sum(account.row_count(s) for s in account.source_ids)
        for name, account in organization.accounts.items()
    }
    summary[organization.organization_name] = sum(
        organization.org_row_count(s) for s in organization.org_source_ids
    )
    return summary


def organization_credit_totals(organization: GeneratedOrganization) -> dict[str, Decimal]:
    """Metered compute credits per account, summed from ACCOUNT_USAGE."""
    return {
        name: sum(
            (
                _decimal(row["CREDITS_USED_COMPUTE"])
                for row in account.tables["warehouse_metering_history"]
            ),
            Decimal(0),
        )
        for name, account in organization.accounts.items()
    }


def sum_column(rows: Iterable[dict[str, Any]], column: str) -> Decimal:
    """Exact Decimal sum of a credit or currency column (never float, §27.7)."""
    return sum((_decimal(row[column]) for row in rows), Decimal(0))


__all__ = [
    "ACCOUNTS_SUBDIR",
    "DEFAULT_ACCOUNT_PROFILES",
    "ORGANIZATION_SOURCE_IDS",
    "ORGANIZATION_SUBDIR",
    "AccountProfile",
    "Commitment",
    "GeneratedOrganization",
    "OrganizationConfig",
    "OrganizationLayout",
    "account_credit_components",
    "account_storage_bytes",
    "balance_source_for",
    "build_organization_ground_truth",
    "generate_organization",
    "organization_credit_totals",
    "sum_column",
    "summarise_organization",
    "write_organization_csv",
]
