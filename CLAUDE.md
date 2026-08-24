# CLAUDE.md — Snowflake Observability & FinOps Platform (`snowobs`)

Source of truth for design intent: `docs/BUILD_PROMPT.md` (application concerns) and the
Snowflake Observability & FinOps HLD (data semantics: allocation waterfall, latencies,
KPI definitions, retention). This file summarises what must never be violated. When in
doubt, re-read the corresponding section of `docs/BUILD_PROMPT.md`.

## Non-negotiable principles (BUILD_PROMPT §2)

- **R1 — One semantic layer, two execution engines.** Metrics, dimensions, joins, and
  business logic are defined once, declaratively, in YAML under `packages/semantics/`.
  A compiler renders them to engine-specific SQL via SQLGlot. Never hand-write a
  "Snowflake version" and a "DuckDB version" of the same logic. Non-portable constructs
  go in `packages/semantics/dialect_shims.py` with a shim per engine and a parity test —
  never a forked metric definition.
- **R2 — The app never becomes the system of record.** LIVE mode reads Snowflake and
  caches aggregates; raw telemetry is not copied out unless `export.enabled` is set.
  App metadata (users, products, contracts, approvals, audit) lives in Postgres;
  telemetry does not.
- **R3 — Graceful degradation over hard failure.** A KPI with missing source data renders
  as "Unavailable — requires `ACCOUNT_USAGE.X`" with a remediation hint. Partial input
  produces a partially populated, fully functional app. Never crash, never show zero
  where the answer is unknown, never substitute nulls for zeros in a cost figure.
- **R4 — Read-only by default, least privilege always.** The app's Snowflake role is
  read-only and built from granular `SNOWFLAKE` database roles (`USAGE_VIEWER`,
  `GOVERNANCE_VIEWER`, `SECURITY_VIEWER`, `OBJECT_VIEWER`), never blanket
  `IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE`. Write paths use a separate, explicitly
  configured role and interactive approval.
- **R5 — Every number is traceable.** Every KPI tile, chart point, and agent claim can
  reveal the compiled SQL, source views, as-of timestamp, documented source latency, and
  allocation method. "Show the SQL" is a first-class UI affordance.
- **R6 — Allocated cost reconciles or does not publish.** Daily reconciliation
  (allocated total vs. `METERING_DAILY_HISTORY`, tolerance 0.5%) gates publication of
  chargeback figures. Failure = banner + P2 alert, never a quietly wrong dashboard.
- **R7 — Latency honesty.** Every surface states the freshness floor of its slowest
  source. Latency figures live in `config/source_registry.yaml` / the source registry
  YAML, verified against current docs — never hardcoded in application code.
- **R8 — Agents propose, humans dispose.** No agent mutates a Snowflake account, data
  product contract, alert rule, or budget without an explicit human approval event in
  the audit log (actor, timestamp, diff).
- **R9 — The SQL guard is mandatory.** All agent- or user-originated SQL passes through
  `packages/sqlguard/`: SQLGlot-parsed, single read-only `SELECT`/`WITH`, allowlisted
  schemas, forced `LIMIT`, statement timeout, warehouse pinning. No string-concatenation
  escape hatch anywhere.
- **R10 — Deployment parity.** The same container images run locally and on AWS.
  Environment differences live only in configuration and provider adapters
  (`storage`, `secrets`, `llm`, `queue`) — never branching application code.
- **R11 — LLM portability.** The LLM provider is an adapter: Anthropic API, Amazon
  Bedrock (Claude), Snowflake Cortex (LIVE only). Application code must not care which.
- **R12 — Deterministic core, probabilistic edge.** Numbers come from SQL; narratives
  come from the LLM. An agent never computes a figure itself — it quotes tool results
  from governed metric queries, or says it cannot ground the claim.

## Repository layout (BUILD_PROMPT §5)

Monorepo: Python backend, TypeScript frontend, Terraform infra, shared declarative packages.

