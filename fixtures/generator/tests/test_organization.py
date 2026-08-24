"""The organization roll-up must reconcile to its accounts, to the cent.

An organization-wide figure that does not tie back to the accounts it came from
is worse than no figure at all: it is a number a FinOps analyst will act on and
cannot defend. These tests assert exact Decimal equality — never a tolerance —
because the org views here are *derived* from the per-account data, so any
difference is an arithmetic bug rather than a rounding artefact.
"""

from __future__ import annotations

import hashlib
import itertools
import statistics
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from snowobs_fixtures.config import GeneratorConfig
from snowobs_fixtures.generator import GeneratedAccount, generate, write_csv
from snowobs_fixtures.organization import (
    DEFAULT_ACCOUNT_PROFILES,
    ORGANIZATION_SOURCE_IDS,
    USAGE_TYPE_COMPUTE,
    USAGE_TYPE_TRANSFER,
    AccountProfile,
    GeneratedOrganization,
    OrganizationConfig,
    account_storage_bytes,
    generate_organization,
    organization_credit_totals,
    summarise_organization,
    write_organization_csv,
)
from snowobs_semantics.registry import SourceScope, default_registry

#: A full-length window (the phenomena are calibrated for 120 days) with a thin
#: query volume, so the fleet generates in a couple of seconds.
ORG_CONFIG = OrganizationConfig(
    days=120,
    accounts=tuple(replace(p, queries_per_day=150) for p in DEFAULT_ACCOUNT_PROFILES),
)


@pytest.fixture(scope="module")
def organization() -> GeneratedOrganization:
    return generate_organization(ORG_CONFIG)


def _sum(rows: list[dict[str, object]], column: str) -> Decimal:
    return sum((Decimal(str(r[column])) for r in rows), Decimal(0))


def _where(rows: list[dict[str, object]], **equals: str) -> list[dict[str, object]]:
    return [r for r in rows if all(str(r[k]) == v for k, v in equals.items())]


# --------------------------------------------------------------- determinism
def test_organization_generation_is_deterministic() -> None:
    config = OrganizationConfig(
        days=21, accounts=tuple(replace(p, queries_per_day=60) for p in DEFAULT_ACCOUNT_PROFILES)
    )
    first, second = generate_organization(config), generate_organization(config)
    assert first.org_tables == second.org_tables
    assert first.accounts.keys() == second.accounts.keys()
    for name in first.accounts:
        assert first.accounts[name].tables == second.accounts[name].tables, name


def test_the_fleet_spans_clouds_regions_and_editions(organization: GeneratedOrganization) -> None:
    profiles = organization.config.accounts
    assert len(profiles) >= 4
    assert len({p.region for p in profiles}) == len(profiles)  # no two share a region
    assert len({p.cloud for p in profiles}) >= 3
    assert len({p.edition for p in profiles}) >= 2
    assert len({p.locator for p in profiles}) == len(profiles)


# ----------------------------------------------------------- schema fidelity
def test_every_org_source_is_registered_and_column_faithful(
    organization: GeneratedOrganization,
) -> None:
    registry = default_registry()
    for source_id, rows in organization.org_tables.items():
        source = registry.get(source_id)  # raises if unregistered
        assert source.scope is SourceScope.ORGANIZATION, source_id
        assert rows, f"{source_id} generated no rows"
        produced = {c.upper() for c in rows[0]}
        declared = {c.name.upper() for c in source.columns}
        assert produced <= declared, f"{source_id} emits undeclared columns: {produced - declared}"
        required = {c.name.upper() for c in source.required_columns}
        assert required <= produced, f"{source_id} missing required: {required - produced}"


def test_registry_marks_exactly_the_organization_usage_views(
    organization: GeneratedOrganization,
) -> None:
    registry = default_registry()
    org_scoped = {s.id for s in registry.scoped(SourceScope.ORGANIZATION)}
    assert org_scoped == set(ORGANIZATION_SOURCE_IDS)
    for source in registry.scoped(SourceScope.ORGANIZATION):
        assert ".ORGANIZATION_USAGE." in source.snowflake_object
    for source in registry.scoped(SourceScope.ACCOUNT):
        assert ".ORGANIZATION_USAGE." not in source.snowflake_object


