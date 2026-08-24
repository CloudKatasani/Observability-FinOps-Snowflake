"""Golden-file tests for every artifact emitter (§13.4).

Emission is pure, so a change to any rendering shows up here as a diff in
review rather than as a subtly different script in somebody's Snowflake account.

Regenerate deliberately with ``SNOWOBS_UPDATE_GOLDEN=1 pytest
packages/dataproducts`` and read the diff before committing it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from snowobs_dataproducts.contracts import build_contract
from snowobs_dataproducts.emitters import (
    EmitterError,
    SnowflakeTarget,
    audit_generated_sql,
    ident,
)
from snowobs_dataproducts.emitters.agent_spec import build_specification, emit_agent_spec
from snowobs_dataproducts.emitters.cortex_search import NEVER_INDEXED, emit_cortex_search
from snowobs_dataproducts.emitters.dbt import emit_dbt_project
from snowobs_dataproducts.emitters.ddl import (
    emit_foundations_ddl,
    emit_grants,
    emit_policies,
    emit_published_views,
)
from snowobs_dataproducts.emitters.listing import emit_listing_ddl, emit_listing_manifest
from snowobs_dataproducts.emitters.semantic_view import emit_semantic_view, verified_queries
from snowobs_dataproducts.registry import load_products

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
UPDATE = os.environ.get("SNOWOBS_UPDATE_GOLDEN") == "1"

#: One product per emitter shape: a multi-relation product with no search, a
#: product with a search service, and a restricted product with masking.
GOLDEN_PRODUCTS = ("finops_chargeback", "pipeline_health", "access_governance")


@pytest.fixture(scope="module")
def products():
    return load_products()


def _check_all(rendered: dict[str, str]) -> None:
    """Compare a whole artifact set against its pinned files.

    Missing files are all written in one pass before skipping, so seeding a new
    emitter's golden set does not take one run per file.
    """
    created = []
    for name, body in sorted(rendered.items()):
        path = GOLDEN_DIR / name
        if UPDATE or not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
            created.append(name)
    if created and not UPDATE:
        pytest.skip(f"created {len(created)} missing golden file(s): {created[0]} …")
    for name, body in sorted(rendered.items()):
        path = GOLDEN_DIR / name
        assert body == path.read_text(encoding="utf-8"), (
            f"{name} changed. If intended, regenerate with SNOWOBS_UPDATE_GOLDEN=1 and "
            f"review the diff."
        )


def _artifacts(product_id: str, products) -> dict[str, str]:
    product = products.get(product_id)
    contract = build_contract(product)
    manifest = emit_listing_manifest(product, contract)
    files: dict[str, str] = {
        f"{product_id}/published_views.sql": emit_published_views(product, contract),
        f"{product_id}/semantic_view.sql": emit_semantic_view(product, contract),
        f"{product_id}/listing_manifest.yaml": manifest,
        f"{product_id}/share_and_listing.sql": emit_listing_ddl(product, contract, manifest),
        f"{product_id}/agent.sql": emit_agent_spec(product, contract),
        f"{product_id}/grants.sql": emit_grants(product, contract),
    }
    policies = emit_policies(product, contract)
    if policies is not None:
        files[f"{product_id}/policies.sql"] = policies
    if product.search is not None:
        files[f"{product_id}/cortex_search.sql"] = emit_cortex_search(product, contract)
    for name, body in emit_dbt_project(product, contract).files.items():
        files[f"{product_id}/{name}"] = body
    return files


# ═══════════════════════════════════════════════════════════ golden files ════
def test_foundations_ddl_is_pinned() -> None:
    _check_all({"foundations.sql": emit_foundations_ddl()})


@pytest.mark.parametrize("product_id", GOLDEN_PRODUCTS)
def test_every_artifact_is_pinned(product_id: str, products) -> None:
    _check_all(_artifacts(product_id, products))


def test_no_golden_file_outlives_its_emitter(products) -> None:
    """A stale golden file for an artifact nobody emits any more is dead weight."""
    if not GOLDEN_DIR.is_dir():
        pytest.skip("no golden files yet")
    expected = {"foundations.sql"}
    for product_id in GOLDEN_PRODUCTS:
        expected.update(_artifacts(product_id, products))
    found = {str(path.relative_to(GOLDEN_DIR)) for path in GOLDEN_DIR.rglob("*") if path.is_file()}
    assert found - expected == set()


# ═══════════════════════════════════════════════════════════ determinism ═════
@pytest.mark.parametrize("product_id", GOLDEN_PRODUCTS)
def test_emission_is_deterministic(product_id: str, products) -> None:
    assert _artifacts(product_id, products) == _artifacts(product_id, products)


# ══════════════════════════════════════════════════════ safety properties ════
def test_no_generated_sql_grants_anything_blanket(products) -> None:
    """§27.3: no IMPORTED PRIVILEGES, no ACCOUNTADMIN, no ALL PRIVILEGES."""
    for product in products:
        contract = build_contract(product)
        manifest = emit_listing_manifest(product, contract)
        scripts = [
            emit_foundations_ddl(),
            emit_published_views(product, contract),
            emit_semantic_view(product, contract),
            emit_listing_ddl(product, contract, manifest),
            emit_agent_spec(product, contract),
            emit_grants(product, contract),
        ]
        for script in scripts:
            assert audit_generated_sql(script) == []


def test_the_audit_catches_a_blanket_grant() -> None:
    assert audit_generated_sql("GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE X;")
    assert audit_generated_sql("GRANT ROLE ACCOUNTADMIN TO ROLE X;")
    # A comment naming the role is not a grant of it.
    assert audit_generated_sql("-- never grant IMPORTED PRIVILEGES here") == []


def test_the_share_never_reaches_curated_or_raw(products) -> None:
    for product in products:
        contract = build_contract(product)
        ddl = emit_listing_ddl(product, contract, emit_listing_manifest(product, contract))
        for line in ddl.splitlines():
            if line.strip().upper().startswith("GRANT") and "TO SHARE" in line.upper():
                assert "PUBLISHED" in line or "SEMANTIC" in line or "DATABASE" in line


def test_the_listing_is_created_unpublished(products) -> None:
    """R8: creating the listing and offering it to the org are separate acts."""
    product = products.get("finops_chargeback")
    contract = build_contract(product)
    ddl = emit_listing_ddl(product, contract, emit_listing_manifest(product, contract))
    assert "PUBLISH = FALSE" in ddl
    assert "PUBLISH = TRUE" not in ddl


def test_published_views_carry_no_row_cap(products) -> None:
    """The guard's interactive LIMIT must not truncate a published relation."""
    for product in products:
        sql = emit_published_views(product, build_contract(product))
        assert "LIMIT " not in sql.upper()


