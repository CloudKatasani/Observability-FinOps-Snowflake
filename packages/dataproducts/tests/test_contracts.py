"""Contract derivation, drift detection, and change classification (§13.2, §13.3).

The diff is the gate that stops a breaking change reaching consumers under a
patch version, so every classification here is asserted individually rather than
through a single happy-path case.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from snowobs_dataproducts.contracts import (
    BREAKING_CHANGE_POLICY,
    BreakingChangeError,
    ChangeKind,
    ContractColumn,
    ContractError,
    ContractStore,
    DataContract,
    Severity,
    build_contract,
    diff,
    freshness_floor,
)
from snowobs_dataproducts.model import Bump, Classification, Version
from snowobs_dataproducts.registry import load_products
from snowobs_dataproducts.resolve import ColumnType
from snowobs_semantics.model import default_model
from snowobs_semantics.registry import default_registry


@pytest.fixture(scope="module")
def products():
    return load_products()


@pytest.fixture(scope="module")
def contract(products):
    return build_contract(products.get("finops_chargeback"))


def _bump(contract: DataContract, version: str) -> DataContract:
    return contract.model_copy(update={"version": Version.parse(version)})


def _with_column(contract: DataContract, dataset_name: str, column: ContractColumn) -> DataContract:
    datasets = [
        d.model_copy(update={"columns": [*d.columns, column]}) if d.name == dataset_name else d
        for d in contract.datasets
    ]
    return contract.model_copy(update={"datasets": datasets})


def _map_column(contract: DataContract, dataset_name: str, name: str, **update: object):
    datasets = []
    for dataset in contract.datasets:
        if dataset.name != dataset_name:
            datasets.append(dataset)
            continue
        columns = [c.model_copy(update=update) if c.name == name else c for c in dataset.columns]
        datasets.append(dataset.model_copy(update={"columns": columns}))
    return contract.model_copy(update={"datasets": datasets})


def _drop_column(contract: DataContract, dataset_name: str, name: str) -> DataContract:
    datasets = [
        d.model_copy(update={"columns": [c for c in d.columns if c.name != name]})
        if d.name == dataset_name
        else d
        for d in contract.datasets
    ]
    return contract.model_copy(update={"datasets": datasets})


# ═══════════════════════════════════════════════════════════ derivation ══════
def test_every_metric_column_is_bound_to_a_governed_metric(products) -> None:
    model = default_model()
    for product in products:
        built = build_contract(product)
        bound = built.metric_ids
        assert sorted(bound) == sorted(product.metrics)
        for metric_id in bound:
            assert metric_id in model.metrics


def test_freshness_guarantee_is_the_slowest_source_never_the_fastest(contract) -> None:
    """R7: the promise is a maximum over sources, not an average or a minimum."""
    assert contract.freshness_guarantee_minutes == max(
        d.freshness_minutes for d in contract.datasets
    )
    floor = freshness_floor(contract.metric_ids, default_model(), default_registry())
    assert contract.freshness_guarantee_minutes == floor


def test_grain_columns_are_not_null_and_measures_are(contract) -> None:
    for dataset in contract.datasets:
        for column in dataset.columns:
            if column.name == "TIME_BUCKET":
                assert not column.nullable
            if column.metric_id is not None:
                # R3: a bucket nothing contributed to is unknown, not zero.
                assert column.nullable


def test_credit_and_currency_columns_are_fixed_point(contract) -> None:
    """§27.7: no floating-point type may carry a credit or a currency figure."""
    for dataset in contract.datasets:
        for column in dataset.columns:
            assert "FLOAT" not in column.type.value
            assert "DOUBLE" not in column.type.value
    assert ColumnType.NUMBER_MONEY.value == "NUMBER(38,9)"


def test_ratio_metrics_carry_extra_scale(contract) -> None:
    share = next(
        column
        for dataset in contract.datasets
        for column in dataset.columns
        if column.metric_id == "chargeback.unattributed_share"
    )
    assert share.type is ColumnType.NUMBER_RATIO


def test_date_grained_relations_get_a_date_bucket(contract) -> None:
    """The bucket type is inferred from the entity's own projection, not guessed."""
    daily = contract.dataset("V_FINOPS_CHARGEBACK_COST_DAILY")
    assert daily.column("TIME_BUCKET").type is ColumnType.DATE
    hourly = contract.dataset("V_FINOPS_CHARGEBACK_WAREHOUSE_METERING_HOURLY")
    assert hourly.column("TIME_BUCKET").type is ColumnType.TIMESTAMP_LTZ