# ------------------------------------------------------------ reconciliation
def test_org_warehouse_metering_reconciles_to_every_account(
    organization: GeneratedOrganization,
) -> None:
    org_rows = organization.org_tables["org_warehouse_metering_history"]
    per_account = organization_credit_totals(organization)

    for name, account in organization.accounts.items():
        account_total = _sum(account.tables["warehouse_metering_history"], "CREDITS_USED_COMPUTE")
        org_total = _sum(_where(org_rows, ACCOUNT_NAME=name), "CREDITS_USED_COMPUTE")
        assert org_total == account_total, name  # exact, not approximate
        assert org_total == per_account[name]

    assert _sum(org_rows, "CREDITS_USED_COMPUTE") == sum(per_account.values(), Decimal(0))
    # The cloud-services and total columns must travel across untouched too.
    for column in ("CREDITS_USED", "CREDITS_USED_CLOUD_SERVICES"):
        expected = sum(
            (
                _sum(a.tables["warehouse_metering_history"], column)
                for a in organization.accounts.values()
            ),
            Decimal(0),
        )
        assert _sum(org_rows, column) == expected, column


def test_org_warehouse_metering_carries_one_row_per_account_row(
    organization: GeneratedOrganization,
) -> None:
    org_rows = organization.org_tables["org_warehouse_metering_history"]
    expected = sum(
        a.row_count("warehouse_metering_history") for a in organization.accounts.values()
    )
    assert len(org_rows) == expected
    assert {str(r["ACCOUNT_NAME"]) for r in org_rows} == set(organization.accounts)


def test_org_storage_reconciles_to_per_account_storage(
    organization: GeneratedOrganization,
) -> None:
    org_rows = organization.org_tables["storage_daily_history"]
    for name, account in organization.accounts.items():
        expected = account_storage_bytes(account)
        landed = {
            str(r["USAGE_DATE"]): Decimal(str(r["AVERAGE_BYTES"]))
            for r in _where(org_rows, ACCOUNT_NAME=name)
        }
        assert landed == expected, name  # exact, day for day


def test_usage_in_currency_equals_usage_times_the_rate_sheet(
    organization: GeneratedOrganization,
) -> None:
    rates = {
        (str(r["ACCOUNT_LOCATOR"]), str(r["RATE_SHEET_DATE"]), str(r["USAGE_TYPE"])): Decimal(
            str(r["EFFECTIVE_RATE"])
        )
        for r in organization.org_tables["rate_sheet_daily"]
    }
    rows = organization.org_tables["usage_in_currency_daily"]
    assert rows
    for row in rows:
        key = (str(row["ACCOUNT_LOCATOR"]), str(row["USAGE_DATE"]), str(row["USAGE_TYPE"]))
        rate = rates[key]  # KeyError here means a priced row has no published rate
        usage = Decimal(str(row["USAGE"]))
        expected = (usage * rate).quantize(Decimal("0.01"))
        assert Decimal(str(row["USAGE_IN_CURRENCY"])) == expected, key


def test_usage_in_currency_compute_matches_metered_credits(
    organization: GeneratedOrganization,
) -> None:
    rows = organization.org_tables["usage_in_currency_daily"]
    for name, account in organization.accounts.items():
        billed = {
            str(r["USAGE_DATE"]): Decimal(str(r["CREDITS_USED_COMPUTE"]))
            for r in account.tables["metering_daily_history"]
            if r["SERVICE_TYPE"] == "WAREHOUSE_METERING"
        }
        priced = {
            str(r["USAGE_DATE"]): Decimal(str(r["USAGE"]))
            for r in rows
            if str(r["ACCOUNT_NAME"]) == name and str(r["USAGE_TYPE"]) == USAGE_TYPE_COMPUTE
        }
        assert priced == billed, name