def test_published_views_are_secure(products) -> None:
    for product in products:
        sql = emit_published_views(product, build_contract(product))
        assert sql.count("CREATE OR REPLACE SECURE VIEW") == len(build_contract(product).datasets)


def test_the_semantic_view_uses_the_ai_verified_queries_clause(products) -> None:
    """ASSUMPTIONS A-8: the clause is AI_VERIFIED_QUERIES, not VERIFIED_QUERIES."""
    sql = emit_semantic_view(
        products.get("finops_chargeback"), build_contract(products.get("finops_chargeback"))
    )
    assert "AI_VERIFIED_QUERIES (" in sql
    assert "\n  VERIFIED_QUERIES" not in sql


def test_the_semantic_view_orders_its_clauses_as_the_grammar_requires(products) -> None:
    sql = emit_semantic_view(
        products.get("pipeline_health"), build_contract(products.get("pipeline_health"))
    )
    positions = [
        sql.index("\n  TABLES ("),
        sql.index("\n  FACTS ("),
        sql.index("\n  DIMENSIONS ("),
        sql.index("\n  METRICS ("),
        sql.index("\n  COMMENT = "),
        sql.index("\n  AI_VERIFIED_QUERIES ("),
    ]
    assert positions == sorted(positions)


def test_non_additive_measures_are_facts_not_metrics(products) -> None:
    """A ratio published as a METRIC would be silently re-averaged (R12)."""
    product = products.get("warehouse_efficiency")
    sql = emit_semantic_view(product, build_contract(product))
    facts = sql[sql.index("\n  FACTS (") : sql.index("\n  DIMENSIONS (")]
    metrics = sql[sql.index("\n  METRICS (") : sql.index("\n  COMMENT = ")]
    assert "WH_UTILISATION_PCT" in facts
    assert "WH_UTILISATION_PCT" not in metrics
    assert "SUM(WH_ZOMBIE_CREDITS)" in metrics


def test_every_metric_contributes_at_least_one_verified_query(products) -> None:
    for product in products:
        contract = build_contract(product)
        queries = verified_queries(product, contract)
        covered = {q.name.removeprefix("vq_").rsplit("_", 1)[0].upper() for q in queries}
        for dataset in contract.datasets:
            for column in dataset.columns:
                if column.metric_id is not None:
                    assert column.name in covered, f"{product.id}: {column.name} has no question"


def test_the_semantic_view_carries_a_useful_number_of_verified_queries(products) -> None:
    """The skill's cheapest accuracy lever: 10-20 gold-standard questions."""
    for product in products:
        assert len(verified_queries(product, build_contract(product))) >= 9


def test_verified_queries_read_the_products_own_views(products) -> None:
    target = SnowflakeTarget()
    for product in products:
        contract = build_contract(product)
        names = {target.view(d.name) for d in contract.datasets}
        for query in verified_queries(product, contract):
            assert any(name in query.sql for name in names)


def test_cortex_search_is_refused_for_a_product_without_free_text(products) -> None:
    product = products.get("finops_chargeback")
    with pytest.raises(EmitterError, match="no search column"):
        emit_cortex_search(product, build_contract(product))


def test_cortex_search_refuses_a_sensitive_column(products) -> None:
    product = products.get("access_governance")
    hijacked = product.model_copy(
        update={"search": product.search.model_copy(update={"column": "USER"})}
    )
    with pytest.raises(EmitterError, match="not marked searchable"):
        emit_cortex_search(hijacked, build_contract(hijacked))


