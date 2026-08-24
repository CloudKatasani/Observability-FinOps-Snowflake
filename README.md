# Observability & FinOps Platform for Snowflake (`snowobs`)

A deployable enterprise application that turns Snowflake telemetry
(`ACCOUNT_USAGE`, `ORGANIZATION_USAGE`, `INFORMATION_SCHEMA`, event tables) into a
governed observability + FinOps capability: a conformed data model, a catalogue of
~110 KPIs across 10 domains, fully allocated chargeback that reconciles to the metered
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

## The demo

The fastest way to see the whole platform is on synthetic data — no Snowflake
account, no cloud credentials, and no LLM key:

```bash
git clone <repo> && cd <repo>
make demo                        # http://localhost:8080
```

That generates a synthetic Snowflake organization — four accounts under
`ACME_GROUP`, each uploaded separately the way a real enterprise's extracts
arrive — ingests it through the real OFFLINE path, and serves the app from a
single container. A scope selector beside the time range reads every KPI at
organization level or for one account. With no LLM key the
agent still answers: questions route to governed metrics deterministically, and
it says plainly that it will not narrate the result. `make doctor` checks ports
and Docker resources first; `make demo-down` removes the stack and its volumes.

[`docs/DEMO.md`](docs/DEMO.md) is the guided walkthrough, including the
phenomena planted in the fixture data and where each one surfaces.

## Getting started (development)

Prerequisites: Docker (with Compose), [`uv`](https://docs.astral.sh/uv/), Node 22.

```bash
cp .env.example .env
uv sync --all-packages --dev     # Python workspace
(cd apps/web && npm install)     # SPA
make dev                         # postgres/redis/minio containers + API + worker + web
```

- Web: http://localhost:5173
- API: http://localhost:8000 — `/healthz`, `/readyz`, `/api/v1/meta`, OpenAPI at `/docs`

## Development commands

```bash
make test          # pytest + vitest
make test-parity   # dual-engine parity + golden SQL snapshots (gates merges, R1)
make eval          # agent golden-question suite (§12.6 gates)
make lint          # ruff + eslint
make typecheck     # mypy (strict on packages/) + tsc
make fmt           # auto-format Python
make seed          # ingest a synthetic dataset into the local OFFLINE lake
make catalog       # regenerate docs/KPI_CATALOG.md from the metric YAML
make contracts     # regenerate docs/DATA_CONTRACTS.md from the data product YAML
make provisioning  # regenerate the Snowflake grant SQL from the source registry
make build         # build container images
```

`make help` lists every target.

## Repository map

- `CLAUDE.md` — non-negotiable principles, stack, Definition of Done. Read first.
- `docs/BUILD_PROMPT.md` — the full product specification and phased plan.
- `docs/ARCHITECTURE.md` — the system as built, and where each principle is enforced.
- `docs/SECURITY.md` — threat model, the Snowflake privilege design, the SQL guard.
- `docs/RUNBOOK.md` — deploy, rollback, and a procedure for every alert.
- `docs/USER_GUIDE.md` — for the FinOps analyst who will use this daily.
- `docs/DEMO.md` — the guided walkthrough of the synthetic organization.
- `docs/KPI_CATALOG.md` — every KPI, its sources, and its freshness floor (generated).
- `docs/DATA_CONTRACTS.md` — the published data products and their contracts (generated).
- `docs/ASSUMPTIONS.md` — verified Snowflake/DuckDB/LLM facts with revisit triggers.
- `docs/PARITY_EXCEPTIONS.md` — every metric whose two engines are not bit-identical, and why.
- `docs/adr/` — architecture decision records.
- `apps/` — `api` (FastAPI), `worker` (arq), `web` (React + Vite).
- `packages/` — the shared libraries. `packages/semantics/` is the single source of
  truth for every metric; everything else compiles, guards, executes, or explains it.
- `deploy/` — Dockerfiles, Compose, and the Terraform for a private AWS deployment.
- `snowflake/` — the provisioning SQL, generated from the source registry.
- `config/branding.yaml` — white-label display name and palette.

## License

Proprietary — see [LICENSE](LICENSE).
