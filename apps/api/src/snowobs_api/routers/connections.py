"""LIVE-mode connection, probe, and provisioning endpoints (BUILD_PROMPT §15).

Connection *testing* is available before anything is saved, so an operator can
see the Coverage & Grants report — and the exact grants that would fix it —
without first committing a configuration.
"""

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from snowobs_api.deps import SettingsDep
from snowobs_live.connection import AuthMethod, ConnectionProfile, SnowflakeConnector
from snowobs_live.probe import ConnectionRunner, CoverageAndGrantsReport, probe_all
from snowobs_live.provisioning import (
    DEFAULT_READER_ROLE,
    DEFAULT_WAREHOUSE,
    generate_publisher_role_sql,
    generate_reader_role_sql,
)

router = APIRouter(prefix="/api/v1/connections", tags=["connections"])


class AuthMethodInfo(BaseModel):
    value: str
    discouraged: bool
    warning: str | None


class ConnectionTestRequest(BaseModel):
    """A connection to test. The secret is passed by reference, never inline."""

    account: str
    user: str
    auth: AuthMethod = AuthMethod.KEYPAIR
    secret_ref: str | None = None
    role: str | None = DEFAULT_READER_ROLE
    warehouse: str | None = DEFAULT_WAREHOUSE
    host: str | None = None

    def to_profile(self, query_tag_prefix: str, timeout_s: int) -> ConnectionProfile:
        return ConnectionProfile(
            account=self.account,
            user=self.user,
            auth=self.auth,
            secret_ref=self.secret_ref,
            role=self.role,
            warehouse=self.warehouse,
            host=self.host,
            query_tag_prefix=query_tag_prefix,
            statement_timeout_s=timeout_s,
        )


class SourceProbeResponse(BaseModel):
    source_id: str
    snowflake_object: str
    status: str
    required_db_role: str | None
    row_count: int | None
    max_timestamp: datetime | None
    freshness_minutes: float | None
    documented_latency_minutes: int
    stale: bool
    error: str | None
    remediation: list[str] = Field(default_factory=list)


class CoverageAndGrantsResponse(BaseModel):
    probed_at: datetime
    account: str
    role: str | None
    summary: str
    coverage_pct: float
    accessible_count: int
    blocked_count: int
    sources: list[SourceProbeResponse]
    #: The ranked "run these statements" list (R3, §7.2).
    suggested_grants: list[str]


class ProvisioningResponse(BaseModel):
    role: str
    warehouse: str
    sql: str
    grant_summary: list[str]
    notes: list[str]


@router.get("/auth-methods", response_model=list[AuthMethodInfo])
async def auth_methods() -> list[AuthMethodInfo]:
    """Supported authentication methods, with the discouraged ones marked (§7.2)."""
    return [
        AuthMethodInfo(value=method.value, discouraged=method.discouraged, warning=method.warning)
        for method in AuthMethod
    ]


@router.get("/provisioning/reader", response_model=ProvisioningResponse)
async def reader_provisioning(settings: SettingsDep) -> ProvisioningResponse:
    """The read-only provisioning script, for a human to review and run (R4)."""
    plan = generate_reader_role_sql(
        role=settings.snowflake.role or DEFAULT_READER_ROLE,
        warehouse=settings.snowflake.warehouse or DEFAULT_WAREHOUSE,
    )
    return ProvisioningResponse(
        role=plan.role,
        warehouse=plan.warehouse,
        sql=plan.sql,
        grant_summary=plan.grant_summary(),
        notes=plan.notes,
    )


@router.get("/provisioning/publisher", response_model=ProvisioningResponse)
async def publisher_provisioning() -> ProvisioningResponse:
    """The separate, write-scoped publisher role (R4, R8)."""
    plan = generate_publisher_role_sql()
    return ProvisioningResponse(
        role=plan.role,
        warehouse=plan.warehouse,
        sql=plan.sql,
        grant_summary=plan.grant_summary(),
        notes=plan.notes,
    )


@router.post("/probe", response_model=CoverageAndGrantsResponse)
async def probe(payload: ConnectionTestRequest, settings: SettingsDep) -> CoverageAndGrantsResponse:
    """Connect, probe every registered source, and report what is missing."""
    profile = payload.to_profile(
        settings.snowflake.query_tag_prefix, settings.snowflake.statement_timeout_s
    )
    connector = SnowflakeConnector(profile)
    connection = connector.connect(surface="probe")
    try:
        report = probe_all(ConnectionRunner(connection), account=profile.account, role=profile.role)
    finally:
        connection.close()
    return _to_response(report)


def _to_response(report: CoverageAndGrantsReport) -> CoverageAndGrantsResponse:
    return CoverageAndGrantsResponse(
        probed_at=report.probed_at,
        account=report.account,
        role=report.role,
        summary=report.summary(),
        coverage_pct=round(report.coverage_pct, 1),
        accessible_count=len(report.accessible),
        blocked_count=len(report.blocked),
        sources=[
            SourceProbeResponse(
                source_id=probe.source_id,
                snowflake_object=probe.snowflake_object,
                status=probe.status.value,
                required_db_role=probe.required_db_role,
                row_count=probe.row_count,
                max_timestamp=probe.max_timestamp,
                freshness_minutes=probe.freshness_minutes,
                documented_latency_minutes=probe.documented_latency_minutes,
                stale=probe.stale,
                error=probe.error,
                remediation=probe.remediation,
            )
            for probe in report.sources
        ],
        suggested_grants=report.suggested_grants,
    )
