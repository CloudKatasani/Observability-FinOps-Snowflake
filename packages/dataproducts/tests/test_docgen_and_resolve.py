"""Generated contract documentation, and the type inference underneath it.

``docs/DATA_CONTRACTS.md`` is a client deliverable and the column types in it are
inferred from the entity SQL rather than declared, so both are tested directly.
"""

from __future__ import annotations

import pytest

from snowobs_dataproducts.contracts import build_contract
from snowobs_dataproducts.docgen import render_contracts, write_contracts
from snowobs_dataproducts.model import RefreshCadence, Version
from snowobs_dataproducts.registry import load_products
from snowobs_dataproducts.resolve import (
    ColumnType,
    ProductResolutionError,
    compile_dataset,
    dimension_type,
    is_additive,
    metric_column,
    resolve_datasets,
    time_bucket_type,
    unbounded_sql,
)
from snowobs_semantics.dialect_shims import Dialect
from snowobs_semantics.model import default_model


@pytest.fixture(scope="module")
def products():
    return load_products()


@pytest.fixture(scope="module")
def document(products):
    return render_contracts(products)


# ═════════════════════════════════════════════════════════════ docgen ═══════
def test_the_document_covers_every_product_and_relation(document, products) -> None:
    for product in products:
        assert f"`{product.id}`" in document
        for dataset in build_contract(product).datasets:
            assert dataset.name in document
            for column in dataset.columns:
                assert f"`{column.name}`" in document


def test_the_document_states_the_freshness_guarantee(document) -> None:
    """R7: a contract document without its latency floor is a bare promise."""
    assert "Freshness guarantees are never optimistic" in document
    assert document.count("**Freshness guarantee**") >= 4


def test_the_document_states_the_breaking_change_policy(document) -> None:
    assert "## Breaking-change policy" in document
    assert "recorded human approval" in document


def test_the_document_marks_sensitive_columns(document) -> None:
    assert "**sensitive**" in document


def test_the_document_carries_migration_notes(document) -> None:
    assert "**Migration note, 2.0.0:**" in document


def test_the_document_says_it_is_generated(document) -> None:
    assert "do not edit by hand" in document
    assert "make contracts" in document


def test_write_contracts_round_trips(tmp_path) -> None:
    path = write_contracts(tmp_path / "DATA_CONTRACTS.md")
    assert path.read_text(encoding="utf-8") == render_contracts()


def test_the_checked_in_document_is_current() -> None:
    """A generated document that has drifted from its source is worse than none."""
    from snowobs_dataproducts import DATAPRODUCTS_ROOT

    path = DATAPRODUCTS_ROOT.parents[1] / "docs" / "DATA_CONTRACTS.md"
    assert path.read_text(encoding="utf-8") == render_contracts(), (
        "docs/DATA_CONTRACTS.md is stale; regenerate it with `make contracts`"
    )


# ══════════════════════════════════════════════════════ type inference ══════
def test_a_date_projection_types_as_a_date() -> None:
    model = default_model()
    entity = model.entity("fact_cost_daily")
    assert time_bucket_type(entity) is ColumnType.DATE
    assert dimension_type(entity, "service_type", model) is ColumnType.STRING


def test_a_timestamp_projection_types_as_a_timestamp() -> None:
    model = default_model()
    assert time_bucket_type(model.entity("fact_query_execution")) is ColumnType.TIMESTAMP_LTZ


def test_a_snapshot_entity_has_no_time_bucket() -> None:
    model = default_model()
    with pytest.raises(ProductResolutionError, match="no time column"):
        time_bucket_type(model.entity("dim_user"))


def test_an_unknown_dimension_is_refused() -> None:
    model = default_model()
    with pytest.raises(ProductResolutionError, match="not available"):
        dimension_type(model.entity("fact_cost_daily"), "no_such_dimension", model)


def test_additivity_follows_the_outer_aggregate() -> None:
    """Only SUM and plain COUNT survive a second aggregation (R12)."""
    model = default_model()
    assert is_additive(model.metric("cost.by_team_credits"))
    assert is_additive(model.metric("sec.new_grants"))
    assert not is_additive(model.metric("wh.utilisation_pct"))
    assert not is_additive(model.metric("sec.distinct_client_ips"))
    assert not is_additive(model.metric("pipe.task_duration_p95"))


# ══════════════════════════════════════════════════════════ resolution ══════
def test_metric_columns_are_derived_from_metric_ids() -> None:
    assert metric_column("cost.by_team_credits") == "COST_BY_TEAM_CREDITS"


def test_a_dimension_that_repeats_the_time_column_is_dropped(products) -> None:
    """Publishing USAGE_DAY beside TIME_BUCKET would double the apparent grain."""
    spec = next(
        s
        for s in resolve_datasets(products.get("finops_chargeback"))
        if s.entity_id == "fact_cost_daily"
    )
    assert "usage_day" not in spec.dimensions
    assert spec.bucketed


def test_view_and_model_names_are_derived_from_the_entity(products) -> None:
    spec = next(
        s
        for s in resolve_datasets(products.get("pipeline_health"))
        if s.entity_id == "fact_task_run"
    )
    assert spec.view_name == "V_PIPELINE_HEALTH_TASK_RUN"
    assert spec.dbt_model == "pipeline_health_task_run"


def test_unknown_metrics_are_refused_with_product_context(products) -> None:
    broken = products.get("pipeline_health").model_copy(update={"metrics": ["pipe.not_a_metric"]})
    with pytest.raises(ProductResolutionError, match="unknown metric"):
        resolve_datasets(broken)


def test_the_compiler_produces_both_dialects(products) -> None:
    spec = resolve_datasets(products.get("pipeline_health"))[0]
    for dialect in Dialect:
        compiled = compile_dataset(spec, dialect)
        assert compiled.dialect is dialect
        assert compiled.sql


def test_the_row_cap_is_stripped_for_a_published_relation(products) -> None:
    spec = resolve_datasets(products.get("pipeline_health"))[0]
    compiled = compile_dataset(spec)
    assert "LIMIT" in compiled.sql.upper()
    assert "LIMIT" not in unbounded_sql(compiled).upper()


# ══════════════════════════════════════════════════════════ the model ═══════
def test_versions_compare_and_classify_bumps() -> None:
    from snowobs_dataproducts.model import Bump

    assert Version.parse("1.2.3") < Version.parse("1.10.0")
    assert Version.parse("2.0.0").bump_from(Version.parse("1.9.9")) is Bump.MAJOR
    assert Version.parse("1.1.0").bump_from(Version.parse("1.0.9")) is Bump.MINOR
    assert Version.parse("1.0.1").bump_from(Version.parse("1.0.0")) is Bump.PATCH
    # A version that moved backwards has not bumped at all.
    assert Version.parse("1.0.0").bump_from(Version.parse("2.0.0")) is Bump.NONE
    with pytest.raises(ValueError, match="semantic version"):
        Version.parse("1.2")


def test_target_lag_renders_in_the_coarsest_whole_unit() -> None:
    assert RefreshCadence(target_lag_minutes=1440, cron="0 5 * * *").target_lag_clause == "1 day"
    assert RefreshCadence(target_lag_minutes=120, cron="0 * * * *").target_lag_clause == "2 hours"
    assert RefreshCadence(target_lag_minutes=45, cron="0 * * * *").target_lag_clause == "45 minutes"


def test_a_malformed_cron_is_refused() -> None:
    with pytest.raises(ValueError, match="five fields"):
        RefreshCadence(target_lag_minutes=60, cron="0 *")
