"""The data product registry (BUILD_PROMPT §13.1).

One YAML per product under ``packages/dataproducts/products/``, loaded and
cross-validated against the semantic layer exactly as the semantic registry
loads sources: a product that names a metric the platform cannot compute, a
dimension no entity can resolve, or a freshness target no source can meet does
not load at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

from snowobs_common.errors import ConfigurationError
from snowobs_dataproducts import PRODUCTS_DIR
from snowobs_dataproducts.contracts import build_contract, freshness_floor
from snowobs_dataproducts.model import DataProduct, SearchSpec
from snowobs_dataproducts.resolve import resolve_datasets
from snowobs_semantics.model import SemanticModel, default_model
from snowobs_semantics.registry import SourceRegistry, default_registry


class ProductRegistry(BaseModel):
    """The loaded, cross-validated set of data products."""

    products: dict[str, DataProduct]

    # BaseModel.__iter__ yields (name, value) pairs; iterating a registry should
    # yield the products themselves, which is what every caller wants.
    def __iter__(self) -> Iterator[DataProduct]:  # type: ignore[override]
        return iter(self.products.values())

    def __len__(self) -> int:
        return len(self.products)

    def get(self, product_id: str) -> DataProduct:
        try:
            return self.products[product_id]
        except KeyError:
            raise ConfigurationError(f"Unknown data product: {product_id}") from None

    def ids(self) -> list[str]:
        return sorted(self.products)

    def for_domain(self, domain: str) -> list[DataProduct]:
        return sorted((p for p in self.products.values() if p.domain == domain), key=lambda p: p.id)

    def referenced_metrics(self) -> set[str]:
        return {metric_id for product in self.products.values() for metric_id in product.metrics}


def load_products(
    directory: Path | None = None,
    model: SemanticModel | None = None,
    registry: SourceRegistry | None = None,
) -> ProductRegistry:
    """Load every product YAML and validate it against the semantic layer."""
    directory = directory or PRODUCTS_DIR
    if not directory.is_dir():
        raise ConfigurationError(f"Product directory not found: {directory}")

    products: dict[str, DataProduct] = {}
    for path in sorted(directory.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            product = DataProduct.model_validate(raw)
        except Exception as exc:
            raise ConfigurationError(f"Invalid data product {path.name}: {exc}") from exc
        if product.id in products:
            raise ConfigurationError(f"Duplicate data product id {product.id} in {path.name}")
        if path.stem != product.id:
            raise ConfigurationError(f"{path.name}: filename must match product id '{product.id}'")
        products[product.id] = product

    if not products:
        raise ConfigurationError(f"No data products found in {directory}")

    product_registry = ProductRegistry(products=products)
    _validate(product_registry, model or default_model(), registry or default_registry())
    return product_registry


def _validate(products: ProductRegistry, model: SemanticModel, sources: SourceRegistry) -> None:
    """Cross-checks that catch what a single YAML file cannot see."""
    for product in products:
        unknown = [metric_id for metric_id in product.metrics if metric_id not in model.metrics]
        if unknown:
            raise ConfigurationError(
                f"Data product {product.id} references metrics that do not exist "
                f"in the semantic registry: {unknown}"
            )

        specs = resolve_datasets(product, model)
        entities = {spec.entity_id for spec in specs}

        unreachable = [
            dimension
            for dimension in product.dimensions
            if not any(dimension in spec.dimensions for spec in specs)
        ]
        if unreachable:
            raise ConfigurationError(
                f"Data product {product.id} declares dimensions no entity in its boundary "
                f"can resolve (or that duplicate a time column): {unreachable}"
            )

        undeclared = [
            dimension
            for dimension in product.dimensions
            if not any(dimension in model.metric(m).dimensions for m in product.metrics)
        ]
        if undeclared:
            raise ConfigurationError(
                f"Data product {product.id} publishes dimensions no metric in its boundary "
                f"declares as a supported slice: {undeclared}"
            )

        # Two metrics declared at different grains cannot share a relation: the
        # compiler takes the coarsest, so the finer metric is silently regrained
        # and every figure read from it means something other than its
        # definition says.
        for spec in specs:
            grains = {model.metric(metric_id).grain.value for metric_id in spec.metric_ids}
            if len(grains) > 1:
                raise ConfigurationError(
                    f"Data product {product.id} dataset {spec.view_name} mixes time grains "
                    f"{sorted(grains)}; split the metrics into separate products or align "
                    f"their declared grain"
                )

        for expectation in product.row_expectations:
            if expectation.entity not in entities:
                raise ConfigurationError(
                    f"Data product {product.id} declares a row expectation for entity "
                    f"'{expectation.entity}', which is not in its boundary"
                )

        for relationship in product.relationships:
            missing = [
                e for e in (relationship.from_entity, relationship.to_entity) if e not in entities
            ]
            if missing:
                raise ConfigurationError(
                    f"Data product {product.id} relationship '{relationship.name}' references "
                    f"entities outside its boundary: {missing}"
                )

        # A dataset has to be a relation the emitted SQL can name. A source that
        # is only reachable through SHOW output has no relation to select from,
        # so a product built on it could not be published as a view.
        for spec in specs:
            for source_id in model.entity(spec.entity_id).sources:
                snowflake_object = sources.get(source_id).snowflake_object
                if len(snowflake_object.split(".")) != 3:
                    raise ConfigurationError(
                        f"Data product {product.id} dataset {spec.view_name} reads source "
                        f"'{source_id}' ({snowflake_object}), which is not a queryable "
                        f"relation and cannot be published"
                    )

        floor = freshness_floor(list(product.metrics), model, sources)
        if product.sla.freshness_target_minutes < floor:
            raise ConfigurationError(
                f"Data product {product.id} targets {product.sla.freshness_target_minutes} "
                f"minutes of freshness, but its sources document {floor} minutes (R7)"
            )

        if product.search is not None:
            _validate_search(product, product.search, model, sources)


def _validate_search(
    product: DataProduct, spec: SearchSpec, model: SemanticModel, sources: SourceRegistry
) -> None:
    contract = build_contract(product, model, sources)
    names = {column.name for dataset in contract.datasets for column in dataset.columns}
    searchable = {
        column.name
        for dataset in contract.datasets
        for column in dataset.columns
        if column.searchable
    }
    if spec.column not in names:
        raise ConfigurationError(
            f"Data product {product.id} indexes '{spec.column}' for search, but no contract "
            f"column has that name"
        )
    if spec.column not in searchable:
        raise ConfigurationError(
            f"Data product {product.id} indexes '{spec.column}' for search, but that column "
            f"is not free text or is marked sensitive (R2, §12.5)"
        )
    unknown = [name for name in spec.attributes if name not in names]
    if unknown:
        raise ConfigurationError(
            f"Data product {product.id} search attributes are not contract columns: {unknown}"
        )
    sensitive = [name for name in spec.attributes if product.is_sensitive(name)]
    if sensitive:
        raise ConfigurationError(
            f"Data product {product.id} exposes sensitive columns as search attributes: {sensitive}"
        )


@lru_cache(maxsize=1)
def default_registry_products() -> ProductRegistry:
    return load_products()
