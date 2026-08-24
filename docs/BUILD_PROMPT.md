# Claude Code Build Prompt
## Snowflake Observability & FinOps Platform

> **Product name:** Snowflake Observability & FinOps Platform. **Short handle** (repo, CLI, container images, env prefix): `snowobs`.
> Style the name descriptively — *"Observability & FinOps Platform for Snowflake"* — in any client-facing or marketing surface; do not incorporate the Snowflake trademark into the asset's own mark. The display name lives in `config/branding.yaml` and must be white-labellable without a code change.
> **Audience for this prompt:** Claude Code (agentic coding session).
> **Source of truth for design intent:** the Snowflake Observability & FinOps HLD (EARB/CAB client deliverable). Where this prompt and the HLD disagree, this prompt wins for *application* concerns; the HLD wins for *data semantics* concerns (allocation waterfall, latencies, KPI definitions, retention).

---

## 0. How to use this prompt

1. Create an empty repo and drop this file at `docs/BUILD_PROMPT.md`.
2. Start Claude Code in the repo root and say:
   > *Read `docs/BUILD_PROMPT.md` in full. Then write `CLAUDE.md` summarising the non-negotiable principles (§2), the repo layout (§5), the tech stack (§6) and the Definition of Done (§26). Then execute Phase 0 from §24 and stop for review.*
3. Work **one phase at a time** (§24). Each phase has explicit exit criteria. Do not begin phase *N+1* until phase *N*'s criteria pass in CI.
4. Before writing any Snowflake SQL or feature-detection code, complete the verification checklist in §25. Snowflake's usage-view surface and Cortex object DDL move fast; do not trust training-data recall.
5. Commit at every phase boundary with a conventional-commit message and update `CHANGELOG.md`.

**Working style expected of you (Claude Code):**
- Production-grade code only. No `TODO`, no `pass  # implement later`, no mocked business logic, no illustrative pseudocode. If something cannot be completed, raise it explicitly rather than stubbing it silently.
- Every module ships with tests. Every SQL/metric ships with a parity test (§23).
- Prefer boring, auditable code over clever abstractions. This system will be read by client architects and auditors.
- When you must make an assumption, record it in `docs/ASSUMPTIONS.md` with a rationale and a revisit trigger.

---

## 1. Mission and product definition

### 1.1 The problem

Snowflake exposes very rich telemetry (`SNOWFLAKE.ACCOUNT_USAGE`, `SNOWFLAKE.ORGANIZATION_USAGE`, `INFORMATION_SCHEMA`, event tables), but turning it into a governed observability + FinOps capability takes a multi-month engineering programme: collectors, a star schema, an attribution waterfall, a KPI catalogue, alerting, dashboards, and a conversational layer. Most organisations never finish, and the ones that do build a bespoke, unshareable version of the same thing.

### 1.2 What the platform is

A **deployable enterprise application** that delivers that whole capability as a product. A platform team points it at a Snowflake account — or, where no connectivity is permitted, uploads CSV extracts of the usage views — and within minutes has:

- a conformed observability + FinOps data model,
- a governed catalogue of ~90 KPIs across 9 domains,
- fully allocated cost with chargeback that reconciles to the metered bill,
- optimisation recommendations with dollar impact and evidence,
- forecasting, anomaly detection, and budget variance,
- an **agentic layer** that manages the whole thing as a set of *data products* — proposing, versioning, contracting, publishing, and answering questions about them,
- deployable Snowflake assets (dbt project, semantic view, Cortex Agent, internal Marketplace listing) generated as output artifacts.

### 1.3 What "Agentic Data Product Management" means here

The application does not merely *have* a chatbot bolted onto dashboards. Agents own lifecycle responsibilities over data products:

| Capability | Non-agentic version | Agentic version |
|---|---|---|
| Onboarding a source | Human writes a mapping | **Onboarding Agent** profiles the source, proposes a canonical mapping + data contract, flags drift, asks for confirmation |
| Defining a data product | Human writes YAML | **Curator Agent** proposes product boundary, grain, SLA, owner, semantic model, and verified queries from observed usage |
| Explaining a cost spike | Human writes 6 queries | **FinOps Agent** decomposes the delta across dimensions, tests hypotheses, produces a narrative with the evidence SQL shown |
| Optimisation | Static "top N expensive queries" | **Optimisation Agent** ranks levers by modelled $ impact and risk, drafts the change, and writes the CAB-ready change record |
| Publishing | Manual Snowsight clicks | **Publisher Agent** emits secure views + semantic view + listing manifest + agent spec, runs the validation checklist, and opens a PR |

Every agent action is **proposed, evidenced, and human-approvable** — never silently applied to a customer's Snowflake account. See §12.6.

### 1.4 The two operating modes (this is the core product requirement)

| | **Mode A — LIVE** | **Mode B — OFFLINE** |
|---|---|---|
| Input | Direct Snowflake connection | CSV/Parquet upload of usage-view extracts |
| Engine | Snowflake (pushdown; app issues SQL, stores no telemetry rows) | Embedded DuckDB over Parquet in object storage |
| Freshness | Live, latency-matched to each source view | As-of the extract |
| Writes to Snowflake | Optional and always opt-in (provisioning wizard, data-product publication) | None |
| Output artifacts | Deployed + exported | Exported only |
| Typical use | Production platform team | Air-gapped assessments, pre-sales, POCs, client environments where connectivity needs 6 weeks of approvals |

**Both modes must produce identical numbers for identical inputs.** This is the hardest requirement in the build and the one most likely to be violated by careless implementation. §2 R1 and §22.2 exist to prevent that.

---

## 2. Non-negotiable architectural principles

These are the rules Claude Code must not violate. Put them at the top of `CLAUDE.md`.

**R1 — One semantic layer, two execution engines.**
Metrics, dimensions, joins, and business logic are defined **once**, in declarative YAML under `packages/semantics/`. A compiler renders them to engine-specific SQL via SQLGlot. There must be **no** hand-written duplicate SQL for "the Snowflake version" and "the DuckDB version" of anything. If a construct cannot be expressed portably, add it to `packages/semantics/dialect_shims.py` with a shim per engine and a parity test — never fork the metric definition.

**R2 — The app never becomes the system of record.**
In LIVE mode, the app reads Snowflake and caches aggregates; it does not copy raw telemetry out of Snowflake unless the operator explicitly enables `export.enabled`. Metadata (users, products, contracts, approvals, audit) lives in the app's own Postgres. Telemetry does not.

**R3 — Graceful degradation over hard failure.**
Any KPI whose source data is missing renders as *"Unavailable — requires `ACCOUNT_USAGE.X`"* with a one-click remediation hint. A partial upload must produce a partially populated, fully functional app. Never crash, never show zero where the answer is unknown, never silently substitute nulls for zeros in a cost figure.

**R4 — Read-only by default, least privilege always.**
The Snowflake role the app uses is read-only and built from **granular database roles** (`SNOWFLAKE.USAGE_VIEWER`, `GOVERNANCE_VIEWER`, `SECURITY_VIEWER`, `OBJECT_VIEWER` as applicable) — never blanket `IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE`. Any write path (provisioning, publishing) uses a **separate, explicitly configured** role and requires interactive approval.

**R5 — Every number is traceable.**
Each KPI tile, chart point, and agent claim can reveal (a) the compiled SQL, (b) the source views involved, (c) the as-of timestamp and the source's documented latency, (d) the allocation method applied. "Show the SQL" is a first-class UI affordance, not a debug feature.

**R6 — Allocated cost reconciles or does not publish.**
The daily reconciliation gate (allocated total vs. `METERING_DAILY_HISTORY`, tolerance 0.5%) blocks publication of chargeback figures. A failed gate surfaces as a banner and a P2 alert, not as a quietly wrong dashboard.

**R7 — Latency honesty.**
Every surface states the freshness floor implied by its slowest source (`QUERY_HISTORY` ~45 min, `QUERY_ATTRIBUTION_HISTORY` ~8 h, `METERING_DAILY_HISTORY` ~3 h, `ORGANIZATION_USAGE` currency views 24–72 h with month-end restatement). Never imply real-time where it does not exist. Verify these figures against current docs (§25) and store them in `config/source_registry.yaml`, not in code.

**R8 — Agents propose, humans dispose.**
No agent mutates a Snowflake account, a data product contract, an alert rule, or a budget without an explicit human approval event recorded in the audit log with actor, timestamp, and diff.

**R9 — The SQL guard is mandatory.**
All agent- or user-originated SQL passes through `packages/sqlguard/` before execution: parsed with SQLGlot, rejected unless a single read-only `SELECT`/`WITH`, restricted to an allowlisted schema set, forced `LIMIT`, statement timeout, and warehouse pinning. No string-concatenation escape hatch anywhere in the codebase.

**R10 — Deployment parity.**
The same container images run locally and on AWS. Environment differences are expressed only through configuration and provider adapters (`storage`, `secrets`, `llm`, `queue`), never through branching application code.

**R11 — Portability of the LLM.**
The LLM provider is an adapter: Anthropic API, Amazon Bedrock (Claude), and Snowflake Cortex (LIVE mode only). Clients will insist on at least one of these; the code must not care which.

**R12 — Deterministic core, probabilistic edge.**
Numbers come from SQL. Narratives come from the LLM. An agent never computes a figure itself — it calls a tool that runs a governed metric query and quotes the result. If an agent cannot ground a claim in a tool result, it says so.

---

## 3. Personas and primary journeys

| Persona | Needs | Primary surface |
|---|---|---|
| **Platform Owner** | Is the platform healthy? Where is spend going? What do I take to the steering committee? | Executive dashboard, forecast, optimisation backlog |
| **FinOps Analyst** | Monthly close, allocation disputes, forecast accuracy, commitment posture | Chargeback workbench, reconciliation report, close workflow |
| **Data Engineer** | Why did my pipeline slow down / cost more? What do I fix first? | Engineering deep-dive, offender fingerprints, query drill-through |
| **Team Lead (consumer)** | What did my team spend and why? Dispute a line. | Team chargeback dashboard (row-secured) |
| **Security Lead** | Who read sensitive objects, privilege drift, dormant identities | Security & access dashboard (role-gated) |
| **Data Product Owner** | Publish, version, and support an observability data product | Data Product Studio |
| **Assessor / Consultant** | Land in a client environment with no connectivity, upload extracts, produce findings in a day | Upload wizard + Assessment Report export |