def test_contract_round_trips_through_yaml(contract) -> None:
    assert DataContract.from_yaml(contract.to_yaml()) == contract


def test_contract_publishes_the_breaking_change_policy(contract) -> None:
    assert contract.breaking_change_policy == BREAKING_CHANGE_POLICY


# ═══════════════════════════════════════════════════════════ validation ══════
def test_a_freshly_built_contract_matches_the_semantic_layer(products) -> None:
    for product in products:
        assert build_contract(product).validate_against().ok


def test_a_retyped_metric_is_reported_as_drift(contract) -> None:
    drifted = _map_column(
        contract,
        "V_FINOPS_CHARGEBACK_COST_DAILY",
        "CHARGEBACK_METERED_CREDITS",
        type=ColumnType.STRING,
    )
    validation = drifted.validate_against()
    assert not validation.ok
    assert any("now produces" in f.detail for f in validation.findings)


def test_a_vanished_metric_is_reported_as_drift(contract) -> None:
    drifted = _map_column(
        contract,
        "V_FINOPS_CHARGEBACK_COST_DAILY",
        "CHARGEBACK_METERED_CREDITS",
        metric_id="chargeback.metric_that_was_retired",
    )
    validation = drifted.validate_against()
    assert not validation.ok
    assert any("no longer exists" in f.detail for f in validation.findings)


def test_an_optimistic_freshness_promise_is_reported_as_drift(contract) -> None:
    datasets = [d.model_copy(update={"freshness_minutes": 5}) for d in contract.datasets]
    drifted = contract.model_copy(update={"datasets": datasets, "freshness_guarantee_minutes": 5})
    validation = drifted.validate_against()
    assert not validation.ok
    assert any("minutes of latency" in f.detail for f in validation.findings)


# ═══════════════════════════════════════════════ change classification ═══════
def test_adding_a_column_is_additive_and_needs_a_minor_bump(contract) -> None:
    new = _with_column(
        _bump(contract, "1.2.0"),
        "V_FINOPS_CHARGEBACK_COST_DAILY",
        ContractColumn(
            name="EXTRA", type=ColumnType.NUMBER_MONEY, nullable=True, description="added"
        ),
    )
    result = diff(contract, new)
    assert [c.kind for c in result.changes] == [ChangeKind.COLUMN_ADDED]
    assert result.required_bump is Bump.MINOR
    assert not result.breaking
    assert result.is_version_sufficient


