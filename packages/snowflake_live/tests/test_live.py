"""LIVE mode: provisioning, connection building, and the capability probe.

The Snowflake driver is not exercised here — a real account runs behind
``pytest -m snowflake`` nightly (§22.1). What *is* exercised is everything that
must be right before a single byte reaches a customer's account: that the
generated grants are granular and never blanket, that secrets never appear in
logs or API payloads, and that an inaccessible source produces the exact
statement which would fix it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from snowobs_live.connection import (
    AuthMethod,
    ConnectionProfile,
    SnowflakeConnectionError,
    build_connect_kwargs,
    query_tag,
)
from snowobs_live.probe import (
    PROBE_WINDOW_DAYS,
    ProbeStatus,
    probe_all,
    probe_organization,
    probe_source,
)
from snowobs_live.provisioning import (
    DEFAULT_READER_ROLE,
    audit_script,
    generate_grant_remediation,
    generate_publisher_role_sql,
    generate_reader_role_sql,
)
from snowobs_semantics.registry import default_registry

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


class FakeRunner:
    """A scripted SQL runner: maps a SQL fragment to rows or an exception."""

    def __init__(self, responses: dict[str, Any] | None = None, default: Any = None) -> None:
        self.responses = responses or {}
        self.default = default if default is not None else [(1,)]
        self.executed: list[str] = []

    def fetch(self, sql: str) -> list[tuple[Any, ...]]:
        self.executed.append(sql)
        for fragment, response in self.responses.items():
            if fragment in sql:
                if isinstance(response, Exception):
                    raise response
                return response  # type: ignore[no-any-return]
        return self.default  # type: ignore[no-any-return]


# ═══════════════════════════════════════════════════ provisioning (R4) ═══════
def test_reader_role_never_grants_blanket_privileges() -> None:
    """§27.3: the one grant this platform must never generate."""
    plan = generate_reader_role_sql()
    assert audit_script(plan.sql) == []
    # The script *mentions* the forbidden grant in its header, explaining that it
    # does not use it. The audit distinguishes a comment from a GRANT; assert on
    # the executable statements rather than the prose.
    statements = [line for line in plan.sql.splitlines() if not line.strip().startswith("--")]
    assert not any("IMPORTED PRIVILEGES" in line.upper() for line in statements)


def test_reader_role_uses_granular_database_roles() -> None:
    plan = generate_reader_role_sql()
    granted = {
        line.split("GRANT DATABASE ROLE ")[1].split(" TO ")[0]
        for line in plan.sql.splitlines()
        if line.startswith("GRANT DATABASE ROLE ")
    }
    # The four verified ACCOUNT_USAGE viewer roles must all appear.
    assert {
        "SNOWFLAKE.USAGE_VIEWER",
        "SNOWFLAKE.GOVERNANCE_VIEWER",
        "SNOWFLAKE.SECURITY_VIEWER",
        "SNOWFLAKE.OBJECT_VIEWER",
    } <= granted
    for role in granted:
        assert role.startswith("SNOWFLAKE.")


def test_every_granted_role_is_justified_by_a_registered_source() -> None:
    """A grant with no source behind it is a privilege nobody asked for."""
    plan = generate_reader_role_sql()
    registry = default_registry()
    for database_role, sources in plan.grants.items():
        assert sources, database_role
        for source_id in sources:
            assert registry.get(source_id).required_db_role == database_role


def test_provisioning_is_idempotent() -> None:
    sql = generate_reader_role_sql().sql
    for statement in ("CREATE ROLE", "CREATE WAREHOUSE", "CREATE RESOURCE MONITOR"):
        for line in sql.splitlines():
            if line.strip().startswith(statement):
                assert "IF NOT EXISTS" in line


def test_resource_monitor_is_notify_only() -> None:
    """§14/§27.8: never hard-suspend; the platform's monitor only notifies."""
    sql = generate_reader_role_sql().sql.upper()
    assert "DO NOTIFY" in sql
    assert "DO SUSPEND" not in sql
    assert "DO SUSPEND_IMMEDIATE" not in sql


def test_writer_role_is_separate_and_scoped_to_one_database() -> None:
    """R4: the standing connection must be incapable of writing."""
    reader = generate_reader_role_sql()
    publisher = generate_publisher_role_sql(database="OBSERVABILITY")
    assert publisher.role != reader.role
    assert audit_script(publisher.sql) == []

    granted_all = [line for line in publisher.sql.splitlines() if line.startswith("GRANT ALL")]
    assert granted_all
    for line in granted_all:
        assert "OBSERVABILITY." in line  # never account-wide


def test_reader_sql_contains_no_write_statements() -> None:
    body = "\n".join(
        line
        for line in generate_reader_role_sql().sql.splitlines()
        if not line.strip().startswith("--")
    ).upper()
    for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "TRUNCATE "):
        assert forbidden not in body