def test_no_credit_or_currency_value_is_a_float(organization: GeneratedOrganization) -> None:
    # §27.7: credits and currency are Decimal strings end to end.
    money_columns = {
        "usage_in_currency_daily": ("USAGE", "USAGE_IN_CURRENCY"),
        "rate_sheet_daily": ("EFFECTIVE_RATE",),
        "storage_daily_history": ("CREDITS",),
        "contract_items": ("AMOUNT",),
        "remaining_balance_daily": (
            "FREE_USAGE_BALANCE",
            "CAPACITY_BALANCE",
            "ROLLOVER_BALANCE",
            "ON_DEMAND_CONSUMPTION_BALANCE",
        ),
        "org_warehouse_metering_history": ("CREDITS_USED", "CREDITS_USED_COMPUTE"),
    }
    for source_id, columns in money_columns.items():
        for row in organization.org_tables[source_id]:
            for column in columns:
                value = row[column]
                assert isinstance(value, str), (source_id, column)
                assert "e" not in value.lower(), (source_id, column, value)
                Decimal(value)  # raises if it is not an exact decimal literal


# ------------------------------------------------------------- the burn-down
def test_contract_items_sum_to_the_commitment(organization: GeneratedOrganization) -> None:
    items = {
        str(r["CONTRACT_ITEM"]): Decimal(str(r["AMOUNT"]))
        for r in organization.org_tables["contract_items"]
    }
    assert set(items) == {"Capacity", "Rollover", "Free Usage"}
    assert all(amount > 0 for amount in items.values())
    contract_numbers = {
        str(r["CONTRACT_NUMBER"]) for r in organization.org_tables["contract_items"]
    }
    assert contract_numbers == {organization.config.contract_number}


def test_remaining_balance_is_the_commitment_minus_cumulative_spend(
    organization: GeneratedOrganization,
) -> None:
    commitment = sum(
        (Decimal(str(r["AMOUNT"])) for r in organization.org_tables["contract_items"]), Decimal(0)
    )
    daily_spend: dict[str, Decimal] = defaultdict(Decimal)
    for row in organization.org_tables["usage_in_currency_daily"]:
        daily_spend[str(row["USAGE_DATE"])] += Decimal(str(row["USAGE_IN_CURRENCY"]))

    cumulative = Decimal("0.00")
    balances = organization.org_tables["remaining_balance_daily"]
    assert len(balances) == organization.config.days
    for row in balances:
        cumulative += daily_spend[str(row["DATE"])]
        remaining = (
            Decimal(str(row["FREE_USAGE_BALANCE"]))
            + Decimal(str(row["ROLLOVER_BALANCE"]))
            + Decimal(str(row["CAPACITY_BALANCE"]))
            - Decimal(str(row["ON_DEMAND_CONSUMPTION_BALANCE"]))
        )
        assert remaining == commitment - cumulative, row["DATE"]  # to the cent


def test_the_burn_down_draws_free_then_rollover_then_capacity(
    organization: GeneratedOrganization,
) -> None:
    balances = organization.org_tables["remaining_balance_daily"]
    free = [Decimal(str(r["FREE_USAGE_BALANCE"])) for r in balances]
    rollover = [Decimal(str(r["ROLLOVER_BALANCE"])) for r in balances]
    capacity = [Decimal(str(r["CAPACITY_BALANCE"])) for r in balances]

    # Monotonically non-increasing: a balance never refills mid-contract.
    for series in (free, rollover, capacity):
        assert all(b <= a for a, b in itertools.pairwise(series))
    # Free usage is exhausted before capacity is touched at all.
    first_capacity_draw = next(i for i, value in enumerate(capacity) if value < capacity[0])
    assert free[first_capacity_draw] == 0
    assert rollover[first_capacity_draw] == 0
    # And nothing goes on demand inside the observed window.
    assert all(Decimal(str(r["ON_DEMAND_CONSUMPTION_BALANCE"])) == 0 for r in balances)


# ------------------------------------------------------ planted org phenomena
def _account_daily_spend(organization: GeneratedOrganization) -> dict[str, dict[str, Decimal]]:
    totals: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for row in organization.org_tables["usage_in_currency_daily"]:
        totals[str(row["ACCOUNT_NAME"])][str(row["USAGE_DATE"])] += Decimal(
            str(row["USAGE_IN_CURRENCY"])
        )
    return totals


