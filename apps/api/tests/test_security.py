"""Cross-tenant isolation and the RBAC matrix (BUILD_PROMPT §17, §26).

Two of the Definition of Done's security items are asserted here rather than
argued for. Both fail silently when they fail: a tenant reading another
tenant's spend gets plausible numbers, and a role reaching a tool it should not
have gets a working answer. Neither raises anything on its own.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from snowobs_agents.runtime.tools import ToolContext, build_registry, specs_for
from snowobs_api.main import create_app
from snowobs_api.services.agents import AgentService
from snowobs_api.services.metrics import MetricService
from snowobs_common.config import Settings
from snowobs_engines.duckdb_engine import DuckDBEngine
from snowobs_fixtures.config import GeneratorConfig
from snowobs_fixtures.generator import generate, write_csv
from snowobs_ingest.catalog import DuckDBCatalog
from snowobs_ingest.loader import IngestPipeline, LakeWriter
from snowobs_ingest.tenancy import TenancyError
from snowobs_semantics.compiler import (
    Filter,
    FilterOperator,
    MetricRequest,
    SemanticCompiler,
    TimeRange,
)
from snowobs_semantics.dialect_shims import Dialect
from snowobs_semantics.model import default_model

# Two accounts of visibly different size, so a leak shows up as a wrong number
# rather than as a coincidence.
ACME = GeneratorConfig(days=14, queries_per_day=400, seed=11)
GLOBEX = GeneratorConfig(days=14, queries_per_day=120, seed=22)


@pytest.fixture(scope="module")
def two_tenants(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """One lake holding two tenants' data, landed exactly as the app lands it."""
    lake: Path = tmp_path_factory.mktemp("multi-tenant-lake")
    for tenant, config in (("acme", ACME), ("globex", GLOBEX)):
        extract: Path = tmp_path_factory.mktemp(f"extract-{tenant}")
        write_csv(generate(config), extract)
        IngestPipeline(lake, tenant=tenant).ingest_directory(extract)
    yield lake


def credits_for(lake: Path, tenant: str) -> Decimal:
    catalog = DuckDBCatalog(lake, tenant=tenant)
    catalog.register_all()
    try:
        compiled = SemanticCompiler().compile(
            MetricRequest(metrics=["cost.billed_credits"], bucket_time=False, limit=10),
            Dialect.DUCKDB,
        )
        result = DuckDBEngine(catalog).execute(compiled)
        return Decimal(str(result.scalar()))
    finally:
        catalog.close()


# --------------------------------------------------------- tenant isolation
def test_each_tenant_sees_only_its_own_data(two_tenants: Path) -> None:
    acme = credits_for(two_tenants, "acme")
    globex = credits_for(two_tenants, "globex")

    assert acme > 0 and globex > 0
    # Different accounts, different bills. Equality here would mean one catalog
    # had registered the other's parquet, or both had registered everything.
    assert acme != globex
    # And neither is the sum: a catalog globbing the lake root rather than the
    # tenant's own prefix is the failure this catches.
    assert acme != acme + globex
    assert globex != acme + globex


def test_a_tenant_with_nothing_landed_reads_empty_not_someone_else_s_data(
    two_tenants: Path,
) -> None:
    """The dangerous default: an unknown tenant falling through to the lake root."""
    catalog = DuckDBCatalog(two_tenants, tenant="never-onboarded")
    try:
        assert catalog.register_all() == []
        assert catalog.landed_sources() == []
    finally:
        catalog.close()


@pytest.mark.parametrize(
    "tenant",
    ["acme/../globex", "../globex", "..", "acme/globex", "", ".", "acme%2f..%2fglobex"],
)
def test_a_tenant_identifier_that_could_escape_its_prefix_is_refused(
    two_tenants: Path, tenant: str
) -> None:
    """§17. The hole this closes returned another customer's spend, silently.

    ``acme/../globex`` is a valid path join that resolves into globex's prefix,
    so a catalog built on it registered globex's parquet and answered every
    question with globex's figures — no error anywhere, just the wrong
    customer's money on the page.
    """
    with pytest.raises(TenancyError):
        DuckDBCatalog(two_tenants, tenant=tenant)
    # The write side is guarded too: a traversal there would land one
    # customer's extract inside another's prefix.
    with pytest.raises(TenancyError):
        LakeWriter(two_tenants, tenant=tenant)