def test_grant_remediation_names_the_exact_statement() -> None:
    """R3: a blocked source comes with the fix, not just the problem."""
    remediation = generate_grant_remediation(["access_history", "query_history"])
    joined = "\n".join(remediation)
    assert "GRANT DATABASE ROLE SNOWFLAKE.GOVERNANCE_VIEWER" in joined
    assert "GRANT DATABASE ROLE SNOWFLAKE.USAGE_VIEWER" in joined
    assert DEFAULT_READER_ROLE in joined


# ═══════════════════════════════════════════════════ connections (§17) ═══════
def test_keypair_is_the_default_auth_method() -> None:
    assert ConnectionProfile(account="a", user="u").auth is AuthMethod.KEYPAIR


def test_password_auth_is_marked_discouraged_with_a_reason() -> None:
    assert AuthMethod.PASSWORD.discouraged is True
    warning = AuthMethod.PASSWORD.warning
    assert warning is not None and "October 2026" in warning
    assert AuthMethod.KEYPAIR.discouraged is False


def test_redacted_profile_never_exposes_the_secret_or_its_reference() -> None:
    """§17: secrets never in the DB, never in logs, never in agent context."""
    profile = ConnectionProfile(
        account="acme", user="SVC", secret_ref="secretsmanager://snowobs/key"
    )
    redacted = profile.redacted()
    assert redacted["has_secret_ref"] is True
    assert "secretsmanager" not in str(redacted)
    assert "secret_ref" not in redacted


def test_every_session_is_tagged_for_attribution() -> None:
    """§7.2: the tool must be attributable in the customer's own telemetry."""
    profile = ConnectionProfile(account="a", user="u", warehouse="WH_SNOWOBS_APP", secret_ref="ref")
    tag = query_tag(profile, tenant="acme", surface="tile", trace_id="abc123")
    assert tag == "SNOWOBS:acme:tile:abc123"

    kwargs = build_connect_kwargs(
        profile, _KeyResolver(), tenant="acme", surface="tile", trace_id="abc123"
    )
    assert kwargs["session_parameters"]["QUERY_TAG"] == tag
    assert kwargs["session_parameters"]["STATEMENT_TIMEOUT_IN_SECONDS"] == 300


def test_connect_kwargs_carry_the_timeout_and_warehouse_pin() -> None:
    profile = ConnectionProfile(
        account="a",
        user="u",
        warehouse="WH_X",
        role="SNOWOBS_READER",
        statement_timeout_s=120,
        secret_ref="ref",
    )
    kwargs = build_connect_kwargs(profile, _KeyResolver())
    assert kwargs["warehouse"] == "WH_X"
    assert kwargs["role"] == "SNOWOBS_READER"
    assert kwargs["session_parameters"]["STATEMENT_TIMEOUT_IN_SECONDS"] == 120
    assert kwargs["login_timeout"] > 0


def test_privatelink_host_and_proxy_are_passed_through() -> None:
    profile = ConnectionProfile(
        account="a",
        user="u",
        host="acme.privatelink.snowflakecomputing.com",
        proxy="proxy.internal",
        secret_ref="ref",
    )
    kwargs = build_connect_kwargs(profile, _KeyResolver())
    assert kwargs["host"] == "acme.privatelink.snowflakecomputing.com"
    assert kwargs["proxy_host"] == "proxy.internal"


def test_missing_secret_reference_is_a_clear_error() -> None:
    profile = ConnectionProfile(account="a", user="u", auth=AuthMethod.PAT)
    with pytest.raises(SnowflakeConnectionError, match="secret reference"):
        build_connect_kwargs(profile, _KeyResolver())


def test_missing_resolver_is_a_clear_error() -> None:
    profile = ConnectionProfile(account="a", user="u", secret_ref="ref")
    with pytest.raises(SnowflakeConnectionError, match="secrets adapter"):
        build_connect_kwargs(profile, None)


def test_malformed_private_key_does_not_leak_material_into_the_error() -> None:
    class BadResolver:
        def resolve(self, reference: str) -> str:
            return "-----BEGIN PRIVATE KEY-----\nSUPERSECRETNONSENSE\n-----END PRIVATE KEY-----"

    profile = ConnectionProfile(account="a", user="u", secret_ref="ref")
    with pytest.raises(SnowflakeConnectionError) as excinfo:
        build_connect_kwargs(profile, BadResolver())
    assert "SUPERSECRETNONSENSE" not in str(excinfo.value)
    assert "PEM-encoded RSA key" in str(excinfo.value)


def test_externalbrowser_needs_no_secret() -> None:
    profile = ConnectionProfile(account="a", user="u", auth=AuthMethod.EXTERNALBROWSER)
    kwargs = build_connect_kwargs(profile, None)
    assert kwargs["authenticator"] == "externalbrowser"
    assert "password" not in kwargs