```
snowobs/
├── CLAUDE.md  README.md  CHANGELOG.md  LICENSE  Makefile
├── config/                       # branding.yaml and other deploy-editable config
├── docs/                         # BUILD_PROMPT, ARCHITECTURE, SECURITY, RUNBOOK, DEMO,
│   └── adr/                      # ASSUMPTIONS, KPI_CATALOG, DATA_CONTRACTS, ADRs
├── apps/
│   ├── api/                      # FastAPI service (snowobs_api: routers/services/models/schemas)
│   ├── worker/                   # background jobs (arq)
│   └── web/                      # React + Vite + TS SPA
├── packages/
│   ├── semantics/                # ★ single source of truth: sources/entities/metrics/
│   │                             #   allocation YAML + compiler + dialect_shims.py
│   ├── engines/                  # QueryEngine protocol, duckdb adapter, cache, parity
│   ├── ingest/                   # profiler, mapper, loader, drift, export_script_gen
│   ├── sqlguard/
│   ├── agents/                   # runtime, specialists, prompts, evals
│   ├── llm/                      # base + anthropic/bedrock/cortex adapters
│   ├── analytics/                # forecast, anomaly, rightsizing, levers, alerting
│   ├── finops/                   # allocation waterfall + the reconciliation gate
│   ├── dataproducts/             # data product registry, contracts, lifecycle, and
│   │                             #   emitters/ (dbt / DDL / semantic view / listing /
│   │                             #   Cortex Search / agent spec)
│   ├── snowflake_live/           # connection, grant probe, provisioning, pushdown
│   └── common/                   # config, logging, telemetry, errors, security
├── fixtures/{generator,golden}/  # synthetic ACCOUNT_USAGE generator + parity fixtures
├── deploy/{docker,compose,terraform,helm,spcs}/
├── snowflake/{provisioning,publish}/
└── .github/workflows/{ci.yml,release.yml,security.yml}
```

`services/` contains orchestration only — no SQL strings there. SQL is produced only by
the semantic compiler or vetted by `sqlguard`.

## Technology stack — pinned (BUILD_PROMPT §6)

**Backend:** Python 3.12; `uv`; `ruff` (lint+format); `mypy --strict` on `packages/`;
FastAPI + Uvicorn; Pydantic v2 + `pydantic-settings`; `snowflake-connector-python`
(pandas/pyarrow extras, **not** SQLAlchemy for telemetry); `duckdb` + `pyarrow` +
`polars` for uploads; `sqlglot` (linchpin of R1/R9); `anthropic` SDK + `boto3` for
Bedrock + Cortex via the connector; `arq` (Redis-backed) worker (ADR-0002);
SQLModel/SQLAlchemy 2.x + Alembic for app metadata in Postgres; `structlog` +
`opentelemetry-sdk`; explicit trend + seasonality forecasting (no prophet).

**Frontend:** React 18 + TypeScript 5 + Vite; TanStack Query + TanStack Table; Zustand;
Tailwind CSS + shadcn/ui; ECharts (`echarts-for-react`) as the primary chart library;
`react-router`; `zod`; SSE for agent streaming.

**Data/infra:** Postgres 16; Redis 7; S3 (MinIO locally) behind a `storage` adapter;
Docker + Compose locally; ECS Fargate + Terraform on AWS.

**Rule:** no architecturally load-bearing dependency without an ADR in `docs/adr/`.

## Working style

- Production-grade code only: no `TODO`, no stubbed or mocked business logic, no
  illustrative pseudocode. If something cannot be completed, raise it explicitly.
- Every module ships with tests; every SQL/metric ships with a parity test (§23/§22.2).
- Prefer boring, auditable code over clever abstractions — client architects and
  auditors will read this.
- Assumptions are recorded in `docs/ASSUMPTIONS.md` with rationale and revisit trigger.
- Work one phase at a time (§24); a phase's exit criteria must pass in CI before the
  next begins. Conventional-commit at every phase boundary; keep `CHANGELOG.md` current.