### 3.1 Golden journeys (must work end-to-end, demoed in `docs/DEMO.md`)

1. **Zero-to-insight, offline (< 10 minutes).** Download the export script → run in Snowsight → upload the zip → coverage matrix → dashboards populated → "explain last week's spend increase" answered by the agent → export Assessment Report PDF.
2. **Zero-to-insight, live (< 5 minutes).** Enter account + key-pair → connection test shows which grants are present/missing with copy-pastable remediation SQL → dashboards populated from pushdown queries.
3. **Monthly chargeback close.** Reconciliation gate green → allocation by team → publish → dispute window opens → dispute raised → analyst adjusts a mapping rule → re-run → audit trail intact.
4. **Optimisation loop.** Agent ranks levers → user selects "auto-suspend tuning on 12 ELT warehouses" → agent produces the change SQL, the modelled saving, the risk note, the rollback, and a CAB-ready change record → user approves → tracked as a savings claim → verified after 14 days against actuals.
5. **Publish a data product.** Curator proposes the "Platform Cost & Attribution" product → contract and SLA drafted → semantic model generated → validation checklist run → LIVE mode: deploy secure views + semantic view + Cortex Search + internal listing; OFFLINE mode: export the same as a deployable bundle.

---

## 4. System architecture

```
┌───────────────────────────────────────────────────────────────────────────────┐
│ CLIENTS   Browser (React SPA)  ·  REST/SSE  ·  CLI (snowobs ...)                   │
└───────────────────────────────┬───────────────────────────────────────────────┘
                                │  OIDC / session
┌───────────────────────────────▼───────────────────────────────────────────────┐
│ API TIER — FastAPI                                                            │
│  auth+RBAC │ tenants │ connections │ uploads │ metrics │ agents │ products     │
│  alerts    │ exports │ admin       │ audit   │ health                          │
└───┬───────────────┬───────────────────┬───────────────────┬───────────────────┘
    │               │                   │                   │
    ▼               ▼                   ▼                   ▼
┌─────────┐  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────────┐
│ SEMANTIC│  │ QUERY ENGINE │  │  AGENT RUNTIME   │  │ DATA PRODUCT REGISTRY│
│ LAYER   │  │  (adapter)   │  │  supervisor +    │  │ contracts, SLAs,     │
│ YAML →  │─▶│ ┌──────────┐ │  │  5 specialists   │  │ versions, lineage,   │
│ SQLGlot │  │ │Snowflake │ │  │  tool-use loop   │  │ subscriptions        │
│ compiler│  │ ├──────────┤ │  │  SQL guard       │  └──────────┬───────────┘
└─────────┘  │ │ DuckDB   │ │  │  trace + eval    │             │ emit
             │ └──────────┘ │  └────────┬─────────┘             ▼
             └───┬──────┬───┘           │            ┌──────────────────────┐
                 │      │               ▼            │ ARTIFACT GENERATOR   │
                 │      │        ┌─────────────┐     │ dbt · DDL · semantic │
                 │      │        │ LLM ADAPTER │     │ listing · agent spec │
                 │      │        │ Anthropic / │     │ Terraform · JIL      │
                 │      │        │ Bedrock /   │     └──────────────────────┘
                 │      │        │ Cortex      │
                 │      │        └─────────────┘
                 │      └──────────────────────────────┐
                 ▼                                     ▼
    ┌────────────────────────┐              ┌────────────────────────────┐
    │ SNOWFLAKE (LIVE)       │              │ LOCAL LAKE (OFFLINE)       │
    │ ACCOUNT_USAGE          │              │ S3/MinIO Parquet + DuckDB  │
    │ ORGANIZATION_USAGE     │              │ from CSV uploads           │
    │ INFORMATION_SCHEMA     │              └────────────────────────────┘
    │ event tables           │
    └────────────────────────┘
                 ▲
    ┌────────────┴───────────┐   ┌──────────────┐   ┌─────────────────────┐
    │ WORKER (Celery/arq)    │   │ POSTGRES     │   │ OBJECT STORE        │
    │ refresh · alerts ·     │   │ app metadata │   │ uploads · parquet · │
    │ close · forecast·evals │   │ audit · RBAC │   │ exports · caches    │
    └────────────────────────┘   └──────────────┘   └─────────────────────┘
```

**Request paths to implement:**
- *Dashboard tile* → API → semantic compiler → engine adapter → cache → response with `{value, as_of, latency_floor, sql_ref, sources[]}`.
- *Agent turn* → API (SSE) → supervisor → tool calls (`query_metric`, `search_logs`, `run_sql_guarded`, `list_sources`, `explain_delta`, `simulate_lever`) → grounded narrative streamed with tool trace.
- *Upload* → API (multipart, chunked) → worker → profile → map → validate → Parquet → coverage matrix → cache warm.

---

## 5. Repository layout

Monorepo. Python backend, TypeScript frontend, Terraform infra, shared declarative packages.

```
snowobs/
├── CLAUDE.md                      # you write this in Phase 0
├── README.md  CHANGELOG.md  LICENSE  Makefile
├── docs/
│   ├── BUILD_PROMPT.md            # this file
│   ├── ARCHITECTURE.md  SECURITY.md  RUNBOOK.md  DEMO.md
│   ├── ASSUMPTIONS.md  KPI_CATALOG.md  DATA_CONTRACTS.md
│   └── adr/ADR-0001-*.md          # one ADR per material decision
├── apps/
│   ├── api/                       # FastAPI service
│   │   ├── snowobs_api/
│   │   │   ├── main.py  deps.py  settings.py
│   │   │   ├── routers/{auth,tenants,connections,uploads,metrics,
│   │   │   │            agents,products,alerts,exports,admin,health}.py
│   │   │   ├── services/           # orchestration, no SQL strings here
│   │   │   ├── models/             # SQLModel/SQLAlchemy ORM (app metadata)
│   │   │   └── schemas/            # Pydantic v2 request/response
│   │   └── tests/
│   ├── worker/                    # background jobs
│   └── web/                       # React + Vite + TS
│       ├── src/{pages,components,charts,hooks,api,state,styles}
│       └── tests/
├── packages/
│   ├── semantics/                 # ★ the single source of truth
│   │   ├── sources/*.yaml         # canonical source view registry
│   │   ├── entities/*.yaml        # facts + dims (star schema)
│   │   ├── metrics/*.yaml         # ~90 KPIs, 9 domains
│   │   ├── allocation/*.yaml      # chargeback waterfall config
│   │   ├── compiler/              # YAML → AST → SQLGlot → dialect SQL
│   │   └── dialect_shims.py
│   ├── engines/
│   │   ├── base.py                # QueryEngine protocol
│   │   ├── snowflake_engine.py
│   │   ├── duckdb_engine.py
│   │   └── cache.py
│   ├── ingest/
│   │   ├── profiler.py  mapper.py  loader.py  drift.py
│   │   └── export_script_gen.py   # generates the user's extract script
│   ├── sqlguard/
│   ├── agents/
│   │   ├── runtime/{supervisor,tools,trace,memory,budget}.py
│   │   ├── specialists/{onboarding,finops,sre,governance,curator}.py
│   │   ├── prompts/*.md
│   │   └── evals/{golden_questions.yaml,harness.py}
│   ├── llm/{base,anthropic,bedrock,cortex}.py
│   ├── analytics/{forecast,anomaly,rightsizing,levers,recommender}.py
│   ├── products/                  # data product registry + lifecycle
│   ├── artifacts/                 # dbt / DDL / semantic / listing emitters
│   └── common/{config,logging,telemetry,errors,security}.py
├── fixtures/
│   ├── generator/                 # ★ synthetic ACCOUNT_USAGE generator
│   └── golden/                    # parity fixtures + expected outputs
├── deploy/
│   ├── docker/{Dockerfile.api,Dockerfile.web,Dockerfile.worker,
│   │           Dockerfile.allinone}
│   ├── compose/{docker-compose.yml,docker-compose.demo.yml}
│   ├── terraform/{modules/*,envs/{dev,prod}}
│   ├── helm/                      # optional EKS path
│   └── spcs/                      # optional Snowpark Container Services
├── snowflake/
│   ├── provisioning/*.sql         # read-only role + grants (idempotent)
│   └── publish/*.sql              # data product deployment (opt-in)
└── .github/workflows/{ci.yml,release.yml,security.yml}
```

---

## 6. Technology stack (pin these)

**Backend**
- Python 3.12; `uv` for dependency + venv management; `ruff` (lint+format); `mypy --strict` on `packages/`.
- FastAPI + Uvicorn; Pydantic v2 (`pydantic-settings` for config).
- `snowflake-connector-python` (with `pandas`/`pyarrow` extras) — **not** SQLAlchemy for telemetry queries.
- `duckdb` (embedded), `pyarrow`, `polars` for extract/transform of uploads.
- `sqlglot` for parse/transpile/validate — the linchpin of R1 and R9.
- `anthropic` SDK; `boto3` for Bedrock; Cortex via the Snowflake connector.
- `arq` (Redis-backed) **or** Celery for the worker — pick one, justify in an ADR; prefer `arq` for its lighter footprint.
- `SQLModel`/SQLAlchemy 2.x + Alembic for app metadata in Postgres.
- `structlog` + `opentelemetry-sdk` for logs/traces/metrics.
- `statsforecast` or `prophet`-free approach: implement trend + weekly/monthly seasonality explicitly (see §11.1) — explainability beats accuracy here.

**Frontend**
- React 18 + TypeScript 5 + Vite; TanStack Query + TanStack Table; Zustand for app state.
- Tailwind CSS + shadcn/ui primitives; ECharts (via `echarts-for-react`) for dense analytical charts, Recharts acceptable for simple ones. Choose one primary and stay consistent.
- `react-router`; `zod` for runtime validation of API payloads; SSE for agent streaming.

**Data / infra**
- Postgres 16 (RDS on AWS, container locally).
- Redis 7 (ElastiCache / container).
- S3 (MinIO locally, S3 on AWS) behind a `storage` adapter.
- Docker + Compose locally; ECS Fargate + Terraform on AWS.

**Rule:** no dependency added without an entry in `docs/adr/` if it is architecturally load-bearing.

---

## 7. Data plane

