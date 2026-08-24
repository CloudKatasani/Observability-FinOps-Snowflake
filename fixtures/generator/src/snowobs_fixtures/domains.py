"""Storage, pipeline, security, and AI-usage row generation.

Each function returns rows keyed by source id so the writer can stay generic.
Phenomena anchored in these domains (task root failure, dynamic-table lag,
clone growth, Time-Travel excess, dormant users, privilege drift, AI spend
growth) are planted here.
"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from snowobs_fixtures.account import (
    CLONE_TABLE,
    DRIFT_USER,
    DT_LAGGING,
    Account,
)
from snowobs_fixtures.ground_truth import GroundTruth
from snowobs_fixtures.workload import _dec, _ltz

AI_FUNCTIONS = [
    ("COMPLETE", "claude-sonnet-5"),
    ("COMPLETE", "llama3.1-70b"),
    ("SUMMARIZE", "snowflake-arctic"),
    ("EMBED_TEXT_768", "snowflake-arctic-embed-l-v2.0"),
]


def storage_rows(account: Account, gt: GroundTruth, day: date) -> dict[str, list[dict[str, Any]]]:
    """DATABASE_STORAGE_USAGE_HISTORY + STORAGE_USAGE for a day."""
    elapsed = (day - account.config.start_date).days
    db_rows: list[dict[str, Any]] = []
    total_active = 0.0
    total_failsafe = 0.0

    for db in account.databases:
        active = db.base_bytes * (1 + db.daily_growth) ** elapsed
        failsafe = active * 0.08
        db_rows.append(
            {
                "USAGE_DATE": day.isoformat(),
                "DATABASE_ID": str(2000 + account.databases.index(db)),
                "DATABASE_NAME": db.name,
                "AVERAGE_DATABASE_BYTES": round(active, 2),
                "AVERAGE_FAILSAFE_BYTES": round(failsafe, 2),
                "AVERAGE_HYBRID_TABLE_STORAGE_BYTES": 0,
            }
        )
        total_active += active
        total_failsafe += failsafe

    storage = [
        {
            "USAGE_DATE": day.isoformat(),
            "STORAGE_BYTES": round(total_active, 2),
            "STAGE_BYTES": round(total_active * 0.03, 2),
            "FAILSAFE_BYTES": round(total_failsafe, 2),
            "HYBRID_TABLE_STORAGE_BYTES": 0,
        }
    ]
    return {"database_storage_usage_history": db_rows, "storage_usage": storage}


def table_storage_metrics(account: Account, gt: GroundTruth) -> list[dict[str, Any]]:
    """Current TABLE_STORAGE_METRICS snapshot, including the un-dropped clone."""
    rng = random.Random(account.config.seed + 77)  # noqa: S311
    rows: list[dict[str, Any]] = []
    table_id = 5000
    clone = gt.get("ph-clone-growth")
    retained_clone_bytes = float(clone.expectations["min_retained_for_clone_bytes"]) * 1.15

    for db in account.databases:
        for i in range(1, 9):
            table_id += 1
            active = rng.uniform(2e10, 9e11)
            # Non-prod carries excessive Time Travel (planted).
            tt_ratio = db.time_travel_ratio
            rows.append(
                {
                    "ID": str(table_id),
                    "TABLE_NAME": f"TBL_{db.name.removeprefix('DB_')}_{i:02d}",
                    "TABLE_SCHEMA": "MARTS" if db.environment == "prod" else "STAGING",
                    "TABLE_CATALOG": db.name,
                    "CLONE_GROUP_ID": str(table_id),
                    "ACTIVE_BYTES": round(active, 2),
                    "TIME_TRAVEL_BYTES": round(active * tt_ratio, 2),
                    "FAILSAFE_BYTES": round(active * 0.08, 2),
                    "RETAINED_FOR_CLONE_BYTES": 0,
                    "IS_TRANSIENT": "NO",
                    "DELETED": False,
                    "TABLE_CREATED": _ltz(datetime.combine(gt.start_date, time(9))),
                    "TABLE_DROPPED": "",
                    "TABLE_ENTERED_FAILSAFE": "",
                    "RETENTION_TIME": 1 if db.environment == "prod" else 14,
                }
            )

    # The un-dropped clone: large RETAINED_FOR_CLONE_BYTES on a stale table.
    table_id += 1
    rows.append(
        {
            "ID": str(table_id),
            "TABLE_NAME": CLONE_TABLE,
            "TABLE_SCHEMA": "STAGING",
            "TABLE_CATALOG": "DB_DEV",
            "CLONE_GROUP_ID": "5001",
            "ACTIVE_BYTES": round(retained_clone_bytes * 0.1, 2),
            "TIME_TRAVEL_BYTES": round(retained_clone_bytes * 0.05, 2),
            "FAILSAFE_BYTES": 0,
            "RETAINED_FOR_CLONE_BYTES": round(retained_clone_bytes, 2),
            "IS_TRANSIENT": "NO",
            "DELETED": False,
            "TABLE_CREATED": _ltz(datetime.combine(gt.start_date, time(9))),
            "TABLE_DROPPED": "",
            "TABLE_ENTERED_FAILSAFE": "",
            "RETENTION_TIME": 14,
        }
    )
    return rows


def task_rows(account: Account, gt: GroundTruth, day: date) -> list[dict[str, Any]]:
    """TASK_HISTORY rows, including the planted root-failure fan-out."""
    rng = random.Random(f"{account.config.seed}:tasks:{day.isoformat()}")  # noqa: S311
    failure = gt.get("ph-task-root-failure")
    is_failure_day = failure.window_start == day
    rows: list[dict[str, Any]] = []

    for graph in account.task_graphs:
        scheduled = datetime.combine(day, time(graph.schedule_hour))
        root_fails = is_failure_day and graph.root == failure.subjects[0]
        run_id = f"{day.isoformat()}-{graph.root}"
        group_id = f"grp-{run_id}"

        root_state = "FAILED" if root_fails else "SUCCEEDED"
        rows.append(
            {
                "NAME": graph.root,
                "DATABASE_NAME": graph.database,
                "SCHEMA_NAME": graph.schema,
                "QUERY_ID": "",
                "STATE": root_state,
                "ERROR_CODE": "091003" if root_fails else "",
                "ERROR_MESSAGE": (
                    "Failure during expansion of view: source table not found" if root_fails else ""
                ),
                "SCHEDULED_TIME": _ltz(scheduled),
                "COMPLETED_TIME": _ltz(scheduled + timedelta(minutes=rng.randint(3, 25))),
                "RETURN_VALUE": "",
                "GRAPH_RUN_GROUP_ID": group_id,
                "GRAPH_ROOT_TASK_ID": graph.root,
                "RUN_ID": run_id,
            }
        )

        for offset, child in enumerate(graph.children, start=1):
            # A failed root suspends the graph: children report SKIPPED, which is
            # what makes root-cause identification (one alert, not twelve) testable.
            if root_fails:
                state, code, message = "SKIPPED", "", "Upstream task failed"
            elif rng.random() < 0.01:
                state, code, message = "FAILED", "000630", "Statement reached its timeout"
            else:
                state, code, message = "SUCCEEDED", "", ""
            child_time = scheduled + timedelta(minutes=5 * offset)
            rows.append(
                {
                    "NAME": child,
                    "DATABASE_NAME": graph.database,
                    "SCHEMA_NAME": graph.schema,
                    "QUERY_ID": "",
                    "STATE": state,
                    "ERROR_CODE": code,
                    "ERROR_MESSAGE": message,
                    "SCHEDULED_TIME": _ltz(child_time),
                    "COMPLETED_TIME": _ltz(child_time + timedelta(minutes=rng.randint(1, 12))),
                    "RETURN_VALUE": "",
                    "GRAPH_RUN_GROUP_ID": group_id,
                    "GRAPH_ROOT_TASK_ID": graph.root,
                    "RUN_ID": run_id,
                }
            )
    return rows


def dynamic_table_rows(account: Account, gt: GroundTruth, day: date) -> list[dict[str, Any]]:
    """DYNAMIC_TABLE_REFRESH_HISTORY with the planted TARGET_LAG breach."""
    rng = random.Random(f"{account.config.seed}:dt:{day.isoformat()}")  # noqa: S311
    lag = gt.get("ph-dt-lag")
    target_lag = int(lag.expectations["target_lag_seconds"])
    breaching = (
        lag.window_start is not None
        and lag.window_end is not None
        and lag.window_start <= day <= lag.window_end
    )
    rows: list[dict[str, Any]] = []
    tables = [DT_LAGGING, "DB_ANALYTICS.MARTS.DT_CUSTOMER_360"]

    for qualified in tables:
        database, schema, name = qualified.split(".")
        refreshes = 24 if not (breaching and qualified == DT_LAGGING) else 4
        for r in range(refreshes):
            start = datetime.combine(day, time(0)) + timedelta(hours=(24 / refreshes) * r)
            duration = rng.randint(60, 400)
            data_ts = start - timedelta(
                seconds=target_lag * (3 if breaching and qualified == DT_LAGGING else 0.4)
            )
            rows.append(
                {
                    "NAME": name,
                    "SCHEMA_NAME": schema,
                    "DATABASE_NAME": database,
                    "QUALIFIED_NAME": qualified,
                    "STATE": "SUCCEEDED",
                    "STATE_MESSAGE": "",
                    "REFRESH_START_TIME": _ltz(start),
                    "REFRESH_END_TIME": _ltz(start + timedelta(seconds=duration)),
                    "REFRESH_ACTION": "INCREMENTAL",
                    "REFRESH_TRIGGER": "SCHEDULED",
                    "TARGET_LAG_SEC": target_lag,
                    "DATA_TIMESTAMP": _ltz(data_ts),
                }
            )
    return rows


def login_rows(account: Account, gt: GroundTruth, day: date) -> list[dict[str, Any]]:
    """LOGIN_HISTORY. Dormant users stop logging in; failed-login noise included."""
    rng = random.Random(f"{account.config.seed}:login:{day.isoformat()}")  # noqa: S311
    dormant_cutoff = gt.end_date - timedelta(days=95)
    rows: list[dict[str, Any]] = []
    event_id = int(day.strftime("%Y%m%d")) * 1000

    for user in account.users:
        if user.dormant and day > dormant_cutoff:
            continue  # the dormant cohort: no logins in the trailing window
        attempts = 1 if user.service_account else rng.randint(0, 3)
        for _ in range(attempts):
            event_id += 1
            success = rng.random() > 0.04
            moment = datetime.combine(day, time(rng.randrange(6, 20), rng.randrange(60)))
            rows.append(
                {
                    "EVENT_ID": str(event_id),
                    "EVENT_TIMESTAMP": _ltz(moment),
                    "EVENT_TYPE": "LOGIN",
                    "USER_NAME": user.name,
                    "CLIENT_IP": f"10.{rng.randrange(1, 250)}.{rng.randrange(1, 250)}.12",
                    "REPORTED_CLIENT_TYPE": "PYTHON_DRIVER" if user.service_account else "JDBC",
                    "REPORTED_CLIENT_VERSION": "4.7.2",
                    "FIRST_AUTHENTICATION_FACTOR": (
                        "RSA_KEYPAIR" if user.service_account else "PASSWORD"
                    ),
                    "SECOND_AUTHENTICATION_FACTOR": ("" if user.service_account else "DUO_PUSH"),
                    "IS_SUCCESS": "YES" if success else "NO",
                    "ERROR_CODE": "" if success else "390100",
                    "ERROR_MESSAGE": ""
                    if success
                    else "Incorrect username or password was specified",
                }
            )
    return rows


def grant_rows(account: Account, gt: GroundTruth) -> list[dict[str, Any]]:
    """GRANTS_TO_USERS snapshot including the planted privilege-drift event."""
    drift = gt.get("ph-privilege-drift")
    rows: list[dict[str, Any]] = []
    for user in account.users:
        rows.append(
            {
                "CREATED_ON": _ltz(datetime.combine(gt.start_date, time(9))),
                "DELETED_ON": "",
                "ROLE": f"ROLE_{user.team.removeprefix('TEAM_')}",
                "GRANTED_TO": "USER",
                "GRANTEE_NAME": user.name,
                "GRANTED_BY": "USERADMIN",
            }
        )
    # Privilege drift: a service account gains an ACCOUNTADMIN-adjacent role.
    rows.append(
        {
            "CREATED_ON": _ltz(datetime.combine(drift.window_start or gt.end_date, time(3, 14))),
            "DELETED_ON": "",
            "ROLE": str(drift.expectations["granted_role"]),
            "GRANTED_TO": "USER",
            "GRANTEE_NAME": DRIFT_USER,
            "GRANTED_BY": "ACCOUNTADMIN",
        }
    )
    return rows


def ai_usage_rows(account: Account, gt: GroundTruth, day: date) -> list[dict[str, Any]]:
    """CORTEX_FUNCTIONS_USAGE_HISTORY — appears in week 10 and grows."""
    ai = gt.get("ph-ai-spend-growth")
    if ai.window_start is None or day < ai.window_start:
        return []
    rng = random.Random(f"{account.config.seed}:ai:{day.isoformat()}")  # noqa: S311
    elapsed = (day - ai.window_start).days
    growth = 1.0 + 0.035 * elapsed  # steady growth from first appearance
    rows: list[dict[str, Any]] = []
    for hour in range(8, 20, 4):
        for function_name, model in AI_FUNCTIONS:
            tokens = int(rng.uniform(40_000, 180_000) * growth)
            credits = _dec(tokens / 1_000_000 * 1.4)
            start = datetime.combine(day, time(hour))
            rows.append(
                {
                    "START_TIME": _ltz(start),
                    "END_TIME": _ltz(start + timedelta(hours=1)),
                    "FUNCTION_NAME": function_name,
                    "MODEL_NAME": model,
                    "WAREHOUSE_ID": "1005",
                    "TOKENS": tokens,
                    "TOKEN_CREDITS": str(credits),
                }
            )
    return rows


def serverless_task_rows(account: Account, gt: GroundTruth, day: date) -> list[dict[str, Any]]:
    """SERVERLESS_TASK_HISTORY — serverless compute for the marts graph."""
    rng = random.Random(f"{account.config.seed}:serverless:{day.isoformat()}")  # noqa: S311
    rows: list[dict[str, Any]] = []
    for graph in account.task_graphs:
        if graph.schema != "MARTS":
            continue
        start = datetime.combine(day, time(graph.schedule_hour))
        credits: Decimal = _dec(rng.uniform(0.4, 1.6))
        rows.append(
            {
                "START_TIME": _ltz(start),
                "END_TIME": _ltz(start + timedelta(minutes=40)),
                "TASK_ID": f"task-{graph.root}",
                "TASK_NAME": graph.root,
                "SCHEMA_NAME": graph.schema,
                "DATABASE_NAME": graph.database,
                "CREDITS_USED": str(credits),
            }
        )
    return rows


def warehouse_snapshot(account: Account) -> list[dict[str, Any]]:
    """SHOW WAREHOUSES snapshot."""
    return [
        {
            "NAME": wh.name,
            "STATE": "SUSPENDED",
            "TYPE": "STANDARD",
            "SIZE": wh.size,
            "MIN_CLUSTER_COUNT": wh.min_clusters,
            "MAX_CLUSTER_COUNT": wh.max_clusters,
            "SCALING_POLICY": "STANDARD",
            "AUTO_SUSPEND": wh.auto_suspend,
            "AUTO_RESUME": True,
            "OWNER": "SYSADMIN",
            "COMMENT": f"workload={wh.workload}",
            "CREATED_ON": _ltz(datetime.combine(account.config.start_date, time(8))),
            "RESOURCE_MONITOR": "null",
        }
        for wh in account.warehouses
    ]


def user_snapshot(account: Account, gt: GroundTruth) -> list[dict[str, Any]]:
    """USERS snapshot with last-login reflecting the dormant cohort."""
    rows: list[dict[str, Any]] = []
    for i, user in enumerate(account.users, start=1):
        last_login = gt.end_date - timedelta(days=120 if user.dormant else 1)
        rows.append(
            {
                "USER_ID": str(3000 + i),
                "NAME": user.name,
                "LOGIN_NAME": user.name.lower(),
                "DISPLAY_NAME": user.name.replace("_", " ").title(),
                "EMAIL": f"{user.name.lower()}@example.com",
                "DEFAULT_ROLE": f"ROLE_{user.team.removeprefix('TEAM_')}",
                "DEFAULT_WAREHOUSE": "",
                "DISABLED": False,
                "HAS_PASSWORD": not user.service_account,
                "HAS_RSA_PUBLIC_KEY": user.service_account,
                "LAST_SUCCESS_LOGIN": _ltz(datetime.combine(last_login, time(9, 30))),
                "CREATED_ON": _ltz(datetime.combine(gt.start_date, time(8))),
                "DELETED_ON": "",
                "TYPE": "SERVICE" if user.service_account else "PERSON",
            }
        )
    return rows