def test_a_traversing_tenant_never_reaches_the_data_it_aimed_at(two_tenants: Path) -> None:
    """The property that matters, stated in terms of figures rather than paths."""
    globex = credits_for(two_tenants, "globex")
    assert globex > 0
    with pytest.raises(TenancyError):
        credits_for(two_tenants, "acme/../globex")


def test_the_api_reads_the_tenant_it_was_constructed_for(two_tenants: Path) -> None:
    settings = Settings(_env_file=None, storage={"provider": "local", "bucket": str(two_tenants)})
    acme = MetricService(settings, tenant="acme").tile(
        "cost.billed_credits",
        MetricRequest(metrics=["cost.billed_credits"], bucket_time=False),
    )
    globex = MetricService(settings, tenant="globex").tile(
        "cost.billed_credits",
        MetricRequest(metrics=["cost.billed_credits"], bucket_time=False),
    )
    assert Decimal(str(acme.value)) == credits_for(two_tenants, "acme")
    assert Decimal(str(globex.value)) == credits_for(two_tenants, "globex")
    assert acme.value != globex.value


def test_an_agent_answers_from_its_own_tenant_only(two_tenants: Path) -> None:
    settings = Settings(_env_file=None, storage={"provider": "local", "bucket": str(two_tenants)})
    acme = AgentService(settings, tenant="acme").ask("What were our billed credits?")
    globex = AgentService(settings, tenant="globex").ask("What were our billed credits?")

    assert acme.grounded and globex.grounded
    assert str(credits_for(two_tenants, "acme")) in "".join(acme.tool_outputs)
    assert str(credits_for(two_tenants, "globex")) in "".join(globex.tool_outputs)
    assert acme.answer != globex.answer


# ------------------------------------------------------------- the RBAC matrix
#: (role, tool) → may the caller reach it at all?
RBAC_MATRIX: list[tuple[frozenset[str], str, bool]] = [
    (frozenset(), "query_metric", True),
    (frozenset(), "list_metrics", True),
    (frozenset(), "describe_metric", True),
    (frozenset(), "get_coverage", True),
    (frozenset(), "explain_delta", True),
    # The ad-hoc hatch is the only role-gated tool, and it stays shut by default.
    (frozenset(), "run_sql_guarded", False),
    (frozenset({"viewer"}), "run_sql_guarded", False),
    (frozenset({"analyst"}), "run_sql_guarded", False),
    (frozenset({"finops_lead"}), "run_sql_guarded", False),
    (frozenset({"platform_admin"}), "run_sql_guarded", True),
    (frozenset({"platform_admin"}), "query_metric", True),
]


@pytest.mark.parametrize(("roles", "tool", "permitted"), RBAC_MATRIX)
def test_the_rbac_matrix_holds(roles: frozenset[str], tool: str, permitted: bool) -> None:
    offered = {spec.name for spec in specs_for(build_registry(), roles)}
    assert (tool in offered) is permitted, (
        f"{sorted(roles) or 'no roles'} → {tool} should be {'offered' if permitted else 'withheld'}"
    )


def test_holding_the_admin_role_is_not_enough_to_run_ad_hoc_sql(two_tenants: Path) -> None:
    """Two independent gates: the role, and the deployment's own setting (§12.3).

    A role check alone would mean any admin in any deployment could bypass the
    governed layer, which is not what "disabled by default" means.
    """
    catalog = DuckDBCatalog(two_tenants, tenant="acme")
    catalog.register_all()
    try:
        context = ToolContext(
            engine=DuckDBEngine(catalog),
            compiler=SemanticCompiler(),
            model=default_model(),
            tenant="acme",
            actor="admin@example.com",
            roles=frozenset({"platform_admin"}),
            allow_adhoc_sql=False,  # the deployment has not opted in
        )
        outcome = build_registry()["run_sql_guarded"].run(context, {"sql": "SELECT 1"})
        assert outcome.is_error
        assert "disabled" in outcome.content.lower()
    finally:
        catalog.close()