### 7.1 Canonical source registry

`packages/semantics/sources/` contains one YAML per source object. This registry is the **only** place source-view knowledge lives — schedules, latencies, key columns, retention, edition requirements, and CSV import rules all read from it. Adding a new source view must require **zero code changes**.

```yaml
# packages/semantics/sources/query_history.yaml
id: query_history
snowflake_object: SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
domain: query
criticality: core                 # core | important | optional
edition_min: standard
required_db_role: SNOWFLAKE.USAGE_VIEWER
documented_latency_minutes: 45    # VERIFY against current docs (§25)
retention_days: 365
grain: [QUERY_ID]
time_column: START_TIME
watermark:
  column: START_TIME
  lookback_minutes: 180           # absorbs late-arriving rows
load_strategy: incremental_watermark
csv:
  header_signature: [QUERY_ID, QUERY_TEXT, DATABASE_NAME, WAREHOUSE_NAME, START_TIME]
  aliases: ["query_history", "QUERY_HISTORY", "query_history_export"]
columns:
  - {name: QUERY_ID, type: string, required: true}
  - {name: START_TIME, type: timestamp_ltz, required: true}
  - {name: TOTAL_ELAPSED_TIME, type: number, required: true}
  - {name: QUEUED_OVERLOAD_TIME, type: number, required: false, default: 0}
  - {name: BYTES_SPILLED_TO_REMOTE_STORAGE, type: number, required: false, default: 0}
  - {name: QUERY_PARAMETERIZED_HASH, type: string, required: false}
  # ... complete the column list from the live INFORMATION_SCHEMA introspection
sensitivity:
  QUERY_TEXT: restricted          # redact unless role in {platform_admin, security}
enables_metrics: [q.*, wh.queue_*, cost.per_query, opt.offender_*]
```

**Source objects to register (minimum set).** Verify names, latencies, and edition gates before use (§25).

*Cost & metering:* `METERING_DAILY_HISTORY`, `METERING_HISTORY`, `WAREHOUSE_METERING_HISTORY`, `QUERY_ATTRIBUTION_HISTORY`, `SERVERLESS_TASK_HISTORY`, `AUTOMATIC_CLUSTERING_HISTORY`, `SEARCH_OPTIMIZATION_HISTORY`, `MATERIALIZED_VIEW_REFRESH_HISTORY`, `REPLICATION_USAGE_HISTORY`, `DATA_TRANSFER_HISTORY`, `PIPE_USAGE_HISTORY`, `SNOWPIPE_STREAMING_*_HISTORY`, `CORTEX_FUNCTIONS_USAGE_HISTORY`, `CORTEX_FUNCTIONS_QUERY_USAGE_HISTORY`, `CORTEX_ANALYST_USAGE_HISTORY`, `CORTEX_SEARCH_*_USAGE_HISTORY`, `DOCUMENT_AI_USAGE_HISTORY`.

*Org-level:* `ORGANIZATION_USAGE.USAGE_IN_CURRENCY_DAILY`, `RATE_SHEET_DAILY`, `CONTRACT_ITEMS`, `REMAINING_BALANCE_DAILY`, `WAREHOUSE_METERING_HISTORY` (org), `STORAGE_DAILY_HISTORY`, `DATA_TRANSFER_DAILY_HISTORY`.

*Workload:* `QUERY_HISTORY`, `ACCESS_HISTORY`, `SESSIONS`, `WAREHOUSE_LOAD_HISTORY`, `WAREHOUSE_EVENTS_HISTORY`.

*Pipelines:* `TASK_HISTORY`, `COPY_HISTORY`, `DYNAMIC_TABLE_REFRESH_HISTORY`, `TASK_VERSIONS`, `PIPES`.

*Storage:* `STORAGE_USAGE`, `DATABASE_STORAGE_USAGE_HISTORY`, `STAGE_STORAGE_USAGE_HISTORY`, `TABLE_STORAGE_METRICS`.

*Security & governance:* `LOGIN_HISTORY`, `USERS`, `ROLES`, `GRANTS_TO_ROLES`, `GRANTS_TO_USERS`, `TAG_REFERENCES`, `POLICY_REFERENCES`, `MASKING_POLICIES`, `ROW_ACCESS_POLICIES`, `OBJECT_DEPENDENCIES`.

*Catalog:* `DATABASES`, `SCHEMATA`, `TABLES`, `VIEWS`, `COLUMNS`, `WAREHOUSES` (or `SHOW WAREHOUSES` snapshot).

*Fast-path (LIVE only):* `INFORMATION_SCHEMA.QUERY_HISTORY*`, `WAREHOUSE_LOAD_HISTORY` table functions — used only for the few conditions needing sub-hour detection; flag clearly as real-time-but-short-retention.

*Application telemetry (LIVE only, optional):* configured event table.

### 7.2 Mode A — LIVE connection

**Connection management**
- Auth methods: **key-pair (RSA, recommended and default)**, OAuth (external IdP), PAT, and `externalbrowser` (local dev only). Password auth exists but must be marked *discouraged* in the UI with a warning.
- Private keys and secrets never touch Postgres in plaintext: use the `secrets` adapter (AWS Secrets Manager / local encrypted keyring / env for dev). Store only a secret *reference* in the DB.
- Support account URL, region, PrivateLink hostnames, proxy config, and an optional `warehouse`, `role`, `session parameters` block.
- Connection pool with per-tenant isolation; enforce `STATEMENT_TIMEOUT_IN_SECONDS`, `QUERY_TAG = 'SNOWOBS:{tenant}:{surface}:{trace_id}'` on every session — the tool must be attributable in the customer's own telemetry.

**Capability probe (run on connect, cache 24 h, re-run on demand).** For every registered source: can we `SELECT 1 ... LIMIT 1`? What is `MAX(time_column)`? Row count over the retention window? Edition/feature gates present? Produce a **Coverage & Grants Report** listing, per source: accessible / missing grant / not applicable to edition / empty — each with copy-pastable remediation SQL generated from `snowflake/provisioning/`.

**Query execution**
- All dashboard/agent queries are compiled from the semantic layer and pushed down. Result sets returned to the app are **aggregates**, capped by `max_rows_returned` (default 50 000) and `max_bytes_scanned_warning`.
- Every query is wrapped with a cost estimate and recorded in the app's own `query_log` so Snowflake Observability & FinOps Platform can report *its own* run cost (HLD NFR: run cost < 2% of platform spend). Surface this as a first-class KPI: **platform self-cost**.
- Warehouse selection: configurable, default XSMALL with `AUTO_SUSPEND=60`; the provisioning wizard can create `WH_SNOWOBS_APP` with a resource monitor.

**Optional materialisation (opt-in).** For large accounts, offer "Accelerated mode": deploy the generated dbt project into the customer's `OBSERVABILITY` database so dashboards read pre-aggregated tables instead of raw views. This must be an explicit choice with a cost estimate, and the app must work fully without it.

### 7.3 Mode B — OFFLINE upload

**Getting the data out of Snowflake.** The app generates a tailored extract kit (`packages/ingest/export_script_gen.py`), downloadable from the UI:
1. `01_extract.sql` — one `COPY INTO @~/snowobs/<view>/ FROM (SELECT ... WHERE <time_col> >= DATEADD(day,-N,CURRENT_DATE))` per source, with `HEADER=TRUE FILE_FORMAT=(TYPE=CSV COMPRESSION=GZIP FIELD_OPTIONALLY_ENCLOSED_BY='"' NULL_IF=('') EMPTY_FIELD_AS_NULL=TRUE)`. Also emit a Parquet variant (preferred — it preserves types).
2. `02_download.sh` / `.ps1` — Snowflake CLI (`snow`) `GET` commands.
3. `03_manifest.json` — expected files, window, and a checksum placeholder, so the app can validate completeness on upload.
4. A Snowsight-only fallback path (per-view result download) for environments where stages/CLI are blocked, with explicit row-limit warnings.

**Upload UX**
- Drag-and-drop of a folder, a `.zip`, or individual files; chunked resumable uploads; progress per file; total size limit configurable (default 10 GB).
- Accept `.csv`, `.csv.gz`, `.parquet`, `.tsv`, `.json` (NDJSON).

**Ingestion pipeline (worker)**
1. **Profile** — sniff delimiter, encoding (handle UTF-8 BOM and UTF-16), quoting, compression; read header + 1 000-row sample; infer types.
2. **Identify** — match to a registered source by (a) filename alias, (b) header signature Jaccard similarity ≥ 0.7, (c) LLM-assisted fallback with the candidate shortlist and a confidence score. Ambiguous or low-confidence matches go to a **human confirmation queue** in the UI — never guess silently on a cost-bearing source.
3. **Map & coerce** — case-insensitive column matching; Snowflake timestamp parsing (`TIMESTAMP_LTZ`/`NTZ`/`TZ` string forms, epoch variants); `VARIANT`/`OBJECT`/`ARRAY` columns arrive as JSON text → keep as `JSON`/`VARCHAR` and provide a `json_extract` shim (§8.4); numeric precision preserved via `DECIMAL`, never `FLOAT`, for credits and currency.
4. **Validate** — required columns present; grain uniqueness; time range and gaps; row counts vs. manifest; nulls in required fields → quarantine rows to `_rejects` with a reason and expose them in the UI. Emit a per-file **Data Quality Report**.
5. **Land** — write partitioned Parquet to object storage (`{tenant}/{source_id}/{date}/part-*.parquet`), register in a DuckDB catalog view. Deduplicate on grain across overlapping uploads (last-write-wins on ingest timestamp).
6. **Drift** — new columns are absorbed additively (recorded in a drift log and surfaced in the UI); missing optional columns are back-filled with registry defaults; missing **required** columns disable the dependent metrics with an explanation.
7. **Warm** — build the same curated entities as LIVE mode and pre-aggregate the dashboard tiles.

**Incremental uploads.** A second upload for a later window must merge, not replace. Track `dataset_versions` with window bounds and lineage so the UI can show "data covers 2026-05-01 → 2026-08-20 across 3 uploads".

### 7.4 Coverage matrix (both modes)

A first-class UI page and API resource. For every registered source: status, row count, min/max timestamp, freshness vs. documented latency, and the count of KPIs it enables. For every KPI: enabled / degraded / unavailable, and which missing source is the blocker. This page is what makes R3 real, and it is the single most useful screen in the product for an assessor.

