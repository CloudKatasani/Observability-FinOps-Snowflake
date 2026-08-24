"""Dataset and coverage orchestration.

Services hold orchestration only — no SQL strings (§5). The catalog and the
coverage builder own the data access; this module owns the per-tenant storage
root and the lifetime of the DuckDB connection.
"""

from __future__ import annotations

from pathlib import Path

from snowobs_common.config import Settings
from snowobs_ingest.catalog import DuckDBCatalog
from snowobs_ingest.coverage import CoverageMatrix, build_coverage_matrix
from snowobs_ingest.export_script_gen import ExtractKit, generate_extract_kit
from snowobs_semantics.model import default_model as semantic_model
from snowobs_semantics.registry import default_registry


def storage_root(settings: Settings) -> Path:
    """Local landing root for OFFLINE mode.

    The object-storage adapter (Phase 8) resolves this to S3/MinIO; locally it
    is a directory. Either way, callers never build paths themselves.
    """
    return Path(settings.storage.bucket if settings.storage.provider == "local" else ".data")


class DatasetService:
    """Read-side access to what has been landed for a tenant."""

    def __init__(self, settings: Settings, tenant: str = "default") -> None:
        self.settings = settings
        self.tenant = tenant
        self.root = storage_root(settings)

    def coverage(
        self,
        metric_requirements: dict[str, list[str]] | None = None,
        optional_requirements: dict[str, list[str]] | None = None,
    ) -> CoverageMatrix:
        """Per-source and per-KPI availability (R3).

        The KPI half defaults to the semantic layer's own declared requirements
        rather than to nothing: the point of the coverage page is to say which
        of the ~90 KPIs a caller can trust today, and a matrix with an empty
        ``metrics`` list answers the source question while silently dropping
        the one users actually came for.
        """
        model = semantic_model()
        mode = "live" if self.settings.mode == "live" else "offline"
        with DuckDBCatalog(self.root, tenant=self.tenant) as catalog:
            catalog.register_all()
            return build_coverage_matrix(
                catalog,
                metric_requirements=metric_requirements or model.requirements(),
                mode=mode,
                optional_requirements=optional_requirements or model.optional_requirements(),
            )

    def extract_kit(self, *, days: int = 120, file_format: str = "PARQUET") -> ExtractKit:
        return generate_extract_kit(
            default_registry(),
            days=days,
            file_format=file_format,
            warehouse=self.settings.snowflake.warehouse,
        )
