"""The SQL guard must refuse everything that is not a single read-only SELECT.

These are adversarial tests: each one is a way an agent, an admin, or an
injected string could try to get something past the guard (R9, §27.4).
"""

from __future__ import annotations

import pytest

from snowobs_sqlguard.guard import (
    GuardPolicy,
    SqlGuardError,
    check,
    is_allowed,
    live_policy,
    offline_policy,
)

POLICY = offline_policy(frozenset({"QUERY_HISTORY", "METERING_DAILY_HISTORY"}), max_rows=1000)


def test_simple_select_passes_and_gets_a_limit() -> None:
    result = check("SELECT * FROM query_history", POLICY)
    assert "LIMIT 1000" in result.sql.upper()
    assert result.limit == 1000
    assert result.relations == ("QUERY_HISTORY",)
    assert "LIMIT 1000 applied" in result.adjustments


def test_cte_passes_and_cte_names_are_not_treated_as_relations() -> None:
    sql = """
        WITH recent AS (SELECT * FROM query_history LIMIT 10)
        SELECT COUNT(*) FROM recent
    """
    result = check(sql, POLICY)
    assert "QUERY_HISTORY" in result.relations
    assert "RECENT" not in result.relations


def test_existing_small_limit_is_respected() -> None:
    result = check("SELECT * FROM query_history LIMIT 5", POLICY)
    assert result.limit == 5
    assert result.adjustments == ()


def test_oversized_limit_is_reduced() -> None:
    result = check("SELECT * FROM query_history LIMIT 999999", POLICY)
    assert result.limit == 1000
    assert any("reduced" in a for a in result.adjustments)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM query_history",
        "DROP TABLE query_history",
        "UPDATE query_history SET query_id = 'x'",
        "INSERT INTO query_history VALUES (1)",
        "CREATE TABLE evil AS SELECT * FROM query_history",
        "ALTER TABLE query_history ADD COLUMN x INT",
        "MERGE INTO query_history USING x ON true WHEN MATCHED THEN DELETE",
        "GRANT SELECT ON query_history TO ROLE PUBLIC",
        "COPY INTO @~/exfil FROM query_history",
        "PUT file:///etc/passwd @~/stage",
        "GET @~/stage file:///tmp/out",
        "CALL some_procedure()",
        "USE WAREHOUSE COMPUTE_WH",
        "SET x = 1",
        "BEGIN",
        "COMMIT",
        "TRUNCATE TABLE query_history",
    ],
)
def test_write_and_command_statements_are_rejected(sql: str) -> None:
    with pytest.raises(SqlGuardError):
        check(sql, POLICY)


def test_stacked_statements_are_rejected() -> None:
    with pytest.raises(SqlGuardError, match="single statement"):
        check("SELECT 1 FROM query_history; DROP TABLE query_history", POLICY)


def test_stacked_statement_hidden_behind_a_comment_is_rejected() -> None:
    with pytest.raises(SqlGuardError):
        check(
            "SELECT 1 FROM query_history -- harmless\n; DELETE FROM query_history",
            POLICY,
        )


def test_write_hidden_inside_a_subquery_is_rejected() -> None:
    # The outer statement looks like a SELECT; the payload is nested.
    with pytest.raises(SqlGuardError):
        check(
            "SELECT * FROM query_history WHERE 1 = (DELETE FROM query_history RETURNING 1)",
            POLICY,
        )


def test_relations_outside_the_allowlist_are_rejected() -> None:
    with pytest.raises(SqlGuardError, match="outside the allowed schemas"):
        check("SELECT * FROM secret_customer_data", POLICY)


def test_join_to_a_forbidden_relation_is_rejected() -> None:
    with pytest.raises(SqlGuardError, match="outside the allowed schemas"):
        check(
            "SELECT * FROM query_history q JOIN payroll p ON q.user_name = p.user_name",
            POLICY,
        )


def test_forbidden_relation_inside_a_cte_is_rejected() -> None:
    with pytest.raises(SqlGuardError):
        check("WITH x AS (SELECT * FROM payroll) SELECT * FROM x", POLICY)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SYSTEM$CLUSTERING_INFORMATION('t') FROM query_history",
        "SELECT GET_DDL('table', 't') FROM query_history",
        "SELECT CURRENT_ACCOUNT() FROM query_history",
        "SELECT GET_PRESIGNED_URL(@s, 'f') FROM query_history",
    ],
)
def test_dangerous_functions_are_rejected(sql: str) -> None:
    with pytest.raises(SqlGuardError, match="not permitted"):
        check(sql, POLICY, dialect="snowflake")


def test_unparseable_sql_is_rejected_not_executed() -> None:
    with pytest.raises(SqlGuardError):
        check("SELECT FROM WHERE ((((", POLICY)


def test_empty_statement_is_rejected() -> None:
    with pytest.raises(SqlGuardError, match="Empty"):
        check("   ", POLICY)


def test_live_policy_allows_account_usage_but_not_customer_data() -> None:
    policy = live_policy(warehouse="WH_SNOWOBS_APP", query_tag="SNOWOBS:t1:tile:abc")
    result = check(
        "SELECT * FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY", policy, dialect="snowflake"
    )
    assert result.warehouse == "WH_SNOWOBS_APP"
    assert result.query_tag == "SNOWOBS:t1:tile:abc"
    assert result.statement_timeout_seconds == 300

    assert not is_allowed("SELECT * FROM PROD.SALES.ORDERS", policy, dialect="snowflake")


def test_extra_schemas_can_be_granted_explicitly() -> None:
    policy = live_policy(extra_schemas=frozenset({"OBSERVABILITY.PUBLISHED"}))
    assert is_allowed(
        "SELECT * FROM OBSERVABILITY.PUBLISHED.V_COST_DAILY", policy, dialect="snowflake"
    )
    assert not is_allowed(
        "SELECT * FROM OBSERVABILITY.CURATED.FACT_COST", policy, dialect="snowflake"
    )


def test_union_of_allowed_relations_passes() -> None:
    result = check(
        "SELECT query_id FROM query_history UNION ALL SELECT service_type "
        "FROM metering_daily_history",
        POLICY,
    )
    assert set(result.relations) == {"QUERY_HISTORY", "METERING_DAILY_HISTORY"}


def test_union_smuggling_a_forbidden_relation_is_rejected() -> None:
    with pytest.raises(SqlGuardError):
        check("SELECT 1 FROM query_history UNION ALL SELECT 1 FROM payroll", POLICY)


def test_policy_without_allowlist_denies_everything_by_default() -> None:
    # Fail closed: an unconfigured policy is the most restrictive one.
    with pytest.raises(SqlGuardError):
        check("SELECT * FROM query_history", GuardPolicy())
