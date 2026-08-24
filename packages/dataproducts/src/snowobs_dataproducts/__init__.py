"""Data product management: registry, contracts, artifacts, publication (§13)."""

from pathlib import Path

#: Repository-relative root of the declarative product YAML (…/packages/dataproducts).
DATAPRODUCTS_ROOT = Path(__file__).resolve().parents[2]
#: One YAML per data product — the registry (§13.1).
PRODUCTS_DIR = DATAPRODUCTS_ROOT / "products"
#: Published contract snapshots, ``contracts/<product id>/<version>.yaml`` (§13.2).
CONTRACTS_DIR = DATAPRODUCTS_ROOT / "contracts"