### 7.5 Synthetic data generator (build this early — Phase 1)

`fixtures/generator/` produces a realistic, deterministic (seeded) fake account so the entire app can be developed, demoed, and tested with no Snowflake account.

- Configurable scale: `--warehouses 12 --teams 8 --days 120 --queries-per-day 50000 --scale small|medium|large`.
- Must generate **schema-faithful** outputs for every registered source (same column names, types, and value domains as real `ACCOUNT_USAGE`).
- Must contain **plantable phenomena** the app is supposed to detect, each tagged in a ground-truth file so tests can assert detection:
  - a warehouse persistently over-sized (low utilisation, no queueing),
  - one with sustained queueing and multi-cluster saturation,
  - a query fingerprint that regresses on day 60 (pruning collapse) and becomes the top cost offender,
  - remote spill on a specific ELT job,
  - a 4× spend spike on a single day attributable to one team + one warehouse,
  - a task graph with a root failure fanning out to 12 downstream failures,
  - a dynamic table missing its `TARGET_LAG` for 3 days,
  - 18% untagged spend concentrated in two warehouses,
  - a dormant-user cohort and a privilege-drift event (new `ACCOUNTADMIN`-adjacent grant),
  - storage growth from an un-dropped clone and excessive Time Travel in non-prod,
  - Cortex/AI spend appearing in week 10 and growing.
- Emit as both (a) CSV/Parquet files loadable through the normal upload path — *the generator must exercise the real ingestion pipeline, not a back door* — and (b) a DuckDB file for fast test setup.
- Ship a `make demo` target: generate → upload → warm → open browser at a fully populated app.

---

## 8. Transformation and modelling layer

### 8.1 Medallion shape (logical, engine-agnostic)

| Layer | LIVE mode | OFFLINE mode |
|---|---|---|
| **Raw** | `SNOWFLAKE.ACCOUNT_USAGE.*` read in place (or landed tables if Accelerated mode) | Parquet in object store, registered as DuckDB views |
| **Curated** | Compiled SQL views/CTEs (or dbt models in Accelerated mode) | DuckDB views / materialised tables |
| **Published** | KPI views compiled from the metric layer | Identical, compiled to DuckDB dialect |

### 8.2 Curated star schema

Implement exactly the HLD model, defined in `packages/semantics/entities/`:

**Facts** — `FACT_QUERY_EXECUTION` (1 row/query), `FACT_WAREHOUSE_METERING_HOURLY` (warehouse × hour, with `credits_compute`, `credits_cloud_services`, `credits_attributed`, `credits_idle`), `FACT_COST_DAILY` (team × service_type × account × day), `FACT_PIPELINE_RUN` (task/pipe/DT refresh), `FACT_STORAGE_DAILY` (database × day + monthly table snapshot), `FACT_ACCESS_DAILY` (user × object × day), `FACT_AI_USAGE_DAILY` (model × function × day), `FACT_LOGIN` (login attempt).

**Dimensions** — `DIM_WAREHOUSE` (SCD2 over size/auto-suspend/clusters/owner tag), `DIM_USER`, `DIM_ROLE`, `DIM_TEAM`, `DIM_DATABASE_SCHEMA`, `DIM_TAG`, `DIM_DATE`, `DIM_QUERY_FINGERPRINT`, `DIM_SERVICE_TYPE`.

Grains are chosen to match the questions asked, not the sources. Every entity YAML declares: grain, source(s), join keys, SCD behaviour, and the metrics it supports.

### 8.3 The semantic compiler (`packages/semantics/compiler/`)

Pipeline: **YAML → validated IR (Pydantic) → SQLGlot expression tree → dialect SQL**.

- Input: a `MetricRequest` — metric ids, dimensions, filters, time grain, time range, limit, order.
- Output: `CompiledQuery{sql, dialect, sources_used, estimated_rows, cache_key, fingerprint}`.
- The compiler resolves the join graph automatically from entity relationships (declared, never inferred at runtime), applies row-level security predicates, injects the time filter on the correct partitioning column, and applies `LIMIT`.
- **Fan-out safety:** when a request mixes metrics from facts at different grains, the compiler must produce independent aggregate CTEs joined on shared dimensions — never a naive multi-fact join. Test this explicitly; it is the classic source of silently doubled cost figures.
- Compilation is pure and deterministic: same request → byte-identical SQL. Snapshot-test the compiled SQL for every metric in both dialects (`tests/golden/sql/{metric}.{dialect}.sql`).

### 8.4 Dialect shims

Known divergences to handle in `dialect_shims.py`, each with a parity test:

| Concern | Snowflake | DuckDB |
|---|---|---|
| Timestamps | `TIMESTAMP_LTZ`, `CONVERT_TIMEZONE` | `TIMESTAMPTZ`, `AT TIME ZONE` |
| Date math | `DATEADD/DATEDIFF/DATE_TRUNC` | `date_add`/`date_diff`/`date_trunc` |
| Semi-structured | `col:path::type`, `FLATTEN` | `json_extract_string`, `unnest` |
| Percentiles | `APPROX_PERCENTILE` | `quantile_cont` (exact) — parity tolerance documented |
| Safe divide | `DIV0`, `DIV0NULL` | `CASE WHEN d=0 THEN 0 ELSE n/d END` |
| Regex | `RLIKE`, `REGEXP_SUBSTR` | `regexp_matches`, `regexp_extract` |
| Hashing | `HASH`, `MD5` | `hash`, `md5` |
| Numeric | `NUMBER(38,9)` for credits | `DECIMAL(38,9)` — never `DOUBLE` |
| `QUALIFY` | native | native (supported) — verify version |
| Window frames | identical | identical |

**Rule:** a shim is a translation of one construct. If you find yourself writing engine-specific *business logic*, stop — that is an R1 violation.

### 8.5 Emitting a dbt project

`packages/artifacts/dbt_emitter.py` renders the curated + published layers as a real dbt Core project (staging → intermediate → marts), with `sources.yml`, tests (`not_null`, `unique`, `accepted_values`, custom freshness and reconciliation tests), `dbt_project.yml`, hourly/daily selectors, and schema-drift-tolerant configs (`on_schema_change: append_new_columns`). Lineage columns (`_loaded_at`, `_source_view`, `_batch_id`) on every model.

This artifact is a headline deliverable: an OFFLINE assessment ends with a deployable dbt project the client can run in their own Snowflake account.

---

## 9. Metric / KPI layer

### 9.1 Metric definition schema

```yaml
# packages/semantics/metrics/cost.yaml
- id: cost.total_credits
  name: Total credits consumed
  domain: cost
  entity: fact_cost_daily
  expression: SUM(credits)
  format: {type: number, decimals: 1, unit: credits}
  grain: day
  dimensions: [team, service_type, warehouse, environment, account]
  synonyms: [credits, credit consumption, compute credits]
  description: >
    Total Snowflake credits consumed, from METERING_DAILY_HISTORY. Includes
    compute, cloud services (net of the daily 10% adjustment), and serverless.
  requires_sources: [metering_daily_history]
  latency_floor_minutes: 180
  direction: lower_is_better
  thresholds: {warn: null, critical: null}   # set per-tenant
  owner: finops
  verified_queries:
    - "total credits last 30 days"
    - "credits by team this month"
```

Every metric must declare `requires_sources` — this is what powers the coverage matrix and R3.

### 9.2 KPI catalogue (implement all; ~90 across 9 domains)

Produce `docs/KPI_CATALOG.md` generated from the YAML, not hand-written.

**D1 — Cost & spend (15).** Total credits (day/MTD/13-month trend); spend in currency; spend by service type (compute / cloud services / serverless / storage / data transfer / AI); spend by warehouse; spend by team (allocated); spend by environment; spend by database; cloud-services ratio vs. the 10% free allowance; serverless spend share; data-transfer/egress spend; day-over-day and week-over-week delta; spend concentration (top-5 share); cost per query; cost per TB scanned; Snowflake Observability & FinOps Platform self-cost.

**D2 — Warehouse & compute efficiency (12).** Utilisation % (credits consumed vs. credits if fully busy while running); idle credits and idle %; auto-suspend gap (seconds between last query and suspension); auto-suspend compliance vs. policy (≤60 s ELT / ≤300 s BI); average running vs. queued queries; queue overload time %; queue provisioning time %; multi-cluster scaling events; max-cluster saturation %; warehouse right-size score (composite red/amber/green with evidence); zombie warehouses (no queries in 30 days); consolidation candidates (compatible schedules, low utilisation).

**D3 — Query & workload performance (12).** Query volume; success/failure rate by error class; p50/p95/p99 elapsed time; compilation time share; queue time share; bytes scanned; **pruning efficiency** (partitions scanned ÷ partitions total); full-scan query count; local spill bytes; remote spill bytes (fire alarm); result-cache hit rate; offender fingerprints ranked by total cost; fingerprint regression detection (cost/latency vs. trailing baseline).

**D4 — Storage & data lifecycle (8).** Active bytes; Time-Travel bytes; Fail-safe bytes; clone bytes; storage growth rate by database and by table; stale tables (no writes in 90 days); Time-Travel policy compliance by environment; top tables by storage cost.

**D5 — Pipeline & orchestration reliability (10).** Task success rate (24 h / 7 d); task duration trend and drift; root-failure identification (one alert per dependency-graph root, not per leaf); repeat-failure tasks; Snowpipe files loaded/failed; pipe file-to-table latency; copy error rate; dynamic table actual lag vs. `TARGET_LAG`; DT refresh failures; stream staleness / unconsumed-past-retention flags.

**D6 — Data quality & freshness (7).** Per-table minutes since last successful update vs. an SLA registry; freshness SLA attainment %; SLA breach count and duration; DQ test pass rate (from the standard DQ landing table); quarantined row count; schema drift events; observability pipeline's own freshness (*the watcher's watch*).

**D7 — Security, access & governance (10).** Failed-login rate and per-user z-scored spikes; logins from new client fingerprints; single-factor / legacy auth count; sensitive-object read counts by user (from `ACCESS_HISTORY`); column-level sensitive access; privilege drift (daily diff of the grant graph); new `ACCOUNTADMIN`-adjacent grants; dormant users (no login 90 d); dormant roles; masking/row-access policy coverage on classified objects.