# ═════════════════════════════════════════════════ capability probe ══════════
def test_probe_reports_an_accessible_source_with_freshness() -> None:
    registry = default_registry()
    source = registry.get("query_history")
    recent = NOW - timedelta(minutes=30)
    runner = FakeRunner({"MAX(START_TIME)": [(recent, 12345)]})

    probe = probe_source(runner, source, now=NOW)
    assert probe.status is ProbeStatus.ACCESSIBLE
    assert probe.row_count == 12345
    assert probe.freshness_minutes == pytest.approx(30.0)
    assert probe.remediation == []
    assert not probe.stale


def test_probe_flags_a_stale_source_without_calling_it_missing() -> None:
    source = default_registry().get("query_history")  # 45-minute documented latency
    old = NOW - timedelta(hours=12)
    probe = probe_source(FakeRunner({"MAX(START_TIME)": [(old, 5)]}), source, now=NOW)
    assert probe.status is ProbeStatus.ACCESSIBLE
    assert probe.stale is True


def test_probe_turns_a_permission_error_into_a_grant_statement() -> None:
    """The whole point of the probe: a blocked source explains its own fix."""
    source = default_registry().get("access_history")
    runner = FakeRunner(
        {
            "SELECT 1 FROM": Exception(
                "002003: SQL compilation error: Object does not exist or not authorized"
            )
        }
    )
    probe = probe_source(runner, source, now=NOW)
    assert probe.status is ProbeStatus.MISSING_GRANT
    assert probe.remediation
    assert "GRANT DATABASE ROLE SNOWFLAKE.GOVERNANCE_VIEWER" in probe.remediation[0]


def test_probe_distinguishes_an_edition_gate_from_a_missing_grant() -> None:
    source = default_registry().get("access_history")
    runner = FakeRunner({"SELECT 1 FROM": Exception("Unsupported feature for this edition")})
    probe = probe_source(runner, source, now=NOW)
    assert probe.status is ProbeStatus.NOT_APPLICABLE
    assert probe.remediation == []  # a grant would not fix an edition gate


def test_probe_reports_an_empty_source_as_empty_not_broken() -> None:
    source = default_registry().get("query_history")
    probe = probe_source(FakeRunner({"MAX(START_TIME)": [(None, 0)]}), source, now=NOW)
    assert probe.status is ProbeStatus.EMPTY
    assert probe.remediation  # explains that this may be expected


def test_probe_queries_are_cheap() -> None:
    """A probe must never scan a retention window on a customer's warehouse."""
    source = default_registry().get("query_history")
    runner = FakeRunner({"MAX(START_TIME)": [(NOW, 1)]})
    probe_source(runner, source, now=NOW)

    access, freshness = runner.executed
    assert "LIMIT 1" in access
    assert f"-{PROBE_WINDOW_DAYS}" in freshness  # bounded lookback


def test_probe_never_raises_on_an_unexpected_driver_error() -> None:
    source = default_registry().get("query_history")
    probe = probe_source(FakeRunner({"SELECT 1 FROM": Exception("kaboom")}), source, now=NOW)
    assert probe.status is ProbeStatus.ERROR
    assert probe.error is not None


def test_coverage_and_grants_report_ranks_the_fixes() -> None:
    registry = default_registry()
    denied = Exception("Object does not exist or not authorized")
    # Everything governance-gated is blocked; everything else reads.
    runner = _SelectiveRunner(
        blocked_prefixes=("SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY",), error=denied
    )

    report = probe_all(runner, registry, account="acme", role=DEFAULT_READER_ROLE, now=NOW)
    assert report.sources
    assert report.blocked
    assert report.suggested_grants
    assert "GRANT DATABASE ROLE" in report.suggested_grants[0]
    assert 0 < report.coverage_pct < 100
    assert "need a grant" in report.summary()


def test_full_access_report_says_so_plainly() -> None:
    runner = FakeRunner(default=[(NOW, 10)])
    report = probe_all(runner, default_registry(), now=NOW)
    assert report.blocked == []
    assert report.suggested_grants == []
    assert "All" in report.summary()


class _KeyResolver:
    """Returns a valid throwaway RSA key so key loading can be exercised."""

    _pem: str | None = None

    def resolve(self, reference: str) -> str:
        if _KeyResolver._pem is None:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import rsa

            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            _KeyResolver._pem = key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode()
        return _KeyResolver._pem


class _SelectiveRunner:
    """Blocks specific objects, reads everything else."""

    def __init__(self, blocked_prefixes: tuple[str, ...], error: Exception) -> None:
        self.blocked_prefixes = blocked_prefixes
        self.error = error

    def fetch(self, sql: str) -> list[tuple[Any, ...]]:
        upper = sql.upper()
        if any(prefix.upper() in upper for prefix in self.blocked_prefixes):
            raise self.error
        return [(NOW, 10)]


