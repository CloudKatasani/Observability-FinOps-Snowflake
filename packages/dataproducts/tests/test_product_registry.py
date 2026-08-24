"""The shipped data product registry, validated against the semantic layer.

The load-time cross-checks are the point of this module: a product that names a
metric the platform cannot compute, promises a freshness no source can deliver,
or mixes grains in one relation must fail to load, not fail in production.
"""

from __future__ import annotations

import pytest
import yaml

from snowobs_common.errors import ConfigurationError
from snowobs_dataproducts import PRODUCTS_DIR
from snowobs_dataproducts.contracts import build_contract
from snowobs_dataproducts.model import DataProduct, Lifecycle
from snowobs_dataproducts.registry import load_products
from snowobs_dataproducts.resolve import resolve_datasets
from snowobs_semantics.model import default_model
from snowobs_semantics.registry import default_registry

MINIMUM_PRODUCTS = 4


@pytest.fixture(scope="module")
def products():
    return load_products()


def _raw(product_id: str) -> dict:
    return yaml.safe_load((PRODUCTS_DIR / f"{product_id}.yaml").read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════ the shipped registry ═════
def test_the_registry_ships_the_seed_products(products) -> None:
    assert len(products) >= MINIMUM_PRODUCTS
    assert {
        "finops_chargeback",
        "warehouse_efficiency",
        "pipeline_health",
        "access_governance",
    } <= set(products.ids())


def test_every_referenced_metric_exists_in_the_semantic_registry(products) -> None:
    """The load-bearing check: a product may only expose governed metrics.

    A product referencing a metric id that is not in the semantic registry would
    contract for a column the compiler cannot produce, which is the whole class
    of failure the derived contract exists to make impossible (R1).
    """
    known = set(default_model().metrics)
    for product in products:
        missing = [metric_id for metric_id in product.metrics if metric_id not in known]
        assert not missing, f"{product.id} references unknown metrics: {missing}"


def test_every_product_resolves_to_at_least_one_dataset(products) -> None:
    for product in products:
        assert resolve_datasets(product, default_model())


def test_every_published_dimension_is_declared_by_some_metric(products) -> None:
    model = default_model()
    for product in products:
        declared = {d for metric_id in product.metrics for d in model.metric(metric_id).dimensions}
        assert set(product.dimensions) <= declared, product.id


def test_no_product_promises_a_freshness_its_sources_cannot_deliver(products) -> None:
    """R7 at the registry level, before anything reaches a preflight check."""
    model, registry = default_model(), default_registry()
    for product in products:
        floor = max(
            max(
                (
                    registry.get(s).documented_latency_minutes
                    for s in model.metric(m).requires_sources
                ),
                default=0,
            )
            for m in product.metrics
        )
        assert product.sla.freshness_target_minutes >= floor, product.id


def test_every_dataset_holds_one_time_grain(products) -> None:
    model = default_model()
    for product in products:
        for spec in resolve_datasets(product, model):
            grains = {model.metric(m).grain for m in spec.metric_ids}
            assert len(grains) == 1, f"{spec.view_name} mixes grains {grains}"


def test_sensitive_columns_are_never_searchable(products) -> None:
    for product in products:
        contract = build_contract(product)
        for dataset in contract.datasets:
            for column in dataset.columns:
                assert not (column.sensitive and column.searchable), f"{dataset.name}.{column.name}"


def test_declared_search_column_is_a_searchable_contract_column(products) -> None:
    for product in products:
        if product.search is None:
            continue
        contract = build_contract(product)
        matches = [
            column
            for dataset in contract.datasets
            for column in dataset.columns
            if column.name == product.search.column
        ]
        assert matches, f"{product.id}: search column not in the contract"
        assert all(column.searchable for column in matches)


def test_filenames_match_product_ids(products) -> None:
    for product in products:
        assert (PRODUCTS_DIR / f"{product.id}.yaml").is_file()


# ══════════════════════════════════════════════════════ refusal paths ════════
def test_unknown_metric_is_refused_at_load(tmp_path) -> None:
    raw = _raw("pipeline_health")
    raw["metrics"] = [*raw["metrics"], "pipe.does_not_exist"]
    (tmp_path / "pipeline_health.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="do not exist"):
        load_products(tmp_path)


def test_unreachable_dimension_is_refused_at_load(tmp_path) -> None:
    raw = _raw("pipeline_health")
    raw["dimensions"] = [*raw["dimensions"], "warehouse_size"]
    (tmp_path / "pipeline_health.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="can resolve"):
        load_products(tmp_path)


def test_optimistic_freshness_target_is_refused_at_load(tmp_path) -> None:
    raw = _raw("pipeline_health")
    raw["sla"]["freshness_target_minutes"] = 5
    (tmp_path / "pipeline_health.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="R7"):
        load_products(tmp_path)


def test_mixed_grain_dataset_is_refused_at_load(tmp_path) -> None:
    """A month-grain metric beside a day-grain one silently regrains the finer."""
    raw = _raw("finops_chargeback")
    raw["metrics"] = [*raw["metrics"], "chargeback.budget_variance_credits"]
    (tmp_path / "finops_chargeback.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="mixes time grains"):
        load_products(tmp_path)


def test_source_without_a_queryable_relation_is_refused_at_load(tmp_path) -> None:
    """``SHOW WAREHOUSES`` is not a relation a published view can select from."""
    raw = _raw("warehouse_efficiency")
    raw["metrics"] = [*raw["metrics"], "wh.autosuspend_seconds"]
    raw["dimensions"] = [*raw["dimensions"], "configured_size"]
    (tmp_path / "warehouse_efficiency.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="not a queryable"):
        load_products(tmp_path)


def test_filename_must_match_product_id(tmp_path) -> None:
    raw = _raw("pipeline_health")
    (tmp_path / "something_else.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="filename must match"):
        load_products(tmp_path)


def test_empty_directory_is_refused(tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="No data products"):
        load_products(tmp_path)


def test_sensitive_search_attribute_is_refused_at_load(tmp_path) -> None:
    raw = _raw("access_governance")
    raw["search"]["attributes"] = ["CLIENT_IP"]
    (tmp_path / "access_governance.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="sensitive"):
        load_products(tmp_path)


# ══════════════════════════════════════════════════════ the record itself ════
def test_breaking_release_requires_a_migration_note() -> None:
    with pytest.raises(ValueError, match="migration_note"):
        DataProduct.model_validate(
            {
                **_raw("pipeline_health"),
                "change_log": [
                    {
                        "version": "1.0.0",
                        "released_on": "2026-08-24",
                        "summary": "Removed a column.",
                        "breaking": True,
                    }
                ],
            }
        )


def test_change_log_must_end_at_the_declared_version() -> None:
    raw = _raw("finops_chargeback")
    raw["version"] = "1.2.0"
    with pytest.raises(ValueError, match="change_log ends at"):
        DataProduct.model_validate(raw)


def test_availability_rejects_a_float() -> None:
    """§27.7: no float reaches a figure that sits beside currency in a contract."""
    raw = _raw("pipeline_health")
    raw["sla"]["availability_pct"] = 99.5
    with pytest.raises(ValueError, match="never a float"):
        DataProduct.model_validate(raw)


def test_status_defaults_are_real_lifecycle_states(products) -> None:
    for product in products:
        assert product.status in set(Lifecycle)