**D8 — AI / Cortex & advanced features (7).** Cortex credits by function and model; tokens by role/user; Cortex Analyst request volume and latency; Cortex Search serving credits; AI cost per business outcome (configurable denominator); AI spend share of total; runaway-token detection per role.

**D9 — Chargeback, budget & commitment (9).** Allocated cost by team (direct / idle share / cloud-services share / storage / serverless / AI); unattributed spend % (ranked leaderboard); allocation method mix; reconciliation variance vs. metered bill (must be ≤0.5%); budget variance MTD and projected month-end; forecast and forecast MAPE; commitment (capacity) utilisation %; remaining balance burn-down and projected exhaustion date; dispute count and ageing at close.

### 9.3 Metric quality rules

- Every cost metric carries an `allocation_method` and a `provisional` flag (true while inside the source's restatement window).
- Every metric with a `direction` supports trend arrows and threshold badges.
- Ratios use safe division and render "n/a" for a zero denominator — never 0%.
- Currency conversion uses `RATE_SHEET_DAILY` where available; otherwise a configured credit price with a visible "estimated" badge.

---

## 10. Cost attribution & chargeback engine

Implement exactly the HLD model, configured in `packages/semantics/allocation/`.

### 10.1 Allocation waterfall (first match wins, configurable order)

1. Query tag team (parse `QUERY_TAG` JSON; configurable JSON path)
2. Warehouse `OWNER_TEAM` object tag
3. Role → team registry
4. User → team registry (HR feed / CSV upload / OIDC group claim)
5. `UNATTRIBUTED` (public, ranked, target < 5%)

Rules are editable in the UI (drag to reorder, add a custom rule with a predicate), versioned, and every change is an audited event. Re-running a closed period requires an explicit "restate" action.

### 10.2 Three-component compute cost

For each warehouse-day:
- **Direct** — the query's own attributed credits from `QUERY_ATTRIBUTION_HISTORY`.
- **Idle share** — `metered_credits − attributed_credits`, spread across teams active on that warehouse in the trailing 24 h, pro-rata to their direct usage. *If you did not use it, you pay none of its idle.*
- **Cloud-services share** — account cloud-services credits net of the daily 10% offset, spread pro-rata to compute.

Storage allocates by database owner tag; serverless and AI by the owning object's tag.

Implement the HLD's worked example as a **unit test fixture** (`PRD_SHARED_BI_WH`, 40 credits, Marketing 18 / Finance 9 / Ops 3 direct, 10 idle, $6 cloud services → Marketing $75.60, total $156). If that test fails, the engine is wrong.

### 10.3 Reconciliation gate

Daily job asserts `SUM(allocated) BETWEEN metered × 0.995 AND metered × 1.005`. On failure: block publication of chargeback figures, raise a P2, show a banner with the variance decomposition (which warehouse-days drift and by how much). Store every reconciliation run with inputs and outcome — this is the artifact finance will ask for.

### 10.4 Close workflow

Showback mode (default months 1–3) → chargeback mode. Monthly close on a configurable business day (default BD3): freeze the period, run reconciliation, publish, open a 5-day dispute window, track disputes to resolution, produce a signed-off close pack (PDF/XLSX) with the allocation summary, method mix, unattributed leaderboard, and dispute register.

---

## 11. Analytics engines

### 11.1 Forecasting (`packages/analytics/forecast.py`)

Transparent and explainable — finance must be able to follow it:
- Decomposition: trend (robust linear or Theta) + day-of-week seasonality + day-of-month effects + holiday/blackout calendar (configurable).
- Fit on `USAGE_IN_CURRENCY_DAILY` (or credits where currency is unavailable), respecting the restatement window.
- Outputs: month-end landing point with an 80% interval, budget variance, and a **component breakdown chart** showing trend vs. seasonality vs. residual.
- Track rolling MAPE as a first-class KPI (target < 8% by month 6). Store every forecast version so accuracy can be evaluated honestly against what was knowable at the time.
- Commitment posture: projected consumption vs. remaining balance → projected exhaustion or stranding date.

### 11.2 Anomaly detection (`anomaly.py`)

Per the HLD: **thresholds first, models second.** Statistical scoring is applied only where seasonality genuinely defeats static thresholds — daily spend, and optionally query-fingerprint cost. Use seasonal-decomposition residual z-score plus a robust MAD approach; require both magnitude and persistence to fire. Every anomaly carries an automatic **decomposition**: which dimension combination contributes most of the delta (greedy contribution search across team × warehouse × service type × fingerprint).

### 11.3 Right-sizing and optimisation levers (`rightsizing.py`, `levers.py`)

Implement each HLD lever as a simulatable model with inputs, modelled saving, confidence, risk, and rollback:

| Lever | Model | Typical impact |
|---|---|---|
| Warehouse right-sizing | utilisation + queueing + spill → recommended size delta; simulate credits at new size | 5–15% of compute |
| Auto-suspend tuning | measured suspend gap × credit rate × frequency | 3–8% |
| Idle/zombie elimination | credits on warehouses with no queries in 30 d | 2–5% |
| Multi-cluster policy | scaling events vs. queue time at min_clusters=1 | 2–6% |
| Query optimisation | top-20 fingerprints; pruning/spill/`SELECT *` diagnosis | 5–20% of compute |
| Clustering & search optimisation review | AC/SO credits vs. measured query benefit | 1–4% |
| Storage hygiene | Time-Travel right-sizing, stale tables, orphan clones | 1–3% total |
| Scheduling consolidation | co-schedulable batch jobs onto shared warehouses | 3–8% |
| Result-cache utilisation | identical dashboard refreshes against unchanged data | 1–3% |
| Gen2 / adaptive warehouse evaluation | query-level metering comparison | situational |

Each recommendation produces a **card**: evidence (charts + SQL), modelled $/month, confidence, risk note, the exact change statement, the rollback statement, an owner, and a CAB-ready change record. Accepted recommendations become tracked **savings claims**, verified after a configurable observation window (default 14 days) against actuals — realised vs. claimed savings is itself a KPI. This closes the FinOps loop (Inform → Optimize → Operate) rather than stopping at Inform.

---

## 12. Agentic layer

### 12.1 Runtime

Build a **thin, auditable, in-house tool-use loop** on the Anthropic Messages API (`packages/agents/runtime/`). Do not adopt a heavyweight agent framework: enterprise review requires that every step be inspectable and replayable.

Required runtime features: streaming (SSE) with tool-call events surfaced as "thinking" steps; a full **trace** persisted per turn (messages, tool calls, tool results, latency, tokens, cost); a **token/cost budget** per turn, per user, per day with hard cut-off; retry with backoff; deterministic replay of a trace for debugging; and conversation memory scoped to the session plus an explicit, user-visible pinned context (selected time range, team filter, environment).

### 12.2 Agents

| Agent | Owns | Key tools |
|---|---|---|
| **Supervisor** | Intent routing, planning, multi-agent composition, final synthesis | delegates |
| **Onboarding** | Source identification, column mapping, drift resolution, contract drafting | `profile_file`, `propose_mapping`, `validate_mapping`, `describe_source` |
| **FinOps Analyst** | Spend Q&A, delta explanation, allocation questions, forecast narrative, close support | `query_metric`, `explain_delta`, `reconcile`, `forecast`, `list_allocation_rules` |
| **SRE / Observability** | Pipeline health, freshness, failures, root-cause chains, warehouse behaviour | `query_metric`, `trace_task_graph`, `search_logs`, `query_fingerprint_detail` |
| **Governance** | Access, grants drift, dormancy, tagging coverage, policy coverage | `query_metric`, `diff_grants`, `list_policy_coverage` |
| **Curator (Data Product Mgmt)** | Propose/version/contract/publish data products; generate semantic models and verified queries | `list_entities`, `propose_product`, `draft_contract`, `generate_semantic_model`, `emit_artifacts`, `run_validation_checklist` |
| **Optimisation** | Lever ranking, simulation, change drafting | `simulate_lever`, `rank_recommendations`, `draft_change_record` |

### 12.3 Tools (all agents share this registry)

`query_metric(metrics[], dimensions[], filters, time_range, grain, limit)` — **the primary tool.** Text-to-metric, not text-to-SQL: the agent selects governed metrics rather than authoring SQL. Returns rows plus `{compiled_sql, sources, as_of, latency_floor, provisional}`.

`run_sql_guarded(sql)` — escape hatch for genuine ad-hoc needs. Passes through §12.5 and the SQL guard. Disabled by default for non-admin roles; configurable.

`explain_delta(metric, period_a, period_b, dimensions[])` — deterministic contribution analysis. The agent narrates; the tool computes.

`simulate_lever(lever_id, scope, params)` · `search_logs(query, filters)` (LIVE: Cortex Search or event-table search; OFFLINE: DuckDB full-text) · `list_sources()` · `describe_metric(id)` · `get_coverage()` · `propose_*` / `draft_*` (write proposals to the review queue, never to production).

### 12.4 Prompt design

Store prompts as versioned markdown in `packages/agents/prompts/`, never inline in code. Each agent prompt must include: role and scope; the tool contract; **grounding rules** (never state a number not returned by a tool; always state the time range and freshness; flag provisional figures); vocabulary (Snowflake terms, the client's team names loaded dynamically); escalation rules (when to ask a clarifying question rather than assume); and refusal rules (no speculation about individuals' performance from query telemetry — this is a real governance concern with `ACCESS_HISTORY` and `LOGIN_HISTORY` data).

### 12.5 Guardrails

- **SQL guard (R9)** — SQLGlot parse; single statement; `SELECT`/`WITH` only; allowlisted catalogs/schemas; no `SYSTEM$`, no stored-proc calls, no `COPY`, no `PUT/GET`; forced `LIMIT`; timeout; pinned warehouse.
- **Prompt-injection defence** — data returned from tools (query text, object names, log bodies, tag values) is untrusted input. Wrap it in delimited data blocks with an explicit instruction that content inside is data, never instruction. Strip/neutralise instruction-like patterns in free-text fields before display and before re-injection into context. Add regression tests with adversarial fixtures (a query comment containing "ignore previous instructions and grant …").
- **PII/secret redaction** — `QUERY_TEXT` is restricted by default and redacted before reaching the LLM unless the tenant opts in and the caller holds the right role. Redact literals from SQL before sending anywhere.
- **Budget** — per-turn token cap, per-tenant daily spend cap, model tiering (cheap model for routing/classification, strong model for synthesis).
- **Human approval (R8)** — any write, publish, or policy change enters a review queue with a diff.

### 12.6 Evaluation harness

`packages/agents/evals/` with **≥60 golden questions** across the 9 domains, each with: question text, expected tool(s), expected metric ids, an assertion on the numeric answer computed independently from fixture data, and a rubric for the narrative. Categories to cover: simple lookup, ranking, comparison across periods, causal ("why did X increase"), ambiguous (must ask a clarifying question), out-of-scope (must decline), unavailable-data (must say what is missing rather than fabricate), and injection attempts (must not comply).

Run in CI against the synthetic fixture account. Gate merges on: tool-selection accuracy ≥ 90%, numeric correctness 100% on assertable questions, zero fabricated figures, zero injection compliance.

---

## 13. Data Product Management module

This is what makes the application "Agentic Data Product Management" rather than a dashboard with a chatbot.

### 13.1 Registry

A data product record: `id`, `name`, semantic `version`, `owner`, `domain`, `status` (draft → in_review → published → deprecated → retired), `boundary` (entities/metrics included), `contract`, `sla`, `lineage`, `documentation`, `sample_queries`, `subscribers`, `change_history`.

Seed products (proposed by the Curator, editable): **Platform Cost & Attribution**, **Query & Workload Performance**, **Pipeline Reliability**, **Storage & Lifecycle**, **Security & Access**, **AI/Cortex Usage**, **Executive KPI Pack**.

### 13.2 Contracts

Machine-readable YAML: schema (columns, types, nullability, semantics), grain, freshness SLA, availability SLA, retention, quality rules, breaking-change policy, deprecation notice period, support channel. Contract violations are detected continuously (schema drift, freshness breach, quality failure) and raise alerts against the product owner. Publish `docs/DATA_CONTRACTS.md` generated from these.

### 13.3 Lifecycle and versioning

Semver with an enforced rule: removing or retyping a contracted column is a **major** version and requires a deprecation window and a migration note. The Curator drafts release notes from the diff.

### 13.4 Publication targets

**LIVE mode (opt-in, human-approved):** deploy into the customer's Snowflake account, following the `snowflake-observability-data-product` skill —
`V_`-prefixed **secure views** in a `PUBLISHED` schema (masking + row-access policies applied), a **semantic view** with dimensions, metrics, synonyms and 10–20 **verified queries**, a **Cortex Search** service over free-text (query text/log bodies) where appropriate, a **share** scoped to `PUBLISHED` + `SEMANTIC` only (never curated or raw), a **`CREATE LISTING`** with a YAML manifest targeted at `organization` for the internal Marketplace, and a **`CREATE AGENT`** spec wiring Cortex Analyst + Cortex Search + a constrained `sql_exec` tool on a dedicated warehouse with a resource monitor.
Then run the skill's validation checklist and report each item pass/fail in the UI.

**OFFLINE mode:** emit the identical assets as a downloadable bundle — `sql/01_foundations.sql` … `sql/09_grants.sql`, `listing_manifest.yaml`, the dbt project, a `README.md` runbook with the validation checklist and rollback steps, and Autosys JIL / cron scheduling artifacts where orchestration is required. The bundle must be applyable by hand with no dependency on the app.

**Both modes** additionally export: Terraform for the app's own AWS deployment, a Power BI-ready flattened view set, and the Assessment Report (PDF/DOCX/PPTX).

### 13.5 Marketplace / catalogue surface (in-app)

An internal catalogue where consumers browse products, read contracts, see freshness and SLA attainment live, view sample queries, and request access — routed to the owner for approval, with grants applied on approval in LIVE mode.

---

## 14. Alerting and actions

Implement the HLD's four-tier model as configurable rules.

- **Tiers:** P1 business impact now (page + chat, ack 15 min) · P2 degraded/drifting (chat + ticket, same business day) · P3 waste/early warning (team channel, weekly triage) · P4 informational (monthly digest).
- **Rule model:** metric + condition (threshold, delta, anomaly score) + scope + window + persistence + tier + route + **mandatory runbook URL** (rules without one fail validation).
- **Anti-fatigue (required):** a deduplication ledger suppressing re-fires while an alert is open; automatic pruning proposal for any rule with zero actions in 60 days; per-rule fire/action statistics visible in the UI.
- **Channels:** webhook (Teams/Slack), email (SES/SMTP), PagerDuty, ServiceNow/Jira ticket creation. All outbound payloads carry KPI name, value, threshold, scope, runbook link — **never query text**.
- **Guardrail management (LIVE, opt-in, approval-gated):** draft and apply resource monitors, statement timeouts by workload class, auto-suspend policy, and Snowflake budgets. Non-prod may hard-suspend; **prod monitors are notify-only** plus a P1 — never silently kill production.
- **OFFLINE mode:** rules are authored and validated against the uploaded window (backtest: "this rule would have fired 4 times last month") and exported as Snowflake `ALERT` DDL. No live notifications.

---

## 15. API design

REST + SSE, versioned at `/api/v1`, OpenAPI generated, all responses Pydantic-typed.

```
POST   /auth/login, /auth/callback, /auth/logout       GET  /auth/me
GET    /tenants                                        POST /tenants
POST   /connections            test | probe            GET  /connections/{id}/coverage
POST   /uploads                (chunked)               GET  /uploads/{id}/status
POST   /uploads/{id}/mappings/confirm                  GET  /uploads/{id}/quality
GET    /datasets                                       GET  /datasets/coverage
POST   /metrics/query          (MetricRequest)         GET  /metrics/catalog
GET    /metrics/{id}                                   POST /metrics/explain-delta
GET    /dashboards/{slug}                              GET  /dashboards/{slug}/tiles
POST   /agents/chat            (SSE stream)            GET  /agents/traces/{id}
GET    /recommendations                                POST /recommendations/{id}/accept
GET    /savings-claims                                 POST /savings-claims/{id}/verify
GET    /chargeback/periods     POST /chargeback/close  POST /chargeback/disputes
GET    /chargeback/reconciliation/{date}
GET    /forecast                                       GET  /anomalies
GET    /products               POST /products          POST /products/{id}/publish
GET    /products/{id}/contract POST /products/{id}/versions
GET    /alerts/rules           POST /alerts/rules      POST /alerts/rules/{id}/backtest
POST   /exports/{kind}         (dbt|ddl|listing|report|terraform|jil)
GET    /audit                                          GET  /healthz /readyz /metrics
```

Cross-cutting: idempotency keys on mutating calls; ETag/`Cache-Control` on metric responses; RFC 7807 problem+json errors; every response carrying `as_of`, `latency_floor_minutes`, `provisional`, and `sources[]` for any figure.

---

## 16. Frontend

### 16.1 Pages

1. **Onboarding wizard** — choose mode → connect or upload → coverage matrix → done. Must be genuinely 5 minutes.
2. **Executive cost dashboard** — MTD vs. budget with forecast landing point; 13-month trend; cost by team (allocated, reconciled); cost by service type; unit-cost trends; top 5 optimisation opportunities with $ sizes; unattributed %; commitment utilisation.
3. **Platform health** — freshness SLA attainment; 24 h pipeline success; root-failure list; warehouse queue heatmap; DT lag vs. target; open P1/P2s; the observability pipeline's own freshness.
4. **Team chargeback** (row-secured) — cost by component (direct / idle / overhead / storage / serverless / AI); trend vs. budget; top-10 costliest queries and fingerprints; untagged %; dispute window countdown.
5. **Engineering deep-dive** — offender fingerprints by total cost; pruning-efficiency worst list; spill leaderboard; the queue-vs-utilisation **right-sizing quadrant scatter**; repeat-failure tasks; storage growth by table.
6. **Security & access** (role-gated) — failed-login spikes; sensitive-object reads; privilege drift diff; dormant identities; policy coverage.
7. **Optimisation workbench** — ranked recommendations, simulation, accept/reject, savings-claim tracking.
8. **Data Product Studio** — registry, contract editor, semantic-model preview, publish wizard, validation checklist, catalogue.
9. **Agent console** — chat with streamed tool traces, pinned filters, "show the SQL", export answer to report.
10. **Coverage & sources** — the R3 page.
11. **Admin** — connections, allocation rules, budgets, alert rules, RBAC, LLM settings, cost caps, audit log.

### 16.2 Interaction requirements

- Global time-range and environment/team filters that persist across pages and into the agent's pinned context.
- Every chart supports drill-through to the underlying rows (capped) and "show the SQL".
- A **freshness banner** on every page derived from the slowest contributing source (R7), and a **provisional** badge on figures inside a restatement window.
- Skeleton loaders, empty states that explain *why* (not just "no data"), and error states with the remediation action.
- Keyboard-navigable, WCAG 2.1 AA contrast, no colour-only encoding of status.
- Export any view to PNG/CSV/XLSX; export a whole dashboard to the Assessment Report.

### 16.3 Visual design

Read `/mnt/skills/public/frontend-design/SKILL.md` before building UI. Branding is configuration (`config/branding.yaml`), defaulting to the Capgemini palette: navy `#12446E`, primary `#0070AD`, sky `#12ABDB`, coral `#E94B89`, with neutral greys for chrome. Semantic status colours are separate from brand colours (green/amber/red must not be brand-tinted into ambiguity). Typography: a clean sans for UI, tabular numerals for all figures, monospace for SQL. Density: analytical, not marketing — this is a tool people use for hours.

---

## 17. Security, identity, and audit

- **AuthN:** OIDC (Entra ID, Okta, Cognito) with PKCE; local dev fallback with seeded users; no password auth in production builds.
- **AuthZ:** roles `platform_admin`, `finops_analyst`, `engineer`, `team_viewer`, `security`, `product_owner`, `read_only`. Enforce at the API layer *and* in the semantic compiler (row-level predicates injected server-side — never filter in the browser).
- **Multi-tenancy:** tenant id on every row of app metadata and every object-storage prefix; enforced via a session-scoped filter that cannot be bypassed by a route handler. Test cross-tenant isolation explicitly.
- **Secrets:** `secrets` adapter (AWS Secrets Manager / SSM, local encrypted file). Snowflake private keys, LLM API keys, and webhook URLs never in Postgres, never in logs, never in agent context.
- **Data protection:** TLS everywhere; KMS encryption at rest for S3 and RDS; uploaded files scanned (size/type/zip-bomb), encrypted, and purged on a configurable TTL; `QUERY_TEXT` restricted and redacted per §12.5.
- **Audit log:** append-only, tamper-evident (hash chain), covering auth events, connection changes, allocation-rule changes, publications, approvals, agent turns, exported artifacts, and every guarded SQL statement executed. Exportable for the client's own SIEM.
- **Supply chain:** pinned dependencies, `pip-audit`/`npm audit` and Trivy image scanning in CI, SBOM (CycloneDX) published per release, non-root containers, read-only root filesystem, distroless or slim base images.
- Write `docs/SECURITY.md` covering the threat model, data classification (metadata only — no business data rows), and the control mapping.

---

## 18. Observability of the application itself

Structured JSON logs with trace correlation; OpenTelemetry traces across API → engine → Snowflake/DuckDB → LLM; Prometheus-compatible `/metrics` (request latency, query latency by engine, cache hit rate, ingestion throughput, agent turn latency, LLM tokens and cost, alert fire counts); health endpoints distinguishing liveness from readiness (readiness includes engine reachability); and a built-in **self-diagnostics page** showing connection status, last refresh per source, worker queue depth, and the platform's own Snowflake credit consumption. The tool that preaches observability must be observable.

---

## 19. Local deployment

**Target: `git clone && make demo` produces a fully populated running app in under 10 minutes on a laptop, with no Snowflake account and no cloud credentials.**

- `deploy/compose/docker-compose.yml` — services: `api`, `web`, `worker`, `postgres`, `redis`, `minio`, `minio-init`. Healthchecks and dependency ordering on all. Named volumes for persistence.
- `docker-compose.demo.yml` overlay — runs the synthetic generator, uploads through the real ingestion path, warms caches, seeds demo users.
- `Dockerfile.allinone` — a single-container variant (SQLite + local FS + embedded DuckDB, no Redis) for `docker run -p 8080:8080 snowobs:demo`. Useful for client laptops with locked-down Docker.
- `make` targets: `dev` (hot reload, uv + vite), `demo`, `test`, `test-parity`, `lint`, `typecheck`, `eval`, `migrate`, `seed`, `generate-fixtures`, `clean`, `build`, `scan`.
- LLM in local mode: if no API key is configured, agents must degrade to a **deterministic template mode** that still answers metric questions via keyword→metric routing and states that narrative generation is disabled. The demo cannot hard-depend on an API key.
- `.env.example` fully commented, and a first-run doctor command (`snowobs doctor`) that checks ports, Docker resources, and config.

---

## 20. AWS deployment

**Target: `terraform apply` in a fresh account produces a running, private, HTTPS-terminated deployment.**

### 20.1 Reference topology (ECS Fargate — the default)

```
Route 53 ─ ACM ─ ALB (private or internet-facing, WAF optional)
                  │
      ┌───────────┴────────────┐
      ▼                        ▼
  ECS svc: web (nginx/SPA)  ECS svc: api (FastAPI)
                               │
                        ECS svc: worker (arq)
                               │
  ┌──────────┬─────────────────┼──────────────┬───────────────┐
  ▼          ▼                 ▼              ▼               ▼
RDS Postgres  ElastiCache   S3 (uploads,   Secrets Mgr    Bedrock
(Multi-AZ)    Redis         parquet,       + KMS          (Claude)
                            exports)
```

All compute in **private subnets**; NAT or VPC endpoints (S3 gateway, plus interface endpoints for ECR, Secrets Manager, CloudWatch Logs, Bedrock) so the workload can run with no internet egress. Snowflake connectivity via NAT with an allowlist, or **AWS PrivateLink to Snowflake** where the customer has it — make this a module toggle.

### 20.2 Terraform modules (`deploy/terraform/modules/`)

`network` (VPC, subnets, endpoints, security groups) · `data` (RDS with automated backups + encryption, ElastiCache, S3 with versioning/lifecycle/block-public-access) · `compute` (ECS cluster, task definitions, services, autoscaling on CPU + queue depth) · `edge` (ALB, ACM, Route 53, optional WAF + Shield) · `security` (KMS keys, IAM task roles with least privilege, Secrets Manager entries) · `observability` (CloudWatch log groups with retention, alarms, optional ADOT collector, dashboards) · `ci` (ECR repositories, OIDC role for GitHub Actions).

Environments in `envs/dev` and `envs/prod` with remote state (S3 + DynamoDB lock), no hardcoded account ids, and every variable documented. `terraform plan` must be clean and `tflint`/`checkov` must pass in CI.

### 20.3 Sizing and cost

Publish `docs/AWS_COST.md` with three profiles and monthly estimates: **Small** (1×0.5 vCPU api, 1 worker, db.t4g.small, ~$120–180/mo), **Standard** (2×1 vCPU api with autoscaling, db.t4g.medium Multi-AZ, ~$400–600/mo), **Large** (autoscaled, db.m6g.large Multi-AZ, ~$1.2–2k/mo), excluding Bedrock/Anthropic token cost and the customer's Snowflake credits. Verify current pricing rather than guessing.

### 20.4 CI/CD

GitHub Actions: `ci.yml` (lint, typecheck, unit, parity, agent evals, build images, Trivy scan, SBOM) · `release.yml` (tag → push to ECR → `terraform plan` on PR → apply on approval → ECS blue/green with rollback on health-check failure) · `security.yml` (scheduled dependency and image scanning). Use GitHub OIDC to assume an AWS role — no long-lived keys.

### 20.5 Operational readiness

Runbook (`docs/RUNBOOK.md`) covering: deploy, rollback, rotate Snowflake key, rotate LLM key, restore from RDS snapshot, purge a tenant, re-run a failed close, and re-warm caches. Backups: RDS automated + weekly snapshot copy; S3 versioning with lifecycle to IA at 90 days. RPO 24 h / RTO 4 h for the app tier — stated explicitly, and honest that curated data is re-derivable from source (LIVE) or from retained uploads (OFFLINE).

### 20.6 Optional: Snowpark Container Services

Provide `deploy/spcs/` as a third target — the same images running inside the customer's Snowflake account (no egress, credentials never leave). Service spec YAML, compute pool sizing, image repository push instructions, and ingress config. Mark clearly as optional and validate feature availability in the target region before recommending it.

---

## 21. Configuration

Single typed settings object (`pydantic-settings`), sourced from env → file → defaults, validated at startup with a clear error on misconfiguration. Never read `os.environ` outside the settings module.

```
SNOWOBS_MODE=live|offline|auto
SNOWOBS_TENANCY=single|multi
DATABASE_URL=  REDIS_URL=  STORAGE__PROVIDER=s3|minio|local  STORAGE__BUCKET=
SECRETS__PROVIDER=aws|file|env
AUTH__PROVIDER=oidc|local  AUTH__ISSUER=  AUTH__CLIENT_ID=
LLM__PROVIDER=anthropic|bedrock|cortex|none
LLM__MODEL_STRONG=  LLM__MODEL_FAST=  LLM__DAILY_USD_CAP=
SNOWFLAKE__ACCOUNT=  SNOWFLAKE__USER=  SNOWFLAKE__AUTH=keypair|oauth|pat|externalbrowser
SNOWFLAKE__PRIVATE_KEY_REF=  SNOWFLAKE__ROLE=  SNOWFLAKE__WAREHOUSE=
SNOWFLAKE__QUERY_TAG_PREFIX=SNOWOBS  SNOWFLAKE__STATEMENT_TIMEOUT_S=300
FINOPS__CREDIT_PRICE_USD=  FINOPS__RECONCILE_TOLERANCE_PCT=0.5
FINOPS__MODE=showback|chargeback  FINOPS__CLOSE_BUSINESS_DAY=3
GUARDRAILS__MAX_ROWS=50000  GUARDRAILS__ALLOW_ADHOC_SQL=false
```

Verify current model identifier strings before pinning `LLM__MODEL_*` defaults (§25).

---

## 22. Testing and quality

### 22.1 Layers

- **Unit** — compiler, allocation engine, forecasting, ingestion mapping, SQL guard, shims. Target ≥85% coverage on `packages/`.
- **Parity (the critical suite)** — see §22.2.
- **Contract** — API responses validated against the OpenAPI schema; frontend types generated from it so drift breaks the build.
- **Integration** — full ingestion of generated fixtures through the real upload path; LIVE-mode tests against a Snowflake trial account behind an opt-in marker (`pytest -m snowflake`), skipped by default in CI.
- **Agent evals** — §12.6, gating merges.
- **E2E** — Playwright over the five golden journeys (§3.1).
- **Performance** — see §22.3.
- **Security** — cross-tenant isolation, RBAC matrix, SQL-guard bypass attempts, injection fixtures, upload abuse (zip bomb, 2 GB single line, malformed UTF-16).

### 22.2 Dual-engine parity tests — mandatory

For every metric in the catalogue, on the same fixture data:
1. Load fixtures into a Snowflake test schema **and** into DuckDB from identical Parquet.
2. Compile the metric for both dialects.
3. Execute both, compare results row-for-row.
4. Assert exact equality for counts, sums, and currency/credit figures (`DECIMAL`, not float); assert a documented tolerance only where the shim genuinely differs (e.g., approximate vs. exact percentiles) — and record every such tolerance in `docs/PARITY_EXCEPTIONS.md` with a justification.
5. Where no Snowflake account is available in CI, run the Snowflake-dialect SQL against a snapshot of expected output committed under `tests/golden/`, and run the full live comparison in a nightly job.

A metric without a passing parity test is not shipped. Add `make test-parity` and gate merges on it.

### 22.3 Performance targets

| Surface | Target |
|---|---|
| Dashboard tile (warm cache) | < 300 ms p95 |
| Dashboard tile (cold, OFFLINE, 90 d / 50 M queries) | < 3 s p95 |
| Dashboard tile (cold, LIVE, XSMALL warehouse) | < 8 s p95, with a progress state |
| Upload → dashboards live (10 GB) | < 20 min |
| Agent first token | < 2 s |
| Agent full grounded answer | < 20 s p95 |

Achieve these with pre-aggregation of dashboard tiles, a result cache keyed on `{compiled_sql_fingerprint, dataset_version, rls_context}`, partition pruning on Parquet, and columnar projection — not by silently reducing the time window.

---

## 23. Documentation deliverables

Generated or written, all in `docs/`: `ARCHITECTURE.md` (with C4-ish diagrams as Mermaid), `SECURITY.md`, `RUNBOOK.md`, `DEMO.md`, `KPI_CATALOG.md` (generated), `DATA_CONTRACTS.md` (generated), `ASSUMPTIONS.md`, `PARITY_EXCEPTIONS.md`, `AWS_COST.md`, ADRs, an OpenAPI reference, and a **User Guide** written for a FinOps analyst, not an engineer. Plus in-app contextual help on every KPI (hover → definition, source, latency, allocation method).

---

## 24. Phased build plan

Execute strictly in order. Each phase ends with a commit, updated `CHANGELOG.md`, and a short written status against the exit criteria.

**Phase 0 — Foundations (½ day).** Repo scaffold, `CLAUDE.md`, tooling (uv, ruff, mypy, pytest, vite, eslint), Docker Compose skeleton, CI running lint+typecheck+empty tests, `settings.py`, structured logging, health endpoints, ADR-0001 (monorepo), ADR-0002 (worker choice).
*Exit:* `make dev` starts api + web + postgres + redis + minio; CI green.

**Phase 1 — Source registry, fixtures, offline ingestion (2–3 days).** Source registry YAML for all sources in §7.1; synthetic generator with all plantable phenomena and a ground-truth file; the full offline ingestion pipeline (profile → identify → map → validate → land → drift); DuckDB catalog; coverage matrix API.
*Exit:* generated fixtures upload through the real pipeline; coverage matrix accurate; quality report produced; ingestion tests pass including malformed-input cases.

**Phase 2 — Semantic layer and dual engines (3–4 days).** Entity + metric YAML for domains D1–D3; the compiler; both engine adapters; SQL guard; cache; the parity harness with golden SQL snapshots.
*Exit:* D1–D3 metrics compile and execute in both engines with parity tests green; fan-out safety test passes; `make test-parity` gates CI.

**Phase 3 — Cost attribution, chargeback, dashboards v1 (3–4 days).** Allocation waterfall, three-component compute cost, reconciliation gate (with the HLD worked example as a test), executive + platform-health dashboards, freshness banners, show-the-SQL.
*Exit:* HLD worked example passes to the cent; reconciliation gate blocks on injected drift; two dashboards populated from fixtures.

**Phase 4 — LIVE mode (2–3 days).** Connection management with key-pair auth, capability probe and grants report, provisioning SQL generator (granular database roles), pushdown execution with query tagging and self-cost tracking, optional Accelerated mode.
*Exit:* against a Snowflake trial account, connect → probe → dashboards populate; missing-grant path produces correct remediation SQL; parity between LIVE and OFFLINE on the same exported window.

**Phase 5 — Remaining domains, analytics, alerting (3–4 days).** Metrics D4–D9; forecasting with MAPE tracking; anomaly detection with contribution decomposition; right-sizing and all optimisation levers with simulation; recommendation cards and savings claims; alert rules, tiers, dedup ledger, backtest, channels.
*Exit:* full ~90-KPI catalogue live with parity tests; the generator's planted phenomena are all detected and correctly attributed by the analytics engines (assert against ground truth); alert backtest works.

**Phase 6 — Agentic layer (3–4 days).** Runtime, tools, six specialists + supervisor, prompts, guardrails, injection defences, budgets, traces, the eval harness with ≥60 golden questions, agent console UI with streamed tool traces.
*Exit:* eval thresholds in §12.6 met in CI; injection fixtures all refused; "explain last week's spend increase" produces a grounded, correct narrative on fixture data.

**Phase 7 — Data Product Management (2–3 days).** Registry, contracts, lifecycle/versioning, Curator agent flows, artifact generators (dbt, DDL, semantic view, Cortex Search, share + listing manifest, agent spec, JIL/cron), publish wizard with validation checklist, in-app catalogue, Assessment Report export.
*Exit:* OFFLINE bundle applies cleanly by hand to a Snowflake trial account; LIVE publication passes the skill's validation checklist end-to-end; Assessment Report renders.

**Phase 8 — Deployment, hardening, docs (2–3 days).** Terraform modules and both environments, CI/CD with blue/green, image hardening and SBOM, `docker-compose.demo.yml` and the all-in-one image, performance tuning to §22.3, E2E journeys, full documentation set.
*Exit:* `make demo` under 10 minutes from clean clone; `terraform apply` produces a working private deployment; all five golden journeys pass in Playwright; every §26 item satisfied.

---

## 25. Verify before you code

Snowflake's usage surface and AI object DDL change frequently, and this prompt was written from a point-in-time understanding. Before writing SQL or pinning identifiers, **web-search the current official Snowflake documentation** and record findings in `docs/ASSUMPTIONS.md`:

1. Exact view names, columns, **documented latencies**, and retention for every object in §7.1 — especially `QUERY_ATTRIBUTION_HISTORY`, the Cortex usage views, `ORGANIZATION_USAGE` currency and contract views, and any Snowpipe Streaming / Gen2 warehouse metering views.
2. The **granular `SNOWFLAKE` database roles** currently available (`USAGE_VIEWER`, `GOVERNANCE_VIEWER`, `SECURITY_VIEWER`, `OBJECT_VIEWER`, …) and exactly which views each covers — the provisioning script depends on this being right.
3. Current syntax for `CREATE SEMANTIC VIEW`, verified queries, `CREATE CORTEX SEARCH SERVICE`, `CREATE AGENT`, and `CREATE LISTING` (+ manifest schema), and their edition/region availability.
4. Cloud-services billing rules (the daily adjustment) and how they appear in `METERING_DAILY_HISTORY` vs. `WAREHOUSE_METERING_HISTORY` — the reconciliation gate depends on getting this exactly right.
5. Warehouse generation / adaptive-warehouse metering, if the target account uses it.
6. Current Anthropic model identifier strings for both the direct API and Bedrock, and current Bedrock regional availability.
7. `snowflake-connector-python` current auth options (key-pair rotation, OAuth, PAT) and any deprecations.
8. DuckDB's current support for `QUALIFY`, `DECIMAL(38,x)` arithmetic, and Parquet predicate pushdown behaviour at your target version.

Also read `/mnt/skills/user/snowflake-observability-data-product/SKILL.md` before Phase 7 and follow its phases and pitfalls for anything deployed into Snowflake.

---

## 26. Definition of Done

The build is complete when **all** of the following are true:

- [ ] `git clone && make demo` → fully populated app in < 10 min, no Snowflake account, no cloud credentials, no LLM key required for the deterministic path.
- [ ] `terraform apply` in a clean AWS account → working private deployment; documented rollback tested.
- [ ] LIVE mode: connect with key-pair, probe grants, populate every dashboard; missing grants produce correct remediation SQL.
- [ ] OFFLINE mode: upload a folder of CSV/Parquet extracts, get the same dashboards and the same numbers.
- [ ] **Parity suite green for every metric**, with any tolerance documented in `docs/PARITY_EXCEPTIONS.md`.
- [ ] ~90 KPIs across 9 domains implemented, documented, and each declaring its sources and latency floor.
- [ ] Allocation reconciles within 0.5% on fixture and live data; the HLD worked example passes to the cent; the gate blocks on injected drift.
- [ ] All planted phenomena in the synthetic account are detected and correctly attributed, asserted against ground truth.
- [ ] Agent evals meet the §12.6 thresholds; zero fabricated figures; zero injection compliance.
- [ ] A data product publishes end-to-end in LIVE mode and exports as an applyable bundle in OFFLINE mode.
- [ ] Coverage matrix accurate; every unavailable KPI explains its blocker (R3).
- [ ] Security: cross-tenant isolation tested, RBAC matrix tested, SQL guard bypass attempts fail, secrets never in DB or logs, audit log complete and exportable.
- [ ] Performance targets in §22.3 met on the large fixture profile.
- [ ] `mypy --strict` clean on `packages/`; ruff clean; ≥85% coverage on `packages/`; Trivy high/critical clean; SBOM published.
- [ ] Documentation set complete, including a user guide written for a FinOps analyst.
- [ ] No `TODO`, no stubbed business logic, no dead code, no commented-out blocks.

---

## 27. Explicit anti-requirements

Do **not**:

1. Write separate Snowflake and DuckDB implementations of any business logic (R1).
2. Let an agent compute a number itself, or state any figure not returned by a tool (R12).
3. Grant or request blanket `IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE` (R4).
4. Execute any agent- or user-supplied SQL outside the SQL guard (R9).
5. Copy raw telemetry out of Snowflake by default, or send `QUERY_TEXT` to an LLM without explicit opt-in (R2, §12.5).
6. Apply any change to a customer's Snowflake account without a recorded human approval (R8).
7. Use floating-point types for credits or currency anywhere.
8. Hard-suspend a production warehouse via a resource monitor (§14).
9. Show a figure without its as-of timestamp and latency floor (R5, R7).
10. Ship a metric without a parity test, or an alert rule without a runbook link.
11. Substitute zeros for unknowns, or hide a missing source behind an empty chart (R3).
12. Introduce a heavyweight agent framework that obscures the tool-call trace (§12.1).
13. Store secrets in Postgres, in logs, in the frontend bundle, or in Terraform state as plaintext.
14. Assume Snowflake view names, latencies, or DDL syntax from memory (§25).
15. Stub, mock, or defer any part of the business logic to "a later phase" without recording it in `docs/ASSUMPTIONS.md` and raising it explicitly.

---

## 28. First message to send Claude Code

> Read `docs/BUILD_PROMPT.md` in full, then:
> 1. Write `CLAUDE.md` capturing §2 (principles), §5 (layout), §6 (stack), §26 (Definition of Done), and §27 (anti-requirements).
> 2. Complete the verification checklist in §25 by web-searching current Snowflake documentation, and write `docs/ASSUMPTIONS.md` with what you found, what you could not confirm, and the revisit triggers.
> 3. Write `docs/adr/ADR-0001-monorepo-and-dual-engine.md` and `ADR-0002-worker-runtime.md`.
> 4. Execute **Phase 0** only. Then stop and report status against the Phase 0 exit criteria.
>
> Do not begin Phase 1 until I confirm.