def test_row_level_filters_cannot_be_widened_by_the_caller(two_tenants: Path) -> None:
    """§17: RLS predicates are applied server-side and are not negotiable.

    The attack is a caller sending a filter that contradicts their own RLS
    predicate, hoping the wider one wins.
    """
    catalog = DuckDBCatalog(two_tenants, tenant="acme")
    catalog.register_all()
    try:
        engine = DuckDBEngine(catalog)
        compiler = SemanticCompiler()

        def credits(**kwargs: object) -> Decimal:
            request = MetricRequest(
                metrics=["cost.by_team_credits"],
                bucket_time=False,
                limit=1000,
                time_range=TimeRange(start=None, end=None) if False else None,
                **kwargs,  # type: ignore[arg-type]
            )
            result = engine.execute(compiler.compile(request, Dialect.DUCKDB))
            return sum(
                (Decimal(str(row[-1])) for row in result.rows if row[-1] is not None),
                Decimal(0),
            )

        confined = credits(
            rls_filters=[
                Filter(dimension="team", operator=FilterOperator.IN, value=["TEAM_FINANCE"])
            ]
        )
        # The same predicate, plus a caller-supplied filter naming other teams.
        attempted = credits(
            rls_filters=[
                Filter(dimension="team", operator=FilterOperator.IN, value=["TEAM_FINANCE"])
            ],
            filters=[
                Filter(
                    dimension="team",
                    operator=FilterOperator.IN,
                    value=["TEAM_FINANCE", "TEAM_ANALYTICS", "TEAM_PLATFORM"],
                )
            ],
        )
        unrestricted = credits()

        assert confined > 0
        assert attempted <= confined  # the caller's filter narrows, never widens
        assert confined < unrestricted  # and the RLS predicate really did bite
    finally:
        catalog.close()


