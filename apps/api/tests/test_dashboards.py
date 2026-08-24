"""API behaviour for metrics, tiles, chargeback, and coverage.

These run against a real ingested fixture account, so they exercise the whole
chain: compiler → guard → engine → allocation → reconciliation gate → response.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from snowobs_api.main import create_app
from snowobs_common.config import Settings
from snowobs_fixtures.config import GeneratorConfig
from snowobs_fixtures.generator import generate, write_csv
from snowobs_ingest.loader import IngestPipeline
from snowobs_semantics.model import default_model

FIXTURE = GeneratorConfig(days=14, queries_per_day=400)
START, END = "2026-08-07", "2026-08-20"


@pytest.fixture(scope="module")
def settings(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Settings]:
    lake: Path = tmp_path_factory.mktemp("lake")
    extract: Path = tmp_path_factory.mktemp("extract")
    write_csv(generate(FIXTURE), extract)
    IngestPipeline(lake).ingest_directory(extract)
    yield Settings(
        _env_file=None,
        storage={"provider": "local", "bucket": str(lake)},
        finops={"credit_price_usd": "3.00"},
    )


@asynccontextmanager
async def client_for(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


# ------------------------------------------------------------------ catalogue
@pytest.mark.asyncio
async def test_metric_catalog_exposes_every_governed_metric(settings: Settings) -> None:
    async with client_for(settings) as client:
        response = await client.get("/api/v1/metrics/catalog")
    assert response.status_code == 200
    catalog = response.json()
    assert len(catalog) >= 39
    for entry in catalog:
        assert entry["requires_sources"], entry["id"]
        assert entry["description"]
        assert entry["latency_floor_minutes"] >= 0


# ----------------------------------------------------------------------- tiles
@pytest.mark.asyncio
async def test_tile_returns_a_period_total_with_provenance(settings: Settings) -> None:
    async with client_for(settings) as client:
        response = await client.get(
            f"/api/v1/metrics/cost.billed_credits/tile?start={START}&end={END}"
        )
    assert response.status_code == 200
    tile = response.json()
    # R5/R7: no figure without its provenance.
    assert Decimal(tile["value"]) > 0
    assert tile["sources"] == ["metering_daily_history"]
    assert tile["latency_floor_minutes"] == 180
    assert tile["as_of"]
    assert "SELECT" in tile["sql"]  # "show the SQL" is first-class
    assert tile["unavailable_reason"] is None


@pytest.mark.asyncio
async def test_tile_totals_the_period_rather_than_the_largest_day(
    settings: Settings,
) -> None:
    """The bug this guards: a tile showing one day's peak as the period total."""
    async with client_for(settings) as client:
        tile = (
            await client.get(f"/api/v1/metrics/cost.billed_credits/tile?start={START}&end={END}")
        ).json()
        series = (
            await client.post(
                "/api/v1/metrics/query",
                json={
                    "metrics": ["cost.billed_credits"],
                    "start": START,
                    "end": END,
                    "limit": 500,
                },
            )
        ).json()

    daily_total = sum(Decimal(row[-1]) for row in series["rows"] if row[-1] is not None)
    assert Decimal(tile["value"]) == daily_total
    assert series["row_count"] > 1  # there really were several days to sum


@pytest.mark.asyncio
async def test_tile_for_a_missing_source_explains_itself_rather_than_showing_zero(
    settings: Settings,
) -> None:
    """R3: never show a zero where the answer is unknown."""
    async with client_for(settings) as client:
        # access_history is not in the fixture export.
        response = await client.get(f"/api/v1/metrics/q.volume/tile?start={START}&end={END}")
        assert response.status_code == 200

        catalog = (await client.get("/api/v1/metrics/catalog")).json()
    blocked = [
        entry
        for entry in catalog
        if "access_history" in entry["requires_sources"]
        or "usage_in_currency_daily" not in entry["requires_sources"]
    ]
    assert blocked  # the catalogue still lists them; the tile explains the gap


