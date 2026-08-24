"""Query, attribution, and warehouse-metering generation.

Credits are computed from an explicit model — running hours x credits/hour per
warehouse-day — and queries are attributed to a fraction of those credits, so
metered totals and attributed totals differ by a realistic idle share. That
relationship is what the allocation engine and the reconciliation gate are
tested against, so it must hold exactly rather than approximately.

All credit values are produced as Decimal at 9 dp (never float) per §27.7.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from snowobs_fixtures.account import (
    REGRESSION_FINGERPRINT,
    SPILL_FINGERPRINT,
    WH_OVERSIZED,
    WH_QUEUED,
    WH_REGRESSION,
    WH_SPIKE,
    WH_SPILL,
    WH_ZOMBIE,
    Account,
    Warehouse,
    business_factor,
)
from snowobs_fixtures.config import GeneratorConfig
from snowobs_fixtures.ground_truth import GroundTruth

UTC_OFFSET = "-07:00"  # account timezone rendered into TIMESTAMP_LTZ strings

QUERY_TEMPLATES: list[tuple[str, str, str]] = [
    # (fingerprint id, query type, SQL text)
    ("fp-bi-orders-0001", "SELECT", "SELECT region, SUM(amount) FROM ORDERS GROUP BY 1"),
    ("fp-bi-customers-0002", "SELECT", "SELECT * FROM DIM_CUSTOMER WHERE segment = ?"),
    ("fp-elt-merge-0003", "MERGE", "MERGE INTO FACT_SALES USING STG_SALES ON ?"),
    ("fp-elt-copy-0004", "COPY", "COPY INTO STG_EVENTS FROM @LANDING/events"),
    ("fp-adhoc-explore-0005", "SELECT", "SELECT * FROM EVENTS WHERE event_date > ?"),
    ("fp-ml-features-0006", "SELECT", "SELECT features FROM FEATURE_STORE SAMPLE (?)"),
    (REGRESSION_FINGERPRINT, "SELECT", "SELECT * FROM FACT_EVENTS WHERE event_ts BETWEEN ? AND ?"),
    (SPILL_FINGERPRINT, "SELECT", "SELECT a.*, b.* FROM BIG_A a JOIN BIG_B b ON a.k = b.k"),
]

ERROR_CLASSES = [
    (None, None),
    ("000904", "SQL compilation error: invalid identifier"),
    ("000630", "Statement reached its statement or warehouse timeout"),
    ("100183", "Numeric value is not recognized"),
]


def _dec(value: float, places: str = "0.000000001") -> Decimal:
    return Decimal(str(value)).quantize(Decimal(places))


def _ltz(moment: datetime) -> str:
    """Render a naive datetime as a Snowflake TIMESTAMP_LTZ string."""
    return f"{moment.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} {UTC_OFFSET}"


def _query_id(seed: int, day: date, index: int) -> str:
    digest = hashlib.sha256(f"{seed}:{day.isoformat()}:{index}".encode()).hexdigest()
    return f"01b{digest[:5]}-{digest[5:9]}-{digest[9:13]}-{digest[13:17]}-{digest[17:29]}"


def running_hours(wh: Warehouse, day: date, gt: GroundTruth) -> float:
    """Hours the warehouse has at least one cluster running on this day."""
    is_weekend = day.weekday() >= 5
    base = wh.busy_hours * (wh.weekend_factor if is_weekend else business_factor(day))

    if wh.name == WH_ZOMBIE:
        # Zombie: auto_suspend never bites (long-running idle session), no queries.
        zombie = gt.get("ph-zombie-warehouse")
        if zombie.window_start and day >= zombie.window_start:
            return 4.0
    if wh.name == WH_SPIKE:
        spike = gt.get("ph-spend-spike")
        if spike.window_start == day:
            return 20.0  # runaway backfill: the warehouse never suspends that day
    if wh.name == WH_REGRESSION:
        regression = gt.get("ph-fingerprint-regression")
        if regression.window_start and day >= regression.window_start:
            return base * 1.8  # the regressed fingerprint lengthens the ELT window
    return round(base, 4)


def clusters_running(wh: Warehouse, day: date, gt: GroundTruth | None = None) -> float:
    """Average clusters running — saturates at max_clusters on the queued warehouse."""
    if wh.name == WH_SPIKE and gt is not None:
        spike = gt.get("ph-spend-spike")
        if spike.window_start == day:
            # The runaway scales out to every cluster it is allowed: this, times
            # the 20-hour run above, is what makes the day a ~4x account spike.
            return float(max(wh.max_clusters, 8))
    if wh.max_clusters <= 1:
        return 1.0
    if wh.name == WH_QUEUED and day.weekday() < 5:
        return float(wh.max_clusters)  # sustained saturation (phenomenon)
    return 1.0 + (wh.max_clusters - 1) * 0.25


def warehouse_credits(wh: Warehouse, day: date, gt: GroundTruth) -> Decimal:
    """Metered compute credits for a warehouse-day."""
    hours = running_hours(wh, day, gt)
    return _dec(hours * clusters_running(wh, day, gt) * wh.credits_per_hour)


def attribution_ratio(wh: Warehouse, day: date, gt: GroundTruth) -> float:
    """Fraction of metered credits attributable to queries (rest is idle)."""
    if wh.name == WH_ZOMBIE:
        zombie = gt.get("ph-zombie-warehouse")
        if zombie.window_start and day >= zombie.window_start:
            return 0.0  # all idle: credits burn with no queries at all
    if wh.name == WH_OVERSIZED:
        return 0.18  # heavily over-provisioned: most credits are idle
    if wh.workload == "elt":
        return 0.90
    if wh.workload == "bi":
        return 0.80
    return 0.72


def _daily_query_budget(config: GeneratorConfig, account: Account, wh: Warehouse) -> int:
    """Share of the daily query volume issued against this warehouse."""
    weights = {"elt": 1.4, "bi": 2.2, "adhoc": 1.6, "training": 0.3, "zombie": 0.0}
    total = sum(weights.get(w.workload, 1.0) for w in account.warehouses)
    share = weights.get(wh.workload, 1.0) / total if total else 0.0
    return max(int(config.daily_queries * share), 0)


def _fingerprints_for(wh: Warehouse) -> list[tuple[str, str, str]]:
    if wh.workload == "elt":
        pool = [t for t in QUERY_TEMPLATES if "elt" in t[0] or t[0] == REGRESSION_FINGERPRINT]
        if wh.name == WH_SPILL:
            pool = [t for t in QUERY_TEMPLATES if t[0] == SPILL_FINGERPRINT] + pool
        return pool
    if wh.workload == "bi":
        return [t for t in QUERY_TEMPLATES if "bi" in t[0]]
    if wh.workload == "training":
        return [t for t in QUERY_TEMPLATES if "ml" in t[0]]
    return [t for t in QUERY_TEMPLATES if "adhoc" in t[0] or "bi" in t[0]]


class WorkloadGenerator:
    """Emits QUERY_HISTORY, QUERY_ATTRIBUTION_HISTORY, and metering rows."""

    def __init__(self, account: Account, gt: GroundTruth) -> None:
        self.account = account
        self.config = account.config
        self.gt = gt

    # ---------------------------------------------------------------- queries
    def queries_for_day(self, day: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return (query_history rows, query_attribution rows) for one day."""
        rng = random.Random(f"{self.config.seed}:{day.isoformat()}")  # noqa: S311
        history: list[dict[str, Any]] = []
        attribution: list[dict[str, Any]] = []
        index = 0

        for wh in self.account.warehouses:
            budget = _daily_query_budget(self.config, self.account, wh)
            if budget == 0:
                continue
            if day.weekday() >= 5:
                budget = int(budget * max(wh.weekend_factor, 0.05))
            hours = running_hours(wh, day, self.gt)
            if hours <= 0:
                continue

            metered = warehouse_credits(wh, day, self.gt)
            attributable = metered * _dec(attribution_ratio(wh, day, self.gt), "0.0001")
            templates = _fingerprints_for(wh) or QUERY_TEMPLATES
            # Weight query cost so the planted offender dominates after its day.
            weights = [self._fingerprint_weight(fp, wh, day) for fp, _, _ in templates]
            total_weight = sum(weights) or 1.0

            candidate_users = self._users_for(wh)
            # Index selection is weight-proportional, so the expected weight of a
            # pick is E[w] = sum(w^2)/sum(w). Dividing by that keeps the expected
            # sum of per-query credits equal to `attributable` — without it the
            # attributed total silently undershoots the metered total and every
            # idle-share figure is wrong.
            expected_weight = sum(w * w for w in weights) / total_weight
            for _ in range(budget):
                fp_index = self._pick_index(rng, weights, total_weight)
                fingerprint, query_type, text = templates[fp_index]
                user = candidate_users[rng.randrange(len(candidate_users))]
                start_hour = self._start_hour(rng, wh, hours)
                start = datetime.combine(day, time(0)) + timedelta(hours=start_hour)
                credits = attributable * _dec(
                    weights[fp_index] / (expected_weight * budget), "0.00000001"
                )
                row, attr = self._query_row(
                    rng=rng,
                    day=day,
                    index=index,
                    wh=wh,
                    user=user.name,
                    team=user.team,
                    fingerprint=fingerprint,
                    query_type=query_type,
                    text=text,
                    start=start,
                    credits=credits,
                )
                history.append(row)
                if attr is not None:
                    attribution.append(attr)
                index += 1
        return history, attribution

    def _users_for(self, wh: Warehouse) -> list[Any]:
        team = wh.owner_team
        if team:
            users = self.account.team_users(team)
            if users:
                return users
        # Untagged warehouses draw from everyone — this is what makes the spend
        # untagged rather than merely unlabelled.
        return [u for u in self.account.users if not u.dormant]

    def _fingerprint_weight(self, fingerprint: str, wh: Warehouse, day: date) -> float:
        if fingerprint == REGRESSION_FINGERPRINT:
            regression = self.gt.get("ph-fingerprint-regression")
            if regression.window_start and day >= regression.window_start:
                return 12.0  # becomes the top offender
            return 1.0
        if fingerprint == SPILL_FINGERPRINT and wh.name == WH_SPILL:
            return 4.0
        return 1.0

    @staticmethod
    def _pick_index(rng: random.Random, weights: list[float], total: float) -> int:
        target = rng.random() * total
        cumulative = 0.0
        for i, weight in enumerate(weights):
            cumulative += weight
            if target <= cumulative:
                return i
        return len(weights) - 1

    @staticmethod
    def _start_hour(rng: random.Random, wh: Warehouse, hours: float) -> float:
        if wh.workload == "elt":
            base = 2.0  # nightly batch window
        elif wh.workload == "bi":
            base = 8.0  # business hours
        else:
            base = 9.0
        return min(base + rng.random() * hours, 23.99)

    def _query_row(
        self,
        *,
        rng: random.Random,
        day: date,
        index: int,
        wh: Warehouse,
        user: str,
        team: str,
        fingerprint: str,
        query_type: str,
        text: str,
        start: datetime,
        credits: Decimal,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        query_id = _query_id(self.config.seed, day, index)
        regressed = (
            fingerprint == REGRESSION_FINGERPRINT
            and (rw := self.gt.get("ph-fingerprint-regression").window_start) is not None
            and day >= rw
        )
        spilling = fingerprint == SPILL_FINGERPRINT and wh.name == WH_SPILL

        elapsed_ms = int(rng.uniform(400, 9_000) * (4.0 if regressed else 1.0))
        compilation = int(elapsed_ms * rng.uniform(0.02, 0.08))
        queued_overload = (
            int(elapsed_ms * rng.uniform(0.08, 0.25))
            if wh.name == WH_QUEUED and day.weekday() < 5
            else 0
        )
        execution = max(elapsed_ms - compilation - queued_overload, 1)

        partitions_total = rng.randint(2_000, 40_000)
        if regressed:
            pruning = 0.95  # pruning collapse: nearly every partition scanned
        elif spilling:
            pruning = 0.6
        else:
            pruning = rng.uniform(0.02, 0.15)
        partitions_scanned = max(int(partitions_total * pruning), 1)
        bytes_scanned = partitions_scanned * rng.randint(2_000_000, 6_000_000)

        error_code, error_message = ERROR_CLASSES[0]
        if rng.random() < 0.015:
            error_code, error_message = ERROR_CLASSES[rng.randrange(1, len(ERROR_CLASSES))]
        status = "FAIL" if error_code else "SUCCESS"

        # Untagged warehouses deliberately omit the team tag (phenomenon).
        query_tag = "" if wh.owner_team is None else f'{{"team":"{team}","app":"snowobs-demo"}}'

        row: dict[str, Any] = {
            "QUERY_ID": query_id,
            "QUERY_TEXT": text,
            "DATABASE_NAME": "DB_PROD" if wh.workload == "elt" else "DB_ANALYTICS",
            "SCHEMA_NAME": "ELT" if wh.workload == "elt" else "MARTS",
            "QUERY_TYPE": query_type,
            "SESSION_ID": str(abs(hash((query_id, "session"))) % 10**12),
            "USER_NAME": user,
            "ROLE_NAME": f"ROLE_{team.removeprefix('TEAM_')}",
            "WAREHOUSE_ID": str(1000 + self.account.warehouses.index(wh)),
            "WAREHOUSE_NAME": wh.name,
            "WAREHOUSE_SIZE": wh.size,
            "WAREHOUSE_TYPE": "STANDARD",
            "CLUSTER_NUMBER": 1,
            "QUERY_TAG": query_tag,
            "EXECUTION_STATUS": status,
            "ERROR_CODE": error_code or "",
            "ERROR_MESSAGE": error_message or "",
            "START_TIME": _ltz(start),
            "END_TIME": _ltz(start + timedelta(milliseconds=elapsed_ms)),
            "TOTAL_ELAPSED_TIME": elapsed_ms,
            "BYTES_SCANNED": bytes_scanned,
            "PERCENTAGE_SCANNED_FROM_CACHE": round(rng.uniform(0.0, 0.4), 4),
            "ROWS_PRODUCED": rng.randint(1, 500_000),
            "COMPILATION_TIME": compilation,
            "EXECUTION_TIME": execution,
            "QUEUED_PROVISIONING_TIME": 0,
            "QUEUED_REPAIR_TIME": 0,
            "QUEUED_OVERLOAD_TIME": queued_overload,
            "TRANSACTION_BLOCKED_TIME": 0,
            "BYTES_SPILLED_TO_LOCAL_STORAGE": (
                rng.randint(10**8, 10**9) if spilling or regressed else 0
            ),
            "BYTES_SPILLED_TO_REMOTE_STORAGE": rng.randint(10**9, 4 * 10**9) if spilling else 0,
            "PARTITIONS_SCANNED": partitions_scanned,
            "PARTITIONS_TOTAL": partitions_total,
            "BYTES_WRITTEN": rng.randint(0, 10**8),
            "CREDITS_USED_CLOUD_SERVICES": str(_dec(elapsed_ms / 3_600_000 * 0.05)),
            "QUERY_PARAMETERIZED_HASH": fingerprint,
            "QUERY_HASH": hashlib.md5(  # noqa: S324 — fixture fingerprint, not security
                f"{fingerprint}:{query_id}".encode()
            ).hexdigest(),
        }

        # QUERY_ATTRIBUTION_HISTORY excludes failures and sub-100ms queries (verified).
        attribution: dict[str, Any] | None = None
        if status == "SUCCESS" and elapsed_ms >= 100 and credits > 0:
            attribution = {
                "QUERY_ID": query_id,
                "PARENT_QUERY_ID": "",
                "ROOT_QUERY_ID": "",
                "WAREHOUSE_ID": row["WAREHOUSE_ID"],
                "WAREHOUSE_NAME": wh.name,
                "USER_NAME": user,
                "QUERY_TAG": query_tag,
                "QUERY_HASH": row["QUERY_HASH"],
                "QUERY_PARAMETERIZED_HASH": fingerprint,
                "START_TIME": row["START_TIME"],
                "END_TIME": row["END_TIME"],
                "CREDITS_ATTRIBUTED_COMPUTE": str(credits),
                "CREDITS_USED_QUERY_ACCELERATION": "0.000000000",
            }
        return row, attribution

    # -------------------------------------------------------------- metering
    def warehouse_metering(self, day: date) -> Iterator[dict[str, Any]]:
        """Hourly WAREHOUSE_METERING_HISTORY rows for a day."""
        for i, wh in enumerate(self.account.warehouses):
            total = warehouse_credits(wh, day, self.gt)
            if total <= 0:
                continue
            hours = max(round(running_hours(wh, day, self.gt)), 1)
            first_hour = 2 if wh.workload == "elt" else 8
            per_hour = (total / hours).quantize(Decimal("0.000000001"))
            emitted = Decimal(0)
            for h in range(hours):
                credits = per_hour if h < hours - 1 else (total - emitted)
                emitted += credits
                start = datetime.combine(day, time(0)) + timedelta(hours=(first_hour + h) % 24)
                cloud = (credits * Decimal("0.06")).quantize(Decimal("0.000000001"))
                yield {
                    "START_TIME": _ltz(start),
                    "END_TIME": _ltz(start + timedelta(hours=1)),
                    "WAREHOUSE_ID": str(1000 + i),
                    "WAREHOUSE_NAME": wh.name,
                    "CREDITS_USED": str(credits + cloud),
                    "CREDITS_USED_COMPUTE": str(credits),
                    "CREDITS_USED_CLOUD_SERVICES": str(cloud),
                }

    def daily_compute_credits(self, day: date) -> Decimal:
        return sum(
            (warehouse_credits(wh, day, self.gt) for wh in self.account.warehouses),
            Decimal(0),
        )