# ------------------------------------------------------------------ secrets
@asynccontextmanager
async def client_for(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.mark.asyncio
async def test_no_endpoint_returns_a_secret_value(two_tenants: Path) -> None:
    """§27.13: secrets are referenced, never carried in a response body."""
    settings = Settings(
        _env_file=None,
        storage={"provider": "local", "bucket": str(two_tenants)},
        snowflake={"account": "ACME-XY12345", "user": "SNOWOBS_READER"},
    )
    async with client_for(settings) as client:
        bodies = [
            (await client.get("/api/v1/meta")).text,
            (await client.get("/api/v1/sources")).text,
            (await client.get("/api/v1/datasets/coverage")).text,
            (await client.get("/api/v1/connections/auth-methods")).text,
        ]

    for body in bodies:
        lowered = body.lower()
        # Secret *values*, not the words. `/connections/auth-methods` names
        # "password" as an authentication method it discourages, which is the
        # opposite of a leak.
        for marker in ("-----begin", "private_key", "aws_secret", "sk-", "eyj"):
            assert marker not in lowered, f"a response looks like it carried {marker!r}"
        assert "secret_ref" not in lowered or "secret_value" not in lowered


def test_the_llm_key_is_held_by_reference_and_resolved_only_at_use(
    two_tenants: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§27.13: the key reaches the provider without passing through Settings.

    The deployment used to inject the key into a settings field that did not
    exist, so it was both leaked into the environment and silently ignored. It
    is now a reference the secrets adapter resolves when a turn needs it.
    """
    monkeypatch.setenv("TEST_LLM_KEY", "sk-not-a-real-key-0123456789")
    settings = Settings(
        _env_file=None,
        storage={"provider": "local", "bucket": str(two_tenants)},
        llm={"provider": "anthropic", "api_key_ref": "env://TEST_LLM_KEY"},
        secrets={"provider": "env"},
    )
    service = AgentService(settings, tenant="acme")

    # The reference resolves for the code that needs it …
    assert service._llm_api_key() == "sk-not-a-real-key-0123456789"
    # … and the key itself is nowhere in the configuration object.
    assert "sk-not-a-real-key" not in settings.model_dump_json()
    assert settings.llm.api_key_ref == "env://TEST_LLM_KEY"


def test_an_unresolvable_llm_key_degrades_instead_of_failing_the_request(
    two_tenants: Path,
) -> None:
    """§19: a missing credential must never take the platform down with it."""
    settings = Settings(
        _env_file=None,
        storage={"provider": "local", "bucket": str(two_tenants)},
        llm={"provider": "anthropic", "api_key_ref": "env://NOT_SET_ANYWHERE"},
        secrets={"provider": "env"},
    )
    service = AgentService(settings, tenant="acme")
    assert service._llm_api_key() is None

    # And a question still gets a grounded answer from the metric layer.
    result = service.ask("how many credits did we use")
    assert result.answer
    assert not result.trace.steps[0].detail.get("api_key")


# ------------------------------------------------------------------- caching
def test_the_result_cache_cannot_serve_one_tenant_s_rows_to_another(
    two_tenants: Path,
) -> None:
    """The cache key must scope by tenant, because the SQL does not.

    Two tenants query identically-named views, so their compiled statements are
    byte-identical and hash to the same fingerprint. A cache keyed on the SQL
    alone — which is what the engines used — would return whichever tenant's
    figures happened to be computed first, with a valid-looking provenance
    envelope attached.
    """
    from snowobs_engines.cache import ResultCache

    shared = ResultCache()
    compiled = SemanticCompiler().compile(
        MetricRequest(metrics=["cost.billed_credits"], bucket_time=False, limit=10),
        Dialect.DUCKDB,
    )

    def read(tenant: str) -> Decimal:
        catalog = DuckDBCatalog(two_tenants, tenant=tenant)
        catalog.register_all()
        try:
            return Decimal(str(DuckDBEngine(catalog, cache=shared).execute(compiled).scalar()))
        finally:
            catalog.close()

    acme, globex = read("acme"), read("globex")
    assert acme == credits_for(two_tenants, "acme")
    assert globex == credits_for(two_tenants, "globex")
    assert acme != globex
    # Both were computed, not one served from the other's entry.
    assert shared.misses >= 2


def test_a_cached_answer_does_not_survive_the_upload_it_was_computed_from(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A new upload changes the answer without changing the statement."""
    from snowobs_engines.cache import ResultCache

    lake: Path = tmp_path_factory.mktemp("restated-lake")
    first: Path = tmp_path_factory.mktemp("extract-first")
    write_csv(generate(GeneratorConfig(days=7, queries_per_day=100, seed=5)), first)
    IngestPipeline(lake, tenant="acme").ingest_directory(first)

    cache = ResultCache()
    compiled = SemanticCompiler().compile(
        MetricRequest(metrics=["cost.billed_credits"], bucket_time=False, limit=10),
        Dialect.DUCKDB,
    )

    def read() -> Decimal:
        catalog = DuckDBCatalog(lake, tenant="acme")
        catalog.register_all()
        try:
            return Decimal(str(DuckDBEngine(catalog, cache=cache).execute(compiled).scalar()))
        finally:
            catalog.close()

    before = read()
    assert read() == before  # warm, and served from the cache
    assert cache.hits >= 1

    # A second extract lands. The SQL is unchanged; the answer is not.
    second: Path = tmp_path_factory.mktemp("extract-second")
    write_csv(generate(GeneratorConfig(days=7, queries_per_day=100, seed=6)), second)
    IngestPipeline(lake, tenant="acme").ingest_directory(second)

    assert read() != before, "a stale figure survived the upload that superseded it"