def test_one_account_runs_away_while_the_others_stay_flat(
    organization: GeneratedOrganization,
) -> None:
    phenomenon = organization.ground_truth.get("org-runaway-account")
    window = int(phenomenon.expectations["comparison_window_days"])
    spend = _account_daily_spend(organization)
    days = sorted({day for account in spend.values() for day in account})
    first, last = set(days[:window]), set(days[-window:])

    ratios: dict[str, Decimal] = {}
    for name, by_day in spend.items():
        opening = sum((v for d, v in by_day.items() if d in first), Decimal(0))
        closing = sum((v for d, v in by_day.items() if d in last), Decimal(0))
        assert opening > 0, name
        ratios[name] = closing / opening

    runaway = phenomenon.subjects[0]
    assert ratios[runaway] >= Decimal(str(phenomenon.expectations["min_growth_ratio"]))
    peer_ceiling = Decimal(str(phenomenon.expectations["max_peer_growth_ratio"]))
    for name, ratio in ratios.items():
        if name != runaway:
            assert ratio <= peer_ceiling, name
    assert max(ratios, key=lambda k: ratios[k]) == runaway


def test_the_capacity_contract_will_strand_before_it_expires(
    organization: GeneratedOrganization,
) -> None:
    phenomenon = organization.ground_truth.get("org-stranded-commitment")
    commitment = Decimal(str(phenomenon.expectations["commitment_total"]))
    contract_days = int(phenomenon.expectations["contract_days"])
    assert phenomenon.subjects == [organization.config.contract_number]
    assert commitment == sum(
        (Decimal(str(r["AMOUNT"])) for r in organization.org_tables["contract_items"]), Decimal(0)
    )

    spend = _account_daily_spend(organization)
    by_day: dict[str, Decimal] = defaultdict(Decimal)
    for account in spend.values():
        for day, value in account.items():
            by_day[day] += value
    days = sorted(by_day)
    consumed = sum(by_day.values(), Decimal(0))
    # Project the remaining contract at the *trailing* run rate, which is the
    # least favourable honest projection: it already includes the runaway.
    trailing = sum((by_day[d] for d in days[-30:]), Decimal(0)) / 30
    projected = consumed + trailing * (contract_days - len(days))
    unused_pct = (commitment - projected) / commitment * 100

    assert projected < commitment
    assert unused_pct >= Decimal(str(phenomenon.expectations["min_unused_pct_at_expiry"]))


def test_cross_region_egress_is_concentrated_on_one_account(
    organization: GeneratedOrganization,
) -> None:
    phenomenon = organization.ground_truth.get("org-cross-region-egress")
    subject = phenomenon.subjects[0]
    rows = organization.org_tables["data_transfer_daily_history"]
    assert rows

    total = _sum(rows, "BYTES_TRANSFERRED")
    subject_rows = _where(rows, ACCOUNT_NAME=subject)
    share = _sum(subject_rows, "BYTES_TRANSFERRED") / total
    assert share >= Decimal(str(phenomenon.expectations["min_share_of_org_bytes"]))

    # It really is leaving the account's region and cloud.
    profile = organization.config.account_profile(subject)
    assert {str(r["TARGET_REGION"]) for r in subject_rows} == {
        str(phenomenon.expectations["target_region"])
    }
    assert str(phenomenon.expectations["target_region"]) != profile.region
    assert str(phenomenon.expectations["target_cloud"]) != profile.cloud

    # And it costs real money, concentrated on that account.
    egress_spend = {
        str(r["ACCOUNT_NAME"]): Decimal(str(r["USAGE_IN_CURRENCY"]))
        for r in organization.org_tables["usage_in_currency_daily"]
        if str(r["USAGE_TYPE"]) == USAGE_TYPE_TRANSFER
    }
    assert set(egress_spend) == {subject}