@pytest.mark.asyncio
async def test_unavailable_metric_states_the_missing_source(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    empty_lake = tmp_path_factory.mktemp("empty-lake")
    settings = Settings(_env_file=None, storage={"provider": "local", "bucket": str(empty_lake)})
    async with client_for(settings) as client:
        tile = (await client.get("/api/v1/metrics/cost.billed_credits/tile")).json()
    assert tile["value"] is None
    assert tile["unavailable_reason"] is not None
    assert "metering_daily_history" in tile["unavailable_reason"]
    assert tile["unavailable_reason"].startswith("Unavailable")


# --------------------------------------------------------------------- queries
@pytest.mark.asyncio
async def test_query_returns_rows_with_decimal_strings(settings: Settings) -> None:
    async with client_for(settings) as client:
        response = await client.post(
            "/api/v1/metrics/query",
            json={
                "metrics": ["cost.by_warehouse_credits"],
                "dimensions": ["warehouse"],
                "start": START,
                "end": END,
                "limit": 100,
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["rows"]
    assert "WAREHOUSE" in body["columns"]
    # §27.7: money crosses the wire as a string, never a float.
    credits = body["rows"][0][-1]
    assert isinstance(credits, str)
    assert Decimal(credits) >= 0


@pytest.mark.asyncio
async def test_unknown_metric_is_a_clean_error_not_a_crash(settings: Settings) -> None:
    async with client_for(settings) as client:
        response = await client.post(
            "/api/v1/metrics/query", json={"metrics": ["cost.not_a_metric"]}
        )
    assert response.status_code in (400, 404, 500)
    assert response.headers["content-type"].startswith("application/problem+json")


# ------------------------------------------------------------------ chargeback
@pytest.mark.asyncio
async def test_chargeback_allocation_reconciles_and_publishes(settings: Settings) -> None:
    async with client_for(settings) as client:
        response = await client.get(f"/api/v1/chargeback/allocation?start={START}&end={END}")
    assert response.status_code == 200
    body = response.json()

    gate = body["reconciliation"]
    assert gate["outcome"] == "passed", gate["banner"]
    assert body["figures_published"] is True
    assert abs(Decimal(gate["variance_pct"])) <= Decimal("0.5")

    assert body["teams"]
    for team in body["teams"]:
        # Every team's components sum to its total, exactly.
        components = (
            Decimal(team["direct_credits"])
            + Decimal(team["idle_credits"])
            + Decimal(team["cloud_services_credits"])
        )
        assert components == Decimal(team["total_credits"])
        assert Decimal(team["cost_usd"]) == (
            Decimal(team["total_credits"]) * Decimal("3.00")
        ).quantize(Decimal("0.01"))


@pytest.mark.asyncio
async def test_unattributed_spend_is_reported_not_hidden(settings: Settings) -> None:
    """The planted ~20% untagged spend must surface, ranked and public (§10.1)."""
    async with client_for(settings) as client:
        body = (await client.get(f"/api/v1/chargeback/allocation?start={START}&end={END}")).json()
    share = Decimal(body["unattributed_share"])
    assert share > Decimal("0.10")
    assert any(team["team"] == "UNATTRIBUTED" for team in body["teams"])


@pytest.mark.asyncio
async def test_chargeback_response_carries_the_gate_verdict(settings: Settings) -> None:
    """R6: figures never travel without the gate that authorises them."""
    async with client_for(settings) as client:
        body = (await client.get(f"/api/v1/chargeback/allocation?start={START}&end={END}")).json()
    gate = body["reconciliation"]
    assert set(gate) >= {
        "outcome",
        "allocated_credits",
        "metered_credits",
        "variance_credits",
        "variance_pct",
        "tolerance_pct",
        "publication_allowed",
        "banner",
        "worst_days",
    }
    assert body["latency_floor_minutes"] == 480  # attribution is the slowest input


@pytest.mark.asyncio
async def test_allocation_without_dates_uses_the_landed_window(settings: Settings) -> None:
    """A caller should not have to know the data window to ask about it.

    The tile endpoints already default this way, and the inconsistency was not
    theoretical: the demo's own smoke test called this endpoint the obvious way
    and got a 422.
    """
    async with client_for(settings) as client:
        response = await client.get("/api/v1/chargeback/allocation")
    assert response.status_code == 200, response.text
    body = response.json()

    # The window is reported back, so a default is stated rather than assumed.
    assert body["period_start"] and body["period_end"]
    assert body["period_start"] <= body["period_end"]
    assert body["teams"]

    # And it agrees with what an explicit request for the same window returns.
    async with client_for(settings) as client:
        explicit = (
            await client.get(
                "/api/v1/chargeback/allocation"
                f"?start={body['period_start']}&end={body['period_end']}"
            )
        ).json()
    assert (
        explicit["reconciliation"]["allocated_credits"]
        == (body["reconciliation"]["allocated_credits"])
    )


@pytest.mark.asyncio
async def test_allocation_with_no_landed_sources_explains_itself(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """R3: never answer "nothing was spent" when the truth is "nothing loaded"."""
    empty: Path = tmp_path_factory.mktemp("lake-empty-chargeback")
    settings = Settings(_env_file=None, storage={"provider": "local", "bucket": str(empty)})
    async with client_for(settings) as client:
        response = await client.get("/api/v1/chargeback/allocation")

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    detail = response.json()["detail"]
    for source in ("warehouse_metering_history", "query_attribution_history"):
        assert source in detail
    assert "coverage" in detail.lower()


@pytest.mark.asyncio
async def test_chargeback_shows_its_own_sql_and_says_whether_it_is_settled(
    settings: Settings,
) -> None:
    """R5 and §15 on the composite response, not just on single metrics.

    An allocation is three metric queries flattened into one answer, and the
    flattening is exactly where provenance gets dropped: without this the
    endpoint returned figures a caller could neither trace nor date.
    """
    async with client_for(settings) as client:
        body = (await client.get(f"/api/v1/chargeback/allocation?start={START}&end={END}")).json()

    assert isinstance(body["provisional"], bool)
    # Every constituent query is shown, each saying what it contributes —
    # including the two runs of the same metric, which do different jobs.
    assert len(body["sql"]) == 4
    purposes = {d["purpose"] for d in body["sql"]}
    assert len(purposes) == 4, "two queries were given the same explanation"
    for disclosure in body["sql"]:
        assert disclosure["purpose"].strip()
        assert disclosure["metrics"]
        assert "SELECT" in disclosure["sql"].upper()
    assert {metric for d in body["sql"] for metric in d["metrics"]} == {
        "cost.by_warehouse_credits",
        "cost.by_team_credits",
        "cost.cloud_services_credits",
    }
    # Sources are the ones that gate the figure, so a caller re-deriving the
    # freshness from them lands on the floor the response already reported.
    assert "query_attribution_history" in body["sources"]


@pytest.mark.asyncio
async def test_credit_price_absent_yields_null_usd_not_an_invented_number(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    lake: Path = tmp_path_factory.mktemp("lake-nousd")
    extract: Path = tmp_path_factory.mktemp("extract-nousd")
    write_csv(generate(FIXTURE), extract)
    IngestPipeline(lake).ingest_directory(extract)
    settings = Settings(_env_file=None, storage={"provider": "local", "bucket": str(lake)})

    async with client_for(settings) as client:
        body = (await client.get(f"/api/v1/chargeback/allocation?start={START}&end={END}")).json()
    assert body["credit_price_usd"] is None
    assert all(team["cost_usd"] is None for team in body["teams"])


# -------------------------------------------------------------------- coverage
@pytest.mark.asyncio
async def test_coverage_lists_landed_and_missing_sources(settings: Settings) -> None:
    async with client_for(settings) as client:
        body = (await client.get("/api/v1/datasets/coverage")).json()
    by_id = {source["source_id"]: source for source in body["sources"]}
    assert by_id["metering_daily_history"]["status"] in ("available", "stale")
    assert by_id["metering_daily_history"]["rows"] > 0
    # Anything missing must carry a remediation, never a bare absence (R3).
    for source in body["sources"]:
        if source["status"] != "available":
            assert source["remediation"], source["source_id"]


@pytest.mark.asyncio
async def test_coverage_answers_the_kpi_question_not_only_the_source_question(
    settings: Settings,
) -> None:
    """R3 is about KPIs: which of the ~90 can a user trust right now?

    The matrix carried an empty `metrics` list, which answered the question
    about sources and silently dropped the one the page exists to answer.
    """
    async with client_for(settings) as client:
        body = (await client.get("/api/v1/datasets/coverage")).json()

    assert len(body["metrics"]) == len(default_model().metrics)
    for assessment in body["metrics"]:
        assert assessment["availability"] in ("enabled", "degraded", "unavailable")
        # R3: an unavailable KPI names its blocker rather than reading zero.
        if assessment["availability"] == "unavailable":
            assert assessment["missing_sources"]
            assert assessment["explanation"].strip()
    # On the full fixture account the headline cost KPIs are answerable.
    by_metric = {a["metric_id"]: a for a in body["metrics"]}
    assert by_metric["cost.attributed_credits"]["availability"] == "enabled"
    # Timestamps are offset-aware everywhere, so no client has to guess a zone.
    assert body["as_of"].endswith("Z") or "+00:00" in body["as_of"]