def test_removing_a_column_is_breaking(contract) -> None:
    new = _drop_column(_bump(contract, "2.0.0"), "V_FINOPS_CHARGEBACK_COST_DAILY", "SERVICE_TYPE")
    result = diff(contract, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.COLUMN_REMOVED in kinds
    assert result.required_bump is Bump.MAJOR
    assert result.breaking


def test_removing_a_column_under_a_patch_bump_is_refused(contract) -> None:
    """§13.3: the diff refuses a patch bump that carries a breaking change."""
    new = _drop_column(_bump(contract, "1.1.1"), "V_FINOPS_CHARGEBACK_COST_DAILY", "SERVICE_TYPE")
    with pytest.raises(BreakingChangeError) as excinfo:
        diff(contract, new)
    assert excinfo.value.contract_diff.required_bump is Bump.MAJOR
    assert excinfo.value.contract_diff.declared_bump is Bump.PATCH
    assert excinfo.value.contract_diff.breaking


def test_removing_a_column_under_a_minor_bump_is_also_refused(contract) -> None:
    new = _drop_column(_bump(contract, "1.2.0"), "V_FINOPS_CHARGEBACK_COST_DAILY", "SERVICE_TYPE")
    with pytest.raises(BreakingChangeError):
        diff(contract, new)


def test_adding_a_column_under_a_patch_bump_is_refused(contract) -> None:
    new = _with_column(
        _bump(contract, "1.1.1"),
        "V_FINOPS_CHARGEBACK_COST_DAILY",
        ContractColumn(
            name="EXTRA", type=ColumnType.NUMBER_MONEY, nullable=True, description="added"
        ),
    )
    with pytest.raises(BreakingChangeError):
        diff(contract, new)


def test_retyping_a_column_is_breaking(contract) -> None:
    new = _map_column(
        _bump(contract, "2.0.0"),
        "V_FINOPS_CHARGEBACK_COST_DAILY",
        "CHARGEBACK_METERED_CREDITS",
        type=ColumnType.NUMBER_COUNT,
    )
    result = diff(contract, new)
    assert any(c.kind is ChangeKind.COLUMN_RETYPED and c.is_breaking for c in result.changes)


def test_relaxing_nullability_is_breaking_and_tightening_is_not(contract) -> None:
    relaxed = _map_column(
        _bump(contract, "2.0.0"), "V_FINOPS_CHARGEBACK_COST_DAILY", "TIME_BUCKET", nullable=True
    )
    result = diff(contract, relaxed)
    assert any(
        c.kind is ChangeKind.COLUMN_NULLABILITY_RELAXED and c.is_breaking for c in result.changes
    )

    tightened = _map_column(
        _bump(contract, "1.2.0"),
        "V_FINOPS_CHARGEBACK_COST_DAILY",
        "SERVICE_TYPE",
        nullable=False,
    )
    result = diff(contract, tightened)
    assert [c.kind for c in result.changes] == [ChangeKind.COLUMN_NULLABILITY_TIGHTENED]
    assert not result.breaking


def test_rebinding_a_column_to_another_metric_is_breaking(contract) -> None:
    new = _map_column(
        _bump(contract, "2.0.0"),
        "V_FINOPS_CHARGEBACK_COST_DAILY",
        "CHARGEBACK_METERED_CREDITS",
        metric_id="cost.billed_credits",
    )
    result = diff(contract, new)
    assert any(c.kind is ChangeKind.COLUMN_REBOUND and c.is_breaking for c in result.changes)


def test_a_documentation_change_alone_is_a_patch(contract) -> None:
    new = _map_column(
        _bump(contract, "1.1.1"),
        "V_FINOPS_CHARGEBACK_COST_DAILY",
        "SERVICE_TYPE",
        description="Reworded for clarity.",
    )
    result = diff(contract, new)
    assert [c.kind for c in result.changes] == [ChangeKind.COLUMN_DOCUMENTED]
    assert result.required_bump is Bump.PATCH


def test_changing_the_grain_is_breaking(contract) -> None:
    datasets = [
        d.model_copy(update={"grain": d.grain[:-1]})
        if d.name == "V_FINOPS_CHARGEBACK_COST_DAILY"
        else d
        for d in contract.datasets
    ]
    new = _bump(contract, "2.0.0").model_copy(update={"datasets": datasets})
    result = diff(contract, new)
    assert any(c.kind is ChangeKind.GRAIN_CHANGED and c.is_breaking for c in result.changes)


def test_loosening_freshness_is_breaking_and_tightening_is_not(contract) -> None:
    loosened = _bump(contract, "2.0.0").model_copy(
        update={"freshness_guarantee_minutes": contract.freshness_guarantee_minutes * 2}
    )
    result = diff(contract, loosened)
    assert any(c.kind is ChangeKind.FRESHNESS_RELAXED and c.is_breaking for c in result.changes)

    tightened = _bump(contract, "1.2.0").model_copy(
        update={"freshness_guarantee_minutes": contract.freshness_guarantee_minutes - 1}
    )
    result = diff(contract, tightened)
    assert [c.kind for c in result.changes] == [ChangeKind.FRESHNESS_TIGHTENED]


def test_shortening_retention_is_breaking(contract) -> None:
    new = _bump(contract, "2.0.0").model_copy(update={"retention_days": 30})
    result = diff(contract, new)
    assert any(c.kind is ChangeKind.RETENTION_REDUCED and c.is_breaking for c in result.changes)


def test_lowering_availability_is_breaking(contract) -> None:
    new = _bump(contract, "2.0.0").model_copy(update={"availability_pct": Decimal("95.0")})
    result = diff(contract, new)
    assert any(c.kind is ChangeKind.AVAILABILITY_REDUCED and c.is_breaking for c in result.changes)


def test_raising_the_classification_is_breaking(contract) -> None:
    new = _bump(contract, "2.0.0").model_copy(update={"classification": Classification.RESTRICTED})
    result = diff(contract, new)
    assert any(c.kind is ChangeKind.CLASSIFICATION_RAISED and c.is_breaking for c in result.changes)


def test_dropping_a_whole_relation_is_breaking(contract) -> None:
    new = _bump(contract, "2.0.0").model_copy(update={"datasets": contract.datasets[:-1]})
    result = diff(contract, new)
    assert any(c.kind is ChangeKind.DATASET_REMOVED and c.is_breaking for c in result.changes)


def test_every_change_kind_has_a_severity_and_a_required_bump() -> None:
    for kind in ChangeKind:
        assert kind.severity in set(Severity)
        assert kind.required_bump in set(Bump)
        if kind.severity is Severity.BREAKING:
            assert kind.required_bump is Bump.MAJOR


def test_an_unchanged_contract_produces_an_empty_diff(contract) -> None:
    result = diff(contract, _bump(contract, "1.1.1"))
    assert result.changes == []
    assert result.required_bump is Bump.NONE
    assert "No contract changes" in result.release_notes()


def test_diffing_two_different_products_is_refused(products) -> None:
    with pytest.raises(ContractError, match="different products"):
        diff(
            build_contract(products.get("finops_chargeback")),
            build_contract(products.get("pipeline_health")),
        )


def test_release_notes_separate_breaking_from_additive(contract) -> None:
    new = _drop_column(_bump(contract, "2.0.0"), "V_FINOPS_CHARGEBACK_COST_DAILY", "SERVICE_TYPE")
    notes = diff(contract, new).release_notes()
    assert "## Breaking changes" in notes
    assert "SERVICE_TYPE" in notes


# ══════════════════════════════════════════════ published snapshots ══════════
def test_the_shipped_snapshots_diff_exactly_as_their_changelogs_say(products) -> None:
    """The two products with release history are checked against what shipped."""
    store = ContractStore()

    finops = products.get("finops_chargeback")
    previous = store.latest_before(finops.id, finops.version)
    assert previous is not None and str(previous.version) == "1.0.0"
    result = diff(previous, build_contract(finops))
    assert not result.breaking
    assert result.required_bump is Bump.MINOR
    assert result.declared_bump is Bump.MINOR

    warehouse = products.get("warehouse_efficiency")
    previous = store.latest_before(warehouse.id, warehouse.version)
    assert previous is not None and str(previous.version) == "1.0.0"
    result = diff(previous, build_contract(warehouse))
    assert result.breaking
    assert result.required_bump is Bump.MAJOR
    assert result.declared_bump is Bump.MAJOR


def test_a_product_without_history_has_no_baseline(products) -> None:
    product = products.get("pipeline_health")
    assert ContractStore().latest_before(product.id, product.version) is None


def test_the_store_refuses_a_version_it_does_not_hold(products) -> None:
    with pytest.raises(ContractError, match="no published contract"):
        ContractStore().get("pipeline_health", Version.parse("9.9.9"))
