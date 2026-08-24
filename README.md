# Observability & FinOps Platform for Snowflake (`snowobs`)

A deployable enterprise application that turns Snowflake telemetry
(`ACCOUNT_USAGE`, `ORGANIZATION_USAGE`, `INFORMATION_SCHEMA`, event tables) into a
governed observability + FinOps capability: a conformed data model, a catalogue of
~90 KPIs across 9 domains, fully allocated chargeback that reconciles to the metered
bill, optimisation recommendations with dollar impact, forecasting and anomaly
detection, and an agentic layer that manages the whole thing as versioned,
contracted **data products**.

Two operating modes, one set of numbers:

| | **LIVE** | **OFFLINE** |
|---|---|---|
| Input | Direct Snowflake connection (read-only, key-pair) | CSV/Parquet extracts uploaded to the app |
| Engine | Snowflake pushdown | Embedded DuckDB over Parquet |
| Use | Production platform teams | Air-gapped assessments, POCs, pre-sales |

Both modes compile from the same declarative semantic layer and must produce
identical numbers for identical inputs — enforced by a dual-engine parity suite.

> **Project status: Phase 0 (foundations).** The build follows the phased plan in
> [`docs/BUILD_PROMPT.md`](docs/BUILD_PROMPT.md) §24 — currently: repo scaffold,
> settings, logging, health endpoints, compose stack, CI. Dashboards, ingestion,
> the semantic compiler, agents, and deployment artifacts land in Phases 1–8.

## Getting started (development)

Prerequisites: Docker (with Compose), [`uv`](https://docs.astral.sh/uv/), Node 22.

```bash
git clone <repo> && cd <repo>
cp .env.example .env
uv sync --all-packages --dev     # Python workspace
(cd apps/web && npm install)     # SPA
make dev                         # postgres/redis/minio containers + API + worker + web
```

- Web: http://localhost:5173 (status page at `/status`)
- API: http://localhost:8000 — `/healthz`, `/readyz`, `/api/v1/meta`, OpenAPI at `/docs`

Full container stack instead: `docker compose -f deploy/compose/docker-compose.yml up -d --build`
(web at http://localhost:8080).

## Development commands

```bash
make test        # pytest + vitest
make lint        # ruff + eslint
make typecheck   # mypy (strict on packages/) + tsc
make fmt         # auto-format Python
make build       # build container images
```

## Repository map

- `CLAUDE.md` — non-negotiable principles, stack, Definition of Done. Read first.
- `docs/BUILD_PROMPT.md` — the full product specification and phased plan.
- `docs/ASSUMPTIONS.md` — verified Snowflake/DuckDB/LLM facts with revisit triggers.
- `docs/adr/` — architecture decision records.
- `apps/` — `api` (FastAPI), `worker` (arq), `web` (React + Vite).
- `packages/` — shared Python libraries; `packages/semantics/` will hold the single
  source of truth for all metrics.
- `deploy/` — Dockerfiles, Compose; Terraform arrives in Phase 8.
- `config/branding.yaml` — white-label display name and palette.

## License

Proprietary — see [LICENSE](LICENSE).
