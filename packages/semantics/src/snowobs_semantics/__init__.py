"""Declarative semantic layer: sources, entities, metrics, allocation (R1)."""

from pathlib import Path

#: Repository-relative root of the declarative YAML (…/packages/semantics).
SEMANTICS_ROOT = Path(__file__).resolve().parents[2]
SOURCES_DIR = SEMANTICS_ROOT / "sources"
ENTITIES_DIR = SEMANTICS_ROOT / "entities"
METRICS_DIR = SEMANTICS_ROOT / "metrics"
ALLOCATION_DIR = SEMANTICS_ROOT / "allocation"