# ═══════════════════════════════════════════ organization-wide probing ═══════
def _org_runner(*, organization_granted: bool) -> FakeRunner:
    """An account that may or may not hold ORGANIZATION_USAGE."""
    denied = Exception("SQL compilation error: Object does not exist or not authorized")
    responses: dict[str, Any] = {
        "MAX(": [(datetime.now(tz=UTC), 1_000)],
    }
    if not organization_granted:
        responses["ORGANIZATION_USAGE"] = denied
    return FakeRunner(responses)


def test_an_organization_probe_reports_each_account_separately() -> None:
    """The enterprise onboarding question: which accounts can you see, how deeply?

    A single overall percentage would hide the shape that matters — billing
    rolling up from one account while another has no detail at all.
    """
    report = probe_organization(
        {
            "ACME_PROD": _org_runner(organization_granted=True),
            "ACME_ANALYTICS": _org_runner(organization_granted=False),
        },
        organization="ACME_GROUP",
        organization_reader="ACME_PROD",
    )

    assert [r.account for r in report.accounts] == ["ACME_PROD", "ACME_ANALYTICS"]
    prod = report.account("ACME_PROD")
    analytics = report.account("ACME_ANALYTICS")
    assert prod is not None and analytics is not None
    # The account holding ORGANIZATION_USAGE sees strictly more than the one
    # that does not, and the report shows the difference per account.
    assert prod.coverage_pct > analytics.coverage_pct
    assert report.organization_wide_sources_available


def test_an_account_that_is_billed_but_not_connected_is_named() -> None:
    """The gap most easily mistaken for "that account is quiet".

    An unconnected account still appears in every billing roll-up, so its spend
    is counted while its queries are invisible. Saying nothing would let a
    reviewer read the silence as an idle account.
    """
    report = probe_organization(
        {"ACME_PROD": _org_runner(organization_granted=True)},
        organization="ACME_GROUP",
        organization_reader="ACME_PROD",
        known_accounts=["ACME_PROD", "ACME_ANALYTICS", "ACME_DEV"],
    )

    assert report.unconnected_accounts == ["ACME_ANALYTICS", "ACME_DEV"]
    summary = report.summary()
    assert "ACME_ANALYTICS" in summary and "ACME_DEV" in summary
    assert "spend is counted" in summary


def test_an_organization_with_no_usage_grant_says_roll_ups_are_unavailable() -> None:
    """R3: an unavailable capability explains itself and names the fix."""
    report = probe_organization(
        {"ACME_PROD": _org_runner(organization_granted=False)},
        organization="ACME_GROUP",
        organization_reader="ACME_PROD",
    )
    assert not report.organization_wide_sources_available
    assert "ORGANIZATION_USAGE_VIEWER" in report.summary()


def test_an_unreachable_account_is_counted_but_not_treated_as_covered() -> None:
    report = probe_organization(
        {"ACME_PROD": _org_runner(organization_granted=True)},
        organization="ACME_GROUP",
        organization_reader="ACME_PROD",
        unreachable={"ACME_EU": "connection timed out"},
        known_accounts=["ACME_PROD", "ACME_EU"],
    )
    assert report.unreachable_accounts == {"ACME_EU": "connection timed out"}
    # It is not reported as an unconnected account: it *is* configured, it just
    # did not answer. Those need different remedies.
    assert report.unconnected_accounts == []
    assert "1 of 2 configured account(s) reachable" in report.summary()


def test_a_freshness_row_of_an_unexpected_shape_does_not_crash_the_probe() -> None:
    """Onboarding must degrade, not raise, over a connection that works."""
    runner = FakeRunner({"MAX(": [(1,)]})  # one column where two are expected
    report = probe_all(runner, account="ACME_PROD")
    assert report.accessible  # still readable
    assert all(probe.freshness_minutes is None for probe in report.sources)


def test_a_declared_source_scope_agrees_with_the_schema_it_reads() -> None:
    """Two statements of the same fact must not be allowed to drift apart.

    A source declaring itself account-scoped while reading
    `ORGANIZATION_USAGE` would make the organization probe report cross-account
    roll-ups as unavailable when they work, or the reverse — and neither shows
    up as an error anywhere.
    """
    from snowobs_live.probe import _is_organization_scoped
    from snowobs_semantics.registry import default_registry

    for source in default_registry():
        by_object = "ORGANIZATION_USAGE" in source.snowflake_object.upper()
        assert _is_organization_scoped(source.id) == by_object, (
            f"{source.id} declares a scope that disagrees with {source.snowflake_object}"
        )