- Verify Snowflake view names, latencies, and DDL against current docs (§25) — never
  from memory.

## Definition of Done (BUILD_PROMPT §26)

The build is complete only when all of the following hold:

- [ ] `git clone && make demo` → fully populated app in < 10 min with no Snowflake
      account, no cloud credentials, no LLM key (deterministic agent path).
- [ ] `terraform apply` in a clean AWS account → working private deployment; documented
      rollback tested.
- [ ] LIVE mode: key-pair connect, grants probe, every dashboard populates; missing
      grants produce correct remediation SQL.
- [ ] OFFLINE mode: upload CSV/Parquet extracts → same dashboards, same numbers.
- [ ] Parity suite green for every metric; tolerances documented in
      `docs/PARITY_EXCEPTIONS.md`.
- [ ] ~90 KPIs across 9 domains implemented and documented, each declaring sources and
      latency floor.
- [ ] Allocation reconciles within 0.5% on fixture and live data; the HLD worked example
      passes to the cent; the gate blocks on injected drift.
- [ ] All planted synthetic phenomena detected and correctly attributed vs. ground truth.
- [ ] Agent evals meet §12.6 thresholds; zero fabricated figures; zero injection
      compliance.
- [ ] A data product publishes end-to-end in LIVE mode and exports as an applyable
      bundle in OFFLINE mode.
- [ ] Coverage matrix accurate; every unavailable KPI explains its blocker (R3).
- [ ] Security: cross-tenant isolation tested, RBAC matrix tested, SQL-guard bypasses
      fail, secrets never in DB or logs, audit log complete and exportable.
- [ ] Performance targets (§22.3) met on the large fixture profile.
- [ ] `mypy --strict` clean on `packages/`; ruff clean; ≥85% coverage on `packages/`;
      Trivy high/critical clean; SBOM published.
- [ ] Documentation set complete, including a FinOps-analyst user guide.
- [ ] No `TODO`, no stubbed business logic, no dead code, no commented-out blocks.

## Anti-requirements (BUILD_PROMPT §27) — never do these

1. Separate Snowflake and DuckDB implementations of any business logic (R1).
2. An agent computing a number itself or stating a figure not returned by a tool (R12).
3. Blanket `IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE` (R4).
4. Agent- or user-supplied SQL executed outside the SQL guard (R9).
5. Copying raw telemetry out of Snowflake by default, or sending `QUERY_TEXT` to an LLM
   without explicit opt-in (R2, §12.5).
6. Applying any change to a customer's Snowflake account without a recorded human
   approval (R8).
7. Floating-point types for credits or currency, anywhere.
8. Hard-suspending a production warehouse via a resource monitor (§14).
9. Showing a figure without its as-of timestamp and latency floor (R5, R7).
10. Shipping a metric without a parity test, or an alert rule without a runbook link.
11. Substituting zeros for unknowns, or hiding a missing source behind an empty chart (R3).
12. Heavyweight agent frameworks that obscure the tool-call trace (§12.1).
13. Secrets in Postgres, logs, the frontend bundle, or plaintext Terraform state.
14. Assuming Snowflake view names, latencies, or DDL syntax from memory (§25).
15. Stubbing/mocking/deferring business logic without recording it in
    `docs/ASSUMPTIONS.md` and raising it explicitly.

## Branding

The display name is configuration (`config/branding.yaml`) and must be white-labellable
without a code change. Style the product descriptively — "Observability & FinOps
Platform for Snowflake" — and never incorporate the Snowflake trademark into the
product's own mark. Short handle everywhere technical: `snowobs`.

## Local development quickstart

```
make dev        # infra containers (postgres/redis/minio) + API + worker + web, hot reload
make test       # Python + web unit tests
make lint       # ruff + eslint
make typecheck  # mypy --strict (packages/ and apps) + tsc
```