def test_one_account_has_far_worse_tagging_discipline(
    organization: GeneratedOrganization,
) -> None:
    phenomenon = organization.ground_truth.get("org-untagged-account")
    subject = phenomenon.subjects[0]

    untagged_pct: dict[str, Decimal] = {}
    for name, account in organization.accounts.items():
        untagged_warehouses = {w.name for w in account.account.warehouses if w.owner_team is None}
        total = Decimal(0)
        untagged = Decimal(0)
        for row in account.tables["warehouse_metering_history"]:
            credits = Decimal(str(row["CREDITS_USED_COMPUTE"]))
            total += credits
            if str(row["WAREHOUSE_NAME"]) in untagged_warehouses:
                untagged += credits
        assert total > 0, name
        untagged_pct[name] = untagged / total * 100

    assert untagged_pct[subject] >= Decimal(str(phenomenon.expectations["min_untagged_pct"]))
    ceiling = Decimal(str(phenomenon.expectations["max_peer_untagged_pct"]))
    for name, pct in untagged_pct.items():
        if name != subject:
            assert pct <= ceiling, name

    # The queries themselves genuinely carry no team tag.
    sandbox = organization.accounts[subject]
    untagged_warehouses = {w.name for w in sandbox.account.warehouses if w.owner_team is None}
    tagged_rows = [
        r
        for r in sandbox.tables["query_history"]
        if str(r["WAREHOUSE_NAME"]) in untagged_warehouses and r["QUERY_TAG"]
    ]
    assert tagged_rows == []


def test_one_account_pays_a_materially_worse_effective_rate(
    organization: GeneratedOrganization,
) -> None:
    phenomenon = organization.ground_truth.get("org-effective-rate-outlier")
    subject = phenomenon.subjects[0]

    # Effective rate on compute alone — mixing in per-terabyte egress would
    # measure the transfer phenomenon rather than the contracted rate.
    rates: dict[str, Decimal] = {}
    for name in organization.accounts:
        rows = [
            r
            for r in organization.org_tables["usage_in_currency_daily"]
            if str(r["ACCOUNT_NAME"]) == name and str(r["USAGE_TYPE"]) == USAGE_TYPE_COMPUTE
        ]
        rates[name] = _sum(rows, "USAGE_IN_CURRENCY") / _sum(rows, "USAGE")

    peers = sorted(rate for name, rate in rates.items() if name != subject)
    median = Decimal(str(statistics.median(peers)))
    premium = rates[subject] / median
    assert premium >= Decimal(str(phenomenon.expectations["min_premium_vs_median"]))
    assert max(rates, key=lambda k: rates[k]) == subject

    profile = organization.config.account_profile(subject)
    assert profile.edition == phenomenon.expectations["service_level"]
    assert profile.region == phenomenon.expectations["region"]


def test_every_org_phenomenon_has_subjects_and_a_window(
    organization: GeneratedOrganization,
) -> None:
    phenomena = organization.ground_truth.phenomena
    assert len(phenomena) >= 5
    kinds = {p.kind for p in phenomena}
    assert kinds == {
        "runaway_account",
        "stranded_commitment",
        "cross_region_egress",
        "account_untagged_spend",
        "effective_rate_outlier",
    }
    for phenomenon in phenomena:
        assert phenomenon.subjects
        assert phenomenon.window_start is not None
        assert phenomenon.window_end is not None
        assert phenomenon.window_start <= phenomenon.window_end
        assert phenomenon.expectations
        assert phenomenon.description


# ------------------------------------------------------------------- writing
def test_write_organization_lays_out_one_directory_per_account(
    organization: GeneratedOrganization, tmp_path: Path
) -> None:
    layout = write_organization_csv(organization, tmp_path)
    registry = default_registry()

    assert set(layout.account_dirs) == set(organization.accounts)
    assert (tmp_path / "organization.json").exists()
    for name, directory in layout.account_dirs.items():
        assert directory.is_dir()
        assert (directory / "03_manifest.json").exists()
        assert (directory / "ground_truth.json").exists()
        landed = {p.stem for p in directory.glob("*.csv")}
        assert landed, name
        # ACCOUNT_USAGE only: the organization-scoped views are exported once,
        # from the organization account, not four contradictory times.
        assert landed.isdisjoint(ORGANIZATION_SOURCE_IDS)
        for source_id in landed:
            assert registry.get(source_id).scope is SourceScope.ACCOUNT

    assert set(layout.organization_files) == set(ORGANIZATION_SOURCE_IDS)
    for source_id, path in layout.organization_files.items():
        match = registry.match_filename(path.name)
        assert match is not None and match.source_id == source_id