def test_query_text_is_never_indexable() -> None:
    """R2/§27.5: query text does not leave the account through a search index."""
    assert "QUERY_TEXT" in NEVER_INDEXED


def test_cortex_search_bounds_its_window(products) -> None:
    product = products.get("pipeline_health")
    sql = emit_cortex_search(product, build_contract(product))
    assert f"DATEADD(day, -{product.search.window_days}, CURRENT_DATE())" in sql
    assert "TARGET_LAG = " in sql


def test_policies_are_emitted_only_where_they_are_needed(products) -> None:
    assert (
        emit_policies(
            products.get("finops_chargeback"), build_contract(products.get("finops_chargeback"))
        )
        is None
    )
    restricted = products.get("access_governance")
    policies = emit_policies(restricted, build_contract(restricted))
    assert policies is not None
    assert "CREATE MASKING POLICY" in policies
    assert "CREATE ROW ACCESS POLICY" in policies
    for column in restricted.sensitive_columns:
        assert f"MODIFY COLUMN {column}" in policies


def test_grants_name_every_object_individually(products) -> None:
    """GRANT ... ON ALL VIEWS would widen the product on the next schema change."""
    for product in products:
        contract = build_contract(product)
        sql = emit_grants(product, contract)
        statements = [line for line in sql.splitlines() if not line.strip().startswith("--")]
        assert not any("ON ALL VIEWS" in line for line in statements)
        for dataset in contract.datasets:
            assert dataset.name in sql


def test_the_agent_spec_is_scoped_to_the_products_metrics(products) -> None:
    product = products.get("pipeline_health")
    contract = build_contract(product)
    spec = build_specification(product, contract)
    analyst = spec["tool_resources"][f"{product.id}_analyst"]
    assert analyst["semantic_view"].endswith(product.slug_upper)
    tool_types = {tool["tool_spec"]["type"] for tool in spec["tools"]}
    assert tool_types == {"cortex_analyst_text_to_sql", "cortex_search", "sql_exec"}
    response = spec["instructions"]["response"]
    assert "must come from a tool result" in response
    assert str(contract.freshness_guarantee_minutes) in response


def test_the_agent_spec_pins_no_unverified_model_by_default(products) -> None:
    """ASSUMPTIONS U-4: model availability varies by region; do not hard-code one."""
    product = products.get("pipeline_health")
    spec = build_specification(product, build_contract(product))
    assert "models" not in spec
    pinned = build_specification(
        product,
        build_contract(product),
        target=SnowflakeTarget(orchestration_model="claude-4-5-sonnet"),
    )
    assert pinned["models"] == {"orchestration": "claude-4-5-sonnet"}


def test_a_product_without_search_gets_no_search_tool(products) -> None:
    product = products.get("finops_chargeback")
    spec = build_specification(product, build_contract(product))
    assert "cortex_search" not in {tool["tool_spec"]["type"] for tool in spec["tools"]}


def test_dbt_models_reference_dbt_sources(products) -> None:
    product = products.get("finops_chargeback")
    project = emit_dbt_project(product, build_contract(product))
    model = project[f"dbt/models/{product.id}/{product.id}_cost_daily.sql"]
    assert "{{ source('account_usage', 'METERING_DAILY_HISTORY') }}" in model
    assert "FROM metering_daily_history" not in model


def test_dbt_models_carry_lineage_columns(products) -> None:
    product = products.get("pipeline_health")
    project = emit_dbt_project(product, build_contract(product))
    for name, body in project.files.items():
        if name.startswith(f"dbt/models/{product.id}/") and name.endswith(".sql"):
            assert "_LOADED_AT" in body
            assert "_SOURCE_VIEW" in body
            assert "_BATCH_ID" in body


def test_dbt_ships_a_test_for_every_contracted_promise(products) -> None:
    product = products.get("pipeline_health")
    contract = build_contract(product)
    names = set(emit_dbt_project(product, contract).names)
    for dataset in contract.datasets:
        model = dataset.name.removeprefix("V_").lower()
        assert f"dbt/tests/{model}_grain_is_unique.sql" in names
        assert f"dbt/tests/{model}_row_expectations.sql" in names
        assert f"dbt/tests/{model}_freshness.sql" in names


def test_unsafe_identifiers_are_refused() -> None:
    with pytest.raises(EmitterError, match="unsafe SQL identifier"):
        ident("V_X; DROP TABLE Y")
    assert ident("V_FINOPS_CHARGEBACK_COST_DAILY") == "V_FINOPS_CHARGEBACK_COST_DAILY"


def test_the_manifest_states_the_freshness_guarantee(products) -> None:
    """R7/§27.9: a marketplace tile without its latency floor is a bare figure."""
    for product in products:
        contract = build_contract(product)
        manifest = emit_listing_manifest(product, contract)
        assert "Freshness guarantee:" in manifest
        assert "organization_profile: INTERNAL" in manifest
