"""Data contracts and their change classification (BUILD_PROMPT §13.2, §13.3).

A contract is the machine-readable promise a product makes: the columns, their
types and nullability, the grain, how fresh the data is guaranteed to be, how
many rows to expect, how long it is retained, and what happens when any of that
has to change.

Two properties matter more than the rest:

* **The freshness guarantee is never optimistic.** It is the *maximum* documented
  latency across every source the product's metrics read (R7). A product cannot
  promise five minutes over an eight-hour view.
* **A breaking change cannot ship as a patch.** :func:`diff` classifies every
  change and refuses a version bump too small for what changed (§13.3).

The contract is *derived* from the product declaration plus the semantic layer,
which is what makes :meth:`DataContract.validate_against` meaningful: a stored
contract that no longer matches what the semantic layer would produce is drift,
and drift is reported, not silently absorbed.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from snowobs_common.errors import AppError, ConfigurationError
from snowobs_dataproducts import CONTRACTS_DIR
from snowobs_dataproducts.model import Bump, Classification, DataProduct, Version
from snowobs_dataproducts.resolve import (
    ColumnType,
    DatasetSpec,
    dimension_type,
    metric_column,
    metric_type,
    resolve_datasets,
    time_bucket_type,
)
from snowobs_semantics.compiler import TIME_COLUMN_ALIAS
from snowobs_semantics.model import SemanticModel, default_model
from snowobs_semantics.registry import SourceRegistry, default_registry

#: The policy every contract publishes, so consumers know the rules up front.
BREAKING_CHANGE_POLICY = (
    "Removing a column, changing its type, relaxing its nullability, rebinding it "
    "to a different governed metric, changing the grain, loosening the freshness "
    "guarantee, or shortening retention is a BREAKING change: it requires a major "
    "version, a migration note, and the full deprecation notice period. Everything "
    "else is additive and ships as a minor or patch version."
)


class ContractError(AppError):
    status_code = 400
    title = "Data contract error"
    problem_type = "https://snowobs.dev/problems/data-contract"


class BreakingChangeError(ContractError):
    """A version bump too small for the changes it carries (§13.3)."""

    status_code = 409
    title = "Breaking change without a major version"
    problem_type = "https://snowobs.dev/problems/breaking-change"

    def __init__(self, detail: str, contract_diff: ContractDiff) -> None:
        super().__init__(detail)
        self.contract_diff = contract_diff


class ChangeKind(StrEnum):
    """Every kind of change a contract revision can carry."""

    DATASET_ADDED = "dataset_added"
    DATASET_REMOVED = "dataset_removed"
    GRAIN_CHANGED = "grain_changed"
    COLUMN_ADDED = "column_added"
    COLUMN_REMOVED = "column_removed"
    COLUMN_RETYPED = "column_retyped"
    COLUMN_NULLABILITY_RELAXED = "column_nullability_relaxed"
    COLUMN_NULLABILITY_TIGHTENED = "column_nullability_tightened"
    COLUMN_REBOUND = "column_rebound"
    COLUMN_DOCUMENTED = "column_documented"
    FRESHNESS_RELAXED = "freshness_relaxed"
    FRESHNESS_TIGHTENED = "freshness_tightened"
    RETENTION_REDUCED = "retention_reduced"
    RETENTION_EXTENDED = "retention_extended"
    AVAILABILITY_REDUCED = "availability_reduced"
    AVAILABILITY_RAISED = "availability_raised"
    ROW_EXPECTATION_CHANGED = "row_expectation_changed"
    CLASSIFICATION_RAISED = "classification_raised"
    CLASSIFICATION_LOWERED = "classification_lowered"

    @property
    def severity(self) -> Severity:
        return _SEVERITY[self]

    @property
    def required_bump(self) -> Bump:
        return _REQUIRED_BUMP[self]


class Severity(StrEnum):
    BREAKING = "breaking"
    NON_BREAKING = "non_breaking"


#: A change is breaking when existing consumer code can stop working because of
#: it. Widening a promise never is; narrowing one always is.
_SEVERITY: dict[ChangeKind, Severity] = {
    ChangeKind.DATASET_ADDED: Severity.NON_BREAKING,
    ChangeKind.DATASET_REMOVED: Severity.BREAKING,
    ChangeKind.GRAIN_CHANGED: Severity.BREAKING,
    ChangeKind.COLUMN_ADDED: Severity.NON_BREAKING,
    ChangeKind.COLUMN_REMOVED: Severity.BREAKING,
    ChangeKind.COLUMN_RETYPED: Severity.BREAKING,
    ChangeKind.COLUMN_NULLABILITY_RELAXED: Severity.BREAKING,
    ChangeKind.COLUMN_NULLABILITY_TIGHTENED: Severity.NON_BREAKING,
    ChangeKind.COLUMN_REBOUND: Severity.BREAKING,
    ChangeKind.COLUMN_DOCUMENTED: Severity.NON_BREAKING,
    ChangeKind.FRESHNESS_RELAXED: Severity.BREAKING,
    ChangeKind.FRESHNESS_TIGHTENED: Severity.NON_BREAKING,
    ChangeKind.RETENTION_REDUCED: Severity.BREAKING,
    ChangeKind.RETENTION_EXTENDED: Severity.NON_BREAKING,
    ChangeKind.AVAILABILITY_REDUCED: Severity.BREAKING,
    ChangeKind.AVAILABILITY_RAISED: Severity.NON_BREAKING,
    ChangeKind.ROW_EXPECTATION_CHANGED: Severity.NON_BREAKING,
    ChangeKind.CLASSIFICATION_RAISED: Severity.BREAKING,
    ChangeKind.CLASSIFICATION_LOWERED: Severity.NON_BREAKING,
}

#: Breaking changes need a major version. Additive changes need a minor. Purely
#: descriptive changes are a patch.
_REQUIRED_BUMP: dict[ChangeKind, Bump] = {
    kind: (
        Bump.MAJOR
        if severity is Severity.BREAKING
        else (Bump.PATCH if kind is ChangeKind.COLUMN_DOCUMENTED else Bump.MINOR)
    )
    for kind, severity in _SEVERITY.items()
}


class ContractColumn(BaseModel):
    """One contracted column (§13.2 schema)."""

    model_config = ConfigDict(frozen=True)

    name: str
    type: ColumnType
    nullable: bool
    description: str
    #: The governed metric this column carries. ``None`` for grain columns —
    #: which is what ties every published figure back to the semantic layer (R5).
    metric_id: str | None = None
    unit: str | None = None
    sensitive: bool = False
    #: False for a column that must never be indexed for free-text search (R2).
    searchable: bool = False


class ContractDataset(BaseModel):
    """One published relation and its promises."""

    model_config = ConfigDict(frozen=True)

    name: str
    entity: str
    description: str
    grain: list[str]
    #: The time bucket the relation is aggregated to, or ``None`` for a snapshot
    #: relation with no time column.
    time_grain: str | None
    columns: list[ContractColumn]
    #: Maximum age of the newest row, from the slowest source this relation reads.
    freshness_minutes: int
    expected_min_rows_per_day: int
    expected_max_rows_per_day: int | None
    #: Registered source ids behind the relation, for lineage (§13.1).
    sources: list[str]

    def column(self, name: str) -> ContractColumn | None:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def metric_ids(self) -> list[str]:
        return [c.metric_id for c in self.columns if c.metric_id is not None]

    @property
    def text_columns(self) -> list[ContractColumn]:
        return [c for c in self.columns if c.type.is_text]


class DataContract(BaseModel):
    """The full contract for one version of one product."""

    model_config = ConfigDict(frozen=True)

    product_id: str
    product_name: str
    version: Version
    owner: str
    domain: str
    classification: Classification
    #: The slowest dataset's freshness — what the product as a whole guarantees.
    freshness_guarantee_minutes: int
    availability_pct: Decimal
    retention_days: int
    support_channel: str
    deprecation_notice_days: int
    breaking_change_policy: str = BREAKING_CHANGE_POLICY
    datasets: list[ContractDataset]

    def dataset(self, name: str) -> ContractDataset:
        for dataset in self.datasets:
            if dataset.name == name:
                return dataset
        raise ContractError(f"{self.product_id}: no dataset named {name}")

    @property
    def dataset_names(self) -> list[str]:
        return [d.name for d in self.datasets]

    @property
    def metric_ids(self) -> list[str]:
        return sorted({m for d in self.datasets for m in d.metric_ids})

    @property
    def sources(self) -> list[str]:
        return sorted({s for d in self.datasets for s in d.sources})

    @property
    def column_count(self) -> int:
        return sum(len(d.columns) for d in self.datasets)

    # ------------------------------------------------------------ persistence
    def to_yaml(self) -> str:
        return yaml.safe_dump(
            self.model_dump(mode="json"),
            sort_keys=False,
            default_flow_style=False,
            width=100,
            allow_unicode=True,
        )

    @classmethod
    def from_yaml(cls, text: str) -> DataContract:
        raw = yaml.safe_load(text)
        if not isinstance(raw, dict):
            raise ContractError("contract YAML must be a mapping")
        return cls.model_validate(raw)

    # ------------------------------------------------------------- validation
    def validate_against(
        self,
        model: SemanticModel | None = None,
        registry: SourceRegistry | None = None,
    ) -> ContractValidation:
        """Check the contract still describes what the semantic layer produces.

        This is the continuous drift check §13.2 asks for: a metric that was
        retyped, retired, moved to another entity, or whose sources got slower
        breaks the promise this contract makes, and the owner has to hear about
        it before a consumer does.
        """
        resolved = model or default_model()
        sources = registry or default_registry()
        findings: list[ContractFinding] = []

        for dataset in self.datasets:
            path = f"{self.product_id}.{dataset.name}"
            if dataset.entity not in resolved.entities:
                findings.append(
                    ContractFinding(
                        path=path,
                        severity=Severity.BREAKING,
                        detail=f"entity '{dataset.entity}' no longer exists in the semantic layer",
                    )
                )
                continue
            entity = resolved.entity(dataset.entity)
            findings.extend(self._validate_columns(dataset, entity.id, resolved, path))
            findings.extend(self._validate_freshness(dataset, resolved, sources, path))
            for source_id in dataset.sources:
                if source_id not in sources.sources:
                    findings.append(
                        ContractFinding(
                            path=path,
                            severity=Severity.BREAKING,
                            detail=f"source '{source_id}' is no longer registered",
                        )
                    )

        promised = max((d.freshness_minutes for d in self.datasets), default=0)
        if self.freshness_guarantee_minutes < promised:
            findings.append(
                ContractFinding(
                    path=self.product_id,
                    severity=Severity.BREAKING,
                    detail=(
                        f"product guarantees {self.freshness_guarantee_minutes} minutes but its "
                        f"slowest dataset is {promised} minutes behind"
                    ),
                )
            )
        return ContractValidation(product_id=self.product_id, findings=findings)

    def _validate_columns(
        self, dataset: ContractDataset, entity_id: str, model: SemanticModel, path: str
    ) -> list[ContractFinding]:
        findings: list[ContractFinding] = []
        for column in dataset.columns:
            if column.metric_id is None:
                continue
            if column.metric_id not in model.metrics:
                findings.append(
                    ContractFinding(
                        path=f"{path}.{column.name}",
                        severity=Severity.BREAKING,
                        detail=f"metric '{column.metric_id}' no longer exists",
                    )
                )
                continue
            metric = model.metric(column.metric_id)
            if metric.entity != entity_id:
                findings.append(
                    ContractFinding(
                        path=f"{path}.{column.name}",
                        severity=Severity.BREAKING,
                        detail=(
                            f"metric '{metric.id}' moved from entity '{entity_id}' to "
                            f"'{metric.entity}'"
                        ),
                    )
                )
            expected = metric_type(metric)
            if expected is not column.type:
                findings.append(
                    ContractFinding(
                        path=f"{path}.{column.name}",
                        severity=Severity.BREAKING,
                        detail=(
                            f"metric '{metric.id}' now produces {expected.value}, "
                            f"contract promises {column.type.value}"
                        ),
                    )
                )
        return findings

    def _validate_freshness(
        self,
        dataset: ContractDataset,
        model: SemanticModel,
        registry: SourceRegistry,
        path: str,
    ) -> list[ContractFinding]:
        metric_ids = [m for m in dataset.metric_ids if m in model.metrics]
        if not metric_ids:
            return []
        floor = freshness_floor(metric_ids, model, registry)
        if floor > dataset.freshness_minutes:
            return [
                ContractFinding(
                    path=path,
                    severity=Severity.BREAKING,
                    detail=(
                        f"sources now document {floor} minutes of latency; the contract "
                        f"promises {dataset.freshness_minutes}"
                    ),
                )
            ]
        return []


class ContractFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    severity: Severity
    detail: str


class ContractValidation(BaseModel):
    model_config = ConfigDict(frozen=True)

    product_id: str
    findings: list[ContractFinding] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def summary(self) -> str:
        if self.ok:
            return f"{self.product_id}: contract matches the semantic layer"
        return f"{self.product_id}: {len(self.findings)} drift finding(s)"


class ContractChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: ChangeKind
    path: str
    detail: str
    before: str | None = None
    after: str | None = None

    @property
    def severity(self) -> Severity:
        return self.kind.severity

    @property
    def is_breaking(self) -> bool:
        return self.severity is Severity.BREAKING


class ContractDiff(BaseModel):
    """Two contract versions compared, with every change classified."""

    model_config = ConfigDict(frozen=True)

    product_id: str
    baseline_version: Version
    target_version: Version
    changes: list[ContractChange] = Field(default_factory=list)

    @property
    def breaking(self) -> list[ContractChange]:
        return [c for c in self.changes if c.is_breaking]

    @property
    def required_bump(self) -> Bump:
        if not self.changes:
            return Bump.NONE
        return max((c.kind.required_bump for c in self.changes), key=lambda b: b.rank)

    @property
    def declared_bump(self) -> Bump:
        return self.target_version.bump_from(self.baseline_version)

    @property
    def is_version_sufficient(self) -> bool:
        return self.declared_bump.covers(self.required_bump)

    def release_notes(self) -> str:
        """Draft release notes from the diff (§13.3 — the Curator's starting point)."""
        lines = [
            f"# {self.product_id} {self.target_version}",
            "",
            f"Previous version: {self.baseline_version} · "
            f"change class: **{self.required_bump.value}**",
            "",
        ]
        if not self.changes:
            lines.append("No contract changes.")
            return "\n".join(lines) + "\n"
        breaking = self.breaking
        if breaking:
            lines.append("## Breaking changes")
            lines.append("")
            lines.extend(f"- `{c.path}` — {c.detail}" for c in breaking)
            lines.append("")
        additive = [c for c in self.changes if not c.is_breaking]
        if additive:
            lines.append("## Other changes")
            lines.append("")
            lines.extend(f"- `{c.path}` — {c.detail}" for c in additive)
            lines.append("")
        return "\n".join(lines)


def diff(old: DataContract, new: DataContract) -> ContractDiff:
    """Compare two contract versions and enforce the versioning rule (§13.3).

    Raises :class:`BreakingChangeError` when the declared version bump is too
    small for what changed — in particular, when a breaking change would ship
    under a patch or minor version. The exception carries the computed
    :class:`ContractDiff` so callers can show the reviewer exactly what forced
    the major version.
    """
    if old.product_id != new.product_id:
        raise ContractError(
            f"cannot diff contracts for different products: {old.product_id} vs {new.product_id}"
        )
    computed = ContractDiff(
        product_id=new.product_id,
        baseline_version=old.version,
        target_version=new.version,
        changes=_changes(old, new),
    )
    if not computed.is_version_sufficient:
        raise BreakingChangeError(
            f"{new.product_id} {old.version} → {new.version} is a "
            f"{computed.declared_bump.value} bump but the changes require a "
            f"{computed.required_bump.value} bump "
            f"({len(computed.breaking)} breaking change(s))",
            computed,
        )
    return computed


def _changes(old: DataContract, new: DataContract) -> list[ContractChange]:
    changes: list[ContractChange] = []
    changes.extend(_product_level_changes(old, new))

    old_datasets = {d.name: d for d in old.datasets}
    new_datasets = {d.name: d for d in new.datasets}
    for name in sorted(set(old_datasets) - set(new_datasets)):
        changes.append(
            ContractChange(
                kind=ChangeKind.DATASET_REMOVED,
                path=name,
                detail="relation withdrawn from the product",
                before=name,
            )
        )
    for name in sorted(set(new_datasets) - set(old_datasets)):
        changes.append(
            ContractChange(
                kind=ChangeKind.DATASET_ADDED,
                path=name,
                detail="new relation added to the product",
                after=name,
            )
        )
    for name in sorted(set(old_datasets) & set(new_datasets)):
        changes.extend(_dataset_changes(old_datasets[name], new_datasets[name]))
    return changes


def _product_level_changes(old: DataContract, new: DataContract) -> list[ContractChange]:
    changes: list[ContractChange] = []
    path = new.product_id
    if new.freshness_guarantee_minutes > old.freshness_guarantee_minutes:
        changes.append(
            ContractChange(
                kind=ChangeKind.FRESHNESS_RELAXED,
                path=path,
                detail="the product's freshness guarantee got weaker",
                before=f"{old.freshness_guarantee_minutes} min",
                after=f"{new.freshness_guarantee_minutes} min",
            )
        )
    elif new.freshness_guarantee_minutes < old.freshness_guarantee_minutes:
        changes.append(
            ContractChange(
                kind=ChangeKind.FRESHNESS_TIGHTENED,
                path=path,
                detail="the product's freshness guarantee got stronger",
                before=f"{old.freshness_guarantee_minutes} min",
                after=f"{new.freshness_guarantee_minutes} min",
            )
        )
    if new.retention_days < old.retention_days:
        changes.append(
            ContractChange(
                kind=ChangeKind.RETENTION_REDUCED,
                path=path,
                detail="history is retained for less time",
                before=f"{old.retention_days} d",
                after=f"{new.retention_days} d",
            )
        )
    elif new.retention_days > old.retention_days:
        changes.append(
            ContractChange(
                kind=ChangeKind.RETENTION_EXTENDED,
                path=path,
                detail="history is retained for longer",
                before=f"{old.retention_days} d",
                after=f"{new.retention_days} d",
            )
        )
    if new.availability_pct < old.availability_pct:
        changes.append(
            ContractChange(
                kind=ChangeKind.AVAILABILITY_REDUCED,
                path=path,
                detail="the availability promise got weaker",
                before=str(old.availability_pct),
                after=str(new.availability_pct),
            )
        )
    elif new.availability_pct > old.availability_pct:
        changes.append(
            ContractChange(
                kind=ChangeKind.AVAILABILITY_RAISED,
                path=path,
                detail="the availability promise got stronger",
                before=str(old.availability_pct),
                after=str(new.availability_pct),
            )
        )
    old_rank = list(Classification).index(old.classification)
    new_rank = list(Classification).index(new.classification)
    if new_rank > old_rank:
        changes.append(
            ContractChange(
                kind=ChangeKind.CLASSIFICATION_RAISED,
                path=path,
                detail="the product is now more restricted; existing grants may not carry over",
                before=old.classification.value,
                after=new.classification.value,
            )
        )
    elif new_rank < old_rank:
        changes.append(
            ContractChange(
                kind=ChangeKind.CLASSIFICATION_LOWERED,
                path=path,
                detail="the product is less restricted than it was",
                before=old.classification.value,
                after=new.classification.value,
            )
        )
    return changes


def _dataset_changes(old: ContractDataset, new: ContractDataset) -> list[ContractChange]:
    changes: list[ContractChange] = []
    if old.grain != new.grain:
        changes.append(
            ContractChange(
                kind=ChangeKind.GRAIN_CHANGED,
                path=new.name,
                detail="every stored aggregate over this relation changes meaning",
                before=", ".join(old.grain),
                after=", ".join(new.grain),
            )
        )
    if new.freshness_minutes > old.freshness_minutes:
        changes.append(
            ContractChange(
                kind=ChangeKind.FRESHNESS_RELAXED,
                path=new.name,
                detail="this relation is guaranteed less fresh than before",
                before=f"{old.freshness_minutes} min",
                after=f"{new.freshness_minutes} min",
            )
        )
    elif new.freshness_minutes < old.freshness_minutes:
        changes.append(
            ContractChange(
                kind=ChangeKind.FRESHNESS_TIGHTENED,
                path=new.name,
                detail="this relation is guaranteed fresher than before",
                before=f"{old.freshness_minutes} min",
                after=f"{new.freshness_minutes} min",
            )
        )
    if (old.expected_min_rows_per_day, old.expected_max_rows_per_day) != (
        new.expected_min_rows_per_day,
        new.expected_max_rows_per_day,
    ):
        changes.append(
            ContractChange(
                kind=ChangeKind.ROW_EXPECTATION_CHANGED,
                path=new.name,
                detail="the expected daily row count changed",
                before=f"{old.expected_min_rows_per_day}..{old.expected_max_rows_per_day}",
                after=f"{new.expected_min_rows_per_day}..{new.expected_max_rows_per_day}",
            )
        )

    old_columns = {c.name: c for c in old.columns}
    new_columns = {c.name: c for c in new.columns}
    for name in sorted(set(old_columns) - set(new_columns)):
        changes.append(
            ContractChange(
                kind=ChangeKind.COLUMN_REMOVED,
                path=f"{new.name}.{name}",
                detail="column withdrawn",
                before=old_columns[name].type.value,
            )
        )
    for name in sorted(set(new_columns) - set(old_columns)):
        changes.append(
            ContractChange(
                kind=ChangeKind.COLUMN_ADDED,
                path=f"{new.name}.{name}",
                detail="column added",
                after=new_columns[name].type.value,
            )
        )
    for name in sorted(set(old_columns) & set(new_columns)):
        changes.extend(_column_changes(new.name, old_columns[name], new_columns[name]))
    return changes


def _column_changes(dataset: str, old: ContractColumn, new: ContractColumn) -> list[ContractChange]:
    path = f"{dataset}.{new.name}"
    changes: list[ContractChange] = []
    if old.type is not new.type:
        changes.append(
            ContractChange(
                kind=ChangeKind.COLUMN_RETYPED,
                path=path,
                detail="consumer casts and downstream models break",
                before=old.type.value,
                after=new.type.value,
            )
        )
    if new.nullable and not old.nullable:
        changes.append(
            ContractChange(
                kind=ChangeKind.COLUMN_NULLABILITY_RELAXED,
                path=path,
                detail="a column promised non-null may now be null",
                before="NOT NULL",
                after="NULL",
            )
        )
    elif old.nullable and not new.nullable:
        changes.append(
            ContractChange(
                kind=ChangeKind.COLUMN_NULLABILITY_TIGHTENED,
                path=path,
                detail="a nullable column is now guaranteed non-null",
                before="NULL",
                after="NOT NULL",
            )
        )
    if old.metric_id != new.metric_id:
        changes.append(
            ContractChange(
                kind=ChangeKind.COLUMN_REBOUND,
                path=path,
                detail="the column now carries a different governed metric",
                before=old.metric_id or "(grain column)",
                after=new.metric_id or "(grain column)",
            )
        )
    elif old.description != new.description:
        changes.append(
            ContractChange(
                kind=ChangeKind.COLUMN_DOCUMENTED,
                path=path,
                detail="documentation updated",
                before=old.description,
                after=new.description,
            )
        )
    return changes


def freshness_floor(metric_ids: list[str], model: SemanticModel, registry: SourceRegistry) -> int:
    """The slowest documented latency behind a set of metrics (R7).

    Both the metric's declared floor and its sources' documented latencies are
    considered, and the maximum wins. Taking anything less would let a product
    advertise a freshness its slowest source cannot deliver.
    """
    floors = [0]
    for metric_id in metric_ids:
        metric = model.metric(metric_id)
        floors.append(metric.latency_floor_minutes)
        for source_id in metric.requires_sources:
            floors.append(registry.get(source_id).documented_latency_minutes)
    return max(floors)


def build_contract(
    product: DataProduct,
    model: SemanticModel | None = None,
    registry: SourceRegistry | None = None,
) -> DataContract:
    """Derive the contract for a product from the semantic layer.

    Derivation rather than declaration is deliberate: a hand-written contract
    can promise a column the metric layer does not produce, and nothing would
    catch it until a consumer's query failed.
    """
    resolved = model or default_model()
    sources = registry or default_registry()
    specs = resolve_datasets(product, resolved)
    if not specs:
        raise ContractError(f"{product.id}: product exposes no datasets")

    datasets = [_build_dataset(product, spec, resolved, sources) for spec in specs]
    return DataContract(
        product_id=product.id,
        product_name=product.name,
        version=product.version,
        owner=product.owner,
        domain=product.domain,
        classification=product.classification,
        freshness_guarantee_minutes=max(d.freshness_minutes for d in datasets),
        availability_pct=product.sla.availability_pct,
        retention_days=product.sla.retention_days,
        support_channel=product.sla.support_channel,
        deprecation_notice_days=product.sla.deprecation_notice_days,
        datasets=datasets,
    )


def _build_dataset(
    product: DataProduct,
    spec: DatasetSpec,
    model: SemanticModel,
    registry: SourceRegistry,
) -> ContractDataset:
    entity = model.entity(spec.entity_id)
    columns: list[ContractColumn] = []

    if spec.bucketed:
        columns.append(
            ContractColumn(
                name=TIME_COLUMN_ALIAS,
                type=time_bucket_type(entity),
                nullable=False,
                description=(
                    f"Time bucket over {entity.time_column}, at the coarsest grain the "
                    f"product's metrics declare."
                ),
                searchable=False,
            )
        )
    for dimension in spec.dimensions:
        reference = entity.dimension(dimension)
        column_type = dimension_type(entity, dimension, model)
        name = dimension.upper()
        columns.append(
            ContractColumn(
                name=name,
                type=column_type,
                # A dimension read from usage telemetry is null wherever the
                # source did not record it. Promising NOT NULL here is the
                # single most common way a contract becomes a lie.
                nullable=True,
                description=(reference.description if reference and reference.description else "")
                or f"{dimension.replace('_', ' ').capitalize()} dimension of {entity.name}.",
                sensitive=product.is_sensitive(name),
                searchable=column_type.is_text and not product.is_sensitive(name),
            )
        )
    for metric_id in spec.metric_ids:
        metric = model.metric(metric_id)
        columns.append(
            ContractColumn(
                name=metric_column(metric_id),
                type=metric_type(metric),
                # A metric aggregate is null when no row in the bucket
                # contributes, and a ratio is null on a zero denominator — R3
                # forbids passing that off as a zero.
                nullable=True,
                description=" ".join(metric.description.split()) or metric.name,
                metric_id=metric_id,
                unit=metric.format.unit,
                searchable=False,
            )
        )

    expectation = product.row_expectation(spec.entity_id)
    grains = {model.metric(m).grain for m in spec.metric_ids}
    return ContractDataset(
        name=spec.view_name,
        entity=spec.entity_id,
        description=f"{product.name}: {entity.name}.",
        grain=list(spec.grain),
        time_grain=next(iter(grains)).value if spec.bucketed and len(grains) == 1 else None,
        columns=columns,
        freshness_minutes=freshness_floor(list(spec.metric_ids), model, registry),
        expected_min_rows_per_day=expectation.min_rows_per_day,
        expected_max_rows_per_day=expectation.max_rows_per_day,
        sources=sorted(
            {s for m in spec.metric_ids for s in model.metric(m).requires_sources}
            | set(entity.sources)
        ),
    )


class ContractStore:
    """Published contract snapshots on disk, one directory per product.

    A published version's contract is frozen: the diff a consumer is shown is
    against what was actually published, not against a re-derivation from a
    semantic layer that has moved on since.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or CONTRACTS_DIR

    def versions(self, product_id: str) -> list[Version]:
        folder = self.directory / product_id
        if not folder.is_dir():
            return []
        versions: list[Version] = []
        for path in sorted(folder.glob("*.yaml")):
            try:
                versions.append(Version.parse(path.stem))
            except ValueError as exc:
                raise ConfigurationError(
                    f"{path}: contract snapshot filename must be a semantic version"
                ) from exc
        return sorted(versions, key=lambda v: v.key)

    def get(self, product_id: str, version: Version) -> DataContract:
        path = self.directory / product_id / f"{version}.yaml"
        if not path.is_file():
            raise ContractError(f"no published contract {product_id} {version}")
        contract = DataContract.from_yaml(path.read_text(encoding="utf-8"))
        if contract.product_id != product_id or contract.version != version:
            raise ConfigurationError(
                f"{path}: snapshot declares {contract.product_id} {contract.version}"
            )
        return contract

    def latest_before(self, product_id: str, version: Version) -> DataContract | None:
        candidates = [v for v in self.versions(product_id) if v < version]
        if not candidates:
            return None
        return self.get(product_id, candidates[-1])

    def write(self, contract: DataContract) -> Path:
        """Record a published contract. Called only after an approved publish."""
        folder = self.directory / contract.product_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{contract.version}.yaml"
        path.write_text(contract.to_yaml(), encoding="utf-8")
        return path


def contract_dict(contract: DataContract) -> dict[str, Any]:
    """JSON-safe mapping of a contract, for API responses and bundle files."""
    return contract.model_dump(mode="json")