def test_summary_counts_every_account_and_the_organization(
    organization: GeneratedOrganization,
) -> None:
    summary = summarise_organization(organization)
    assert set(summary) == set(organization.accounts) | {organization.organization_name}
    assert all(count > 0 for count in summary.values())
    counts = organization.row_counts()
    assert counts[organization.organization_name]["contract_items"] == 3


# --------------------------------------------- backwards-compatibility guard
#: sha256 of every file ``write_csv`` produces for the two configurations the
#: rest of the suite is built on. Pinned so that a change to the organization
#: layer cannot quietly move the single-account fixture underneath ~700 tests
#: that assert against it. If this fails, the single-account generator changed:
#: either that was intended (and these digests are updated in the same commit,
#: with the reason in CHANGELOG.md) or it is the regression this guard exists
#: to catch.
PINNED_DIGESTS: dict[str, dict[str, str]] = {
    "default": {
        "03_manifest.json": "1971659f000afe6a875973d4af32fd57fc1c06afeea3ee15843c7718837fd13d",
        "cortex_functions_usage_history.csv": (
            "3b064de79fc6f29083396d28b7ff80a72fd62acf457e1e3fdcf5e90cd5fdd45f"
        ),
        "database_storage_usage_history.csv": (
            "4d38dba70b0ecc848adcc2ae43239150d1a620ea46907164edff068abfc77b10"
        ),
        "dynamic_table_refresh_history.csv": (
            "f0f06c88429ae06b989a739ebb84aec7a3ec320e425b460ab259a58fb5646288"
        ),
        "grants_to_users.csv": "e23470f33f2ead3e78cf61bfe867ad8aad822f71e6538637ab35ef3846ef2320",
        "ground_truth.json": "962267c4fb692aea3b1b0abb13688de9144050ff40cac0fd9616da33b1080d4e",
        "login_history.csv": "1ed61e2d4d34ace24534def9a118604cbafadcbc141434b3658919a0d85fc0db",
        "metering_daily_history.csv": (
            "58d8fa37e651890561b3654442022bd9340584a3eb3471158602a6ea6cac8617"
        ),
        "query_attribution_history.csv": (
            "6dc76dbe601ef53cefc22d6bcc8f9cc52ba51bde1aad6128e88bb1bf0097aa44"
        ),
        "query_history.csv": "d473ff0e3dfb7f10c66ecec0e4d421185450a1155b16a32b856b684f34c1092a",
        "serverless_task_history.csv": (
            "9fdf4b2065faa7ee34ec2d8830ab1239d609c8bd7b6299b0b71bbdb1610c95e4"
        ),
        "storage_usage.csv": "4437c2bc2ea2378c94ac0e6bed38ea1908946aef7392282ea784298147224e9b",
        "table_storage_metrics.csv": (
            "d48b463ad5c3040b77e67f1ad21948958e5316d0daff626f64f43be97e2017eb"
        ),
        "task_history.csv": "8f4a267ba1fb242fb2dfa90df04462d8cefa56f52e9eebbe778a1d2d1671d6e5",
        "usage_in_currency_daily.csv": (
            "1ea7c8830754df79b32d28e0f2f38130b6597472a9711e27d1e3f4c51e11f390"
        ),
        "users.csv": "9ac55eabcaefd2c83dcb8e9772d4bdcff4621ec513043f7f0f23e36344d450d2",
        "warehouse_metering_history.csv": (
            "90a7e1252c7a4d6c4449fb2c48714008fe53dcdd6c0ddbba2b8b2f9187bc8dac"
        ),
        "warehouses.csv": "4d5992696a63b32dfa2099e9f0b9b8346488fc475775f596cd5e74d7c580c2a5",
    },
    "small14": {
        "03_manifest.json": "3e3907810788e00910c46bd6fe90d6cf0a795bd40fa20782ad731f1d6a51f05d",
        "cortex_functions_usage_history.csv": (
            "71361a9b75cdae39f053b4308535eb0f907116ae8805340d1a3a11e2ce99a75d"
        ),
        "database_storage_usage_history.csv": (
            "d3a2bd7dd79ae84a06ca2e31f534f056f8107a0533785cce21b03edfba39ccd7"
        ),
        "dynamic_table_refresh_history.csv": (
            "7c8430da4c8d05ac78a9af91ef70d6400c11a2cf8a8ee2cdb2cd4b25fd70a971"
        ),
        "grants_to_users.csv": "a2c8da2f8d76b27a0a8416d663d0586142f2b316389a9d8d67b4dc6aecf40dfd",
        "ground_truth.json": "d23fe20e6622602d683dfed1cf7f9af7552560a42a56083add4aa1b3adef7b40",
        "login_history.csv": "cb292801e46e95dff692da8d22bda09b27c906e3f1d1151e9bc55506e6d70b79",
        "metering_daily_history.csv": (
            "5eeb03e54c68f802bfc60b8e8533be4132a814559d72ecfb43211491290f63fc"
        ),
        "query_attribution_history.csv": (
            "020243799f6b0816754b9c4f93bf36264eae5ef0e6a4eb50f79c97f03367a79b"
        ),
        "query_history.csv": "10cd38a2a9cb89d9ab7e4796cdbf686006803992bbda3cf987ffb4e9af8d9f2f",
        "serverless_task_history.csv": (
            "ade24d8d107e9685f83924499fbc67da8b01ee988ca45466c3a3a37f1a9f60d2"
        ),
        "storage_usage.csv": "c01158d61f8c174833ae66446fb29dd75123c9500e4c71986d49de41deb7b492",
        "table_storage_metrics.csv": (
            "5d594db2cc5b5260c4a7b08d0f1bcddbf42a3d60443f9fddadca97550b625943"
        ),
        "task_history.csv": "3901bcd7b8e24f734c1f398dead05c737296af94c8a2b3911e014356dc8416cd",
        "usage_in_currency_daily.csv": (
            "5a3094c5a606c8ebb7aa1d895b58ba7fc3a0de740eb3b07ffea2d14bf9aa968d"
        ),
        "users.csv": "d0b405fd058aca023a5712076d9b0274b687d36bf1a7d7fd2277d36f6755b60d",
        "warehouse_metering_history.csv": (
            "40939c12bc1d130ca61d5f08b8beb9404b8ef9caf41c704f26658d4f2a34885f"
        ),
        "warehouses.csv": "1d6eee761a7e380ca7a6ad1698e33b1f16562e742f39d9ffcb532e58e9427566",
    },
}

