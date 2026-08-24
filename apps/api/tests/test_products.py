"""The data product API: catalogue, contract, diff, approvals, and bundle.

These exercise the whole chain — registry → contract → preflight → approval
ledger → artifact bundle — through the real application, including the refusal
paths a caller can hit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import httpx
import pytest

from snowobs_api.main import create_app
from snowobs_api.services import products as products_service
from snowobs_common.config import Settings
from snowobs_dataproducts.publish import LifecycleLedger

ACTOR = {"X-Snowobs-Actor": "sam@internal"}
REASON = {"reason": "Reviewed at the 2026-08-24 data governance board"}


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture(autouse=True)
def clean_ledger() -> Iterator[None]:
    """Each test starts with an empty approval ledger."""
    original = products_service._LEDGER
    products_service._LEDGER = LifecycleLedger()
    yield
    products_service._LEDGER = original


@asynccontextmanager
async def _client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ═══════════════════════════════════════════════════════════ catalogue ═══════
async def test_lists_every_registered_product(settings: Settings) -> None:
    async with _client(settings) as client:
        response = await client.get("/api/v1/products")
    assert response.status_code == 200
    payload = response.json()
    ids = {entry["id"] for entry in payload}
    assert {"finops_chargeback", "warehouse_efficiency", "pipeline_health"} <= ids
    for entry in payload:
        # R7/§27.9: no product surfaces without its freshness floor.
        assert entry["freshness_guarantee_minutes"] > 0
        assert entry["freshness_target_minutes"] >= entry["freshness_guarantee_minutes"]


async def test_returns_one_product_with_its_history(settings: Settings) -> None:
    async with _client(settings) as client:
        response = await client.get("/api/v1/products/warehouse_efficiency")
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == "2.0.0"
    assert payload["change_log"][-1]["breaking"] is True
    assert payload["change_log"][-1]["migration_note"]
    assert payload["contract_findings"] == []
    assert payload["sources"]


async def test_unknown_product_is_a_problem_document(settings: Settings) -> None:
    async with _client(settings) as client:
        response = await client.get("/api/v1/products/no_such_product")
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "Unknown data product" in response.json()["detail"]


# ═══════════════════════════════════════════════════════════ contract ═══════
async def test_returns_the_contract_with_no_drift(settings: Settings) -> None:
    async with _client(settings) as client:
        response = await client.get("/api/v1/products/finops_chargeback/contract")
    assert response.status_code == 200
    payload = response.json()
    assert payload["findings"] == []
    assert payload["datasets"]
    for dataset in payload["datasets"]:
        assert dataset["freshness_minutes"] <= payload["freshness_guarantee_minutes"]
        for column in dataset["columns"]:
            assert "FLOAT" not in column["type"]


async def test_the_contract_states_its_breaking_change_policy(settings: Settings) -> None:
    async with _client(settings) as client:
        response = await client.get("/api/v1/products/pipeline_health/contract")
    assert "BREAKING" in response.json()["breaking_change_policy"]


# ═══════════════════════════════════════════════════════════════ diff ═══════
async def test_diff_against_the_previous_published_version(settings: Settings) -> None:
    async with _client(settings) as client:
        response = await client.get("/api/v1/products/finops_chargeback/diff")
    payload = response.json()
    assert payload["baseline_version"] == "1.0.0"
    assert payload["target_version"] == "1.1.0"
    assert payload["breaking_count"] == 0
    assert payload["required_bump"] == "minor"
    assert payload["version_sufficient"] is True
    assert payload["refusal"] is None


async def test_diff_reports_a_breaking_release(settings: Settings) -> None:
    async with _client(settings) as client:
        response = await client.get("/api/v1/products/warehouse_efficiency/diff")
    payload = response.json()
    assert payload["breaking_count"] >= 1
    assert payload["required_bump"] == "major"
    assert payload["declared_bump"] == "major"
    assert "Breaking changes" in payload["release_notes"]


async def test_diff_says_so_when_there_is_no_baseline(settings: Settings) -> None:
    """R3: an explicit "nothing to compare against", not an empty diff."""
    async with _client(settings) as client:
        response = await client.get("/api/v1/products/pipeline_health/diff")
    payload = response.json()
    assert payload["baseline_version"] is None
    assert payload["changes"] == []
    assert "no earlier published contract" in payload["refusal"]


# ══════════════════════════════════════════════════════════ preflight ══════
async def test_preflight_reports_every_gate(settings: Settings) -> None:
    async with _client(settings) as client:
        response = await client.get("/api/v1/products/pipeline_health/preflight")
    payload = response.json()
    assert payload["passed"] is True
    assert len(payload["checks"]) == 6
    assert all(check["status"] == "pass" for check in payload["checks"])


# ═══════════════════════════════════════════════════ approvals & publish ════
async def test_the_full_approval_flow_produces_a_bundle(settings: Settings) -> None:
    async with _client(settings) as client:
        proposed = await client.post(
            "/api/v1/products/pipeline_health/propose", json=REASON, headers=ACTOR
        )
        assert proposed.status_code == 200
        assert proposed.json()["to_status"] == "proposed"

        approved = await client.post(
            "/api/v1/products/pipeline_health/approve", json=REASON, headers=ACTOR
        )
        assert approved.status_code == 200
        assert approved.json()["actor"] == "sam@internal"

        published = await client.post(
            "/api/v1/products/pipeline_health/publish", json=REASON, headers=ACTOR
        )
        assert published.status_code == 200
        bundle = published.json()
        assert bundle["approval"]["to_status"] == "published"
        assert "sql/02_published_views.sql" in bundle["file_names"]
        assert bundle["validation_checklist"]
        assert bundle["preflight"]["passed"] is True

        history = await client.get("/api/v1/products/pipeline_health/history")
        assert [e["to_status"] for e in history.json()] == [
            "proposed",
            "approved",
            "published",
        ]


async def test_an_approval_without_an_actor_is_refused(settings: Settings) -> None:
    """R8: an audit record nobody signed is not an audit record."""
    async with _client(settings) as client:
        response = await client.post("/api/v1/products/pipeline_health/propose", json=REASON)
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "X-Snowobs-Actor" in response.json()["detail"]


async def test_publishing_without_approval_is_refused(settings: Settings) -> None:
    async with _client(settings) as client:
        response = await client.post(
            "/api/v1/products/pipeline_health/publish", json=REASON, headers=ACTOR
        )
    assert response.status_code == 409
    assert "recorded approval" in response.json()["detail"]


async def test_skipping_the_proposal_step_is_refused(settings: Settings) -> None:
    async with _client(settings) as client:
        response = await client.post(
            "/api/v1/products/pipeline_health/approve", json=REASON, headers=ACTOR
        )
    assert response.status_code == 409
    assert "it can move to proposed" in response.json()["detail"]


async def test_a_throwaway_approval_reason_is_refused(settings: Settings) -> None:
    async with _client(settings) as client:
        await client.post("/api/v1/products/pipeline_health/propose", json=REASON, headers=ACTOR)
        response = await client.post(
            "/api/v1/products/pipeline_health/approve", json={"reason": "ok"}, headers=ACTOR
        )
    assert response.status_code == 400
    assert "reviewer can act on" in response.json()["detail"]


async def test_a_bundle_without_a_publication_is_refused(settings: Settings) -> None:
    """A bundle implies an approved release; generating one on demand would not."""
    async with _client(settings) as client:
        response = await client.get("/api/v1/products/pipeline_health/bundle")
    assert response.status_code == 409
    assert "no recorded publication" in response.json()["detail"]


async def test_the_bundle_is_downloadable_after_publication(settings: Settings) -> None:
    async with _client(settings) as client:
        await client.post("/api/v1/products/pipeline_health/propose", json=REASON, headers=ACTOR)
        await client.post("/api/v1/products/pipeline_health/approve", json=REASON, headers=ACTOR)
        await client.post("/api/v1/products/pipeline_health/publish", json=REASON, headers=ACTOR)
        response = await client.get("/api/v1/products/pipeline_health/bundle")
    assert response.status_code == 200
    payload = response.json()
    assert payload["files"]["README.md"].startswith("# Pipeline Reliability")
    assert payload["approval"]["actor"] == "sam@internal"


async def test_the_published_status_reflects_the_ledger(settings: Settings) -> None:
    async with _client(settings) as client:
        await client.post("/api/v1/products/pipeline_health/propose", json=REASON, headers=ACTOR)
        response = await client.get("/api/v1/products/pipeline_health")
    assert response.json()["status"] == "proposed"