REGRESSION_CONFIGS: dict[str, GeneratorConfig] = {
    "default": GeneratorConfig(),
    "small14": GeneratorConfig(days=14, queries_per_day=400),
}


@pytest.mark.parametrize("label", sorted(PINNED_DIGESTS))
def test_single_account_extracts_are_byte_identical(label: str, tmp_path: Path) -> None:
    write_csv(generate(REGRESSION_CONFIGS[label]), tmp_path)
    produced = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(tmp_path.iterdir())
    }
    assert produced == PINNED_DIGESTS[label]


def test_generate_keeps_its_single_account_signature() -> None:
    # ~700 tests call generate(GeneratorConfig(...)) and read .tables/.ground_truth.
    generated = generate(GeneratorConfig(days=7, queries_per_day=50))
    assert isinstance(generated, GeneratedAccount)
    assert generated.ground_truth.days == 7
    assert "warehouse_metering_history" in generated.tables
    assert generate() is not None  # the no-argument form still works


def test_account_profile_knobs_are_neutral_by_default() -> None:
    # An organization profile that asks for nothing must produce the base
    # account exactly — otherwise "add a profile" silently rewrites the fixture.
    profile = AccountProfile(
        name="ACME_PROD",
        locator="AB12345",
        region="AWS_EU_WEST_1",
        cloud="AWS",
        edition="Enterprise",
        scale_factor=Decimal("1"),  # everything else left at the profile default
    )
    config = OrganizationConfig(days=14, accounts=(profile,)).account_config(profile)
    assert config.workload_mix == ()
    assert config.untagged_warehouses == ()
    assert config.compute_multiplier(config.start_date) == Decimal("1")
    baseline = GeneratorConfig(days=14, end_date=config.end_date)
    assert generate(config).tables == generate(baseline).tables
