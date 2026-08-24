# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow semver.

## [Unreleased]

### Added — Phase 0 (Foundations)

- Monorepo scaffold: `uv` workspace (`apps/api`, `apps/worker`, `packages/common`),
  React + Vite + TypeScript SPA (`apps/web`).
- `CLAUDE.md` capturing the non-negotiable principles, repo layout, tech stack,
  Definition of Done, and anti-requirements from `docs/BUILD_PROMPT.md`.
- `docs/ASSUMPTIONS.md` — §25 verification of Snowflake usage views, latencies,
  granular database roles, Cortex DDL, connector auth, DuckDB features, and LLM
  model identifiers against current documentation (2026-08-24).
- ADR-0001 (monorepo + single semantic layer over dual engines), ADR-0002
  (arq worker runtime).
- Typed settings (`snowobs_common.config`, pydantic-settings, §21 schema),
  structured JSON logging with trace correlation (`structlog`), RFC 7807 error
  primitives, white-label branding loader (`config/branding.yaml`).
- FastAPI service with `/healthz` (liveness), `/readyz` (per-component readiness:
  Postgres, Redis), `/api/v1/meta` (version, mode, branding), request-id
  middleware, problem+json error handler.
- arq worker harness with explicit job registry and health check.
- Web status page consuming the health endpoints through a zod-validated client.
- Docker Compose stack (api, web, worker, postgres, redis, minio, minio-init)
  with healthchecks and dependency ordering; per-service Dockerfiles (non-root).
- `make dev / test / lint / typecheck / build`; GitHub Actions CI running
  ruff, mypy (strict on `packages/`), pytest, eslint, tsc, vitest, and the SPA build.

### Added — Phase 1 (Source registry, fixtures, offline ingestion)

- **Source registry** (`packages/semantics/sources/`): 55 Snowflake source
  definitions covering cost/metering, org billing, workload, pipelines, storage,
  security/governance, catalog, and Cortex/AI views. Latencies, retention,
  required database roles, edition gates, grain, watermarks, CSV import rules,
  and sensitivity all declared in YAML — adding a source needs no code change.
  Unverified latencies are flagged (`latency_verified: false`) rather than
  presented as fact.
- **Synthetic generator** (`fixtures/generator/`, `snowobs-generate` CLI):
  deterministic, schema-faithful account with all 14 planted phenomena from
  §7.5 and a machine-readable `ground_truth.json`. Emits CSV/CSV.GZ/Parquet
  loadable through the real upload path. Credits are Decimal throughout, and
  the daily cloud-services 10% adjustment follows the verified rule.
- **Offline ingestion pipeline** (`packages/ingest/`): profile (delimiter,
  encoding incl. UTF-16/BOM, gzip, Parquet, NDJSON) → identify (filename alias
  corroborated by header signature; low-confidence goes to a human confirmation
  queue) → map and coerce (Snowflake timestamp/epoch forms, Decimal credits) →
  validate and quarantine with reasons → land as partitioned Parquet with
  lineage columns → absorb drift additively → register a DuckDB catalog that
  deduplicates on grain (last-write-wins) so re-uploads merge, never double-count.
- **Coverage matrix** (`packages/ingest/coverage.py`): per-source status with
  copy-pastable remediation (a `GRANT DATABASE ROLE` in LIVE mode, an upload
  instruction in OFFLINE), per-metric enabled/degraded/unavailable naming the
  blocking source — R3 made concrete.
- **Extract kit generator**: `01_extract.sql` (read-only COPY INTO per source),
  `02_download.sh`/`.ps1`, manifest, and a runbook README, all generated from
  the registry.
- API: `/api/v1/sources`, `/api/v1/datasets/coverage`, `/api/v1/exports/extract-kit`.
- 87 tests: registry invariants (verified latencies, no blanket privileges),
  generator determinism and every planted phenomenon, ingestion round-trip,
  malformed/abusive input handling, incremental merge, and coverage assessment.

### Added — Phase 2 (Semantic layer, dual engines, SQL guard, parity)

- **Semantic model** (`packages/semantics/`): 6 curated entities (facts and a
  warehouse dimension) and **41 KPIs across domains D1–D3**, declared once in
  YAML. Cross-validated at load: a metric cannot claim a freshness floor faster
  than its slowest source (R7), reference an unregistered source, or slice by a
  dimension its entity cannot reach.
- **Compiler**: `MetricRequest` → validated IR → SQLGlot → dialect SQL. Pure and
  deterministic (byte-identical SQL for the same request). Injects the time
  filter on the entity's own time column, applies row-level security
  server-side (an empty allowlist selects nothing, never everything), escapes
  literals, rejects unsafe identifiers, forces a `LIMIT`, and orders totally so
  tiles do not reshuffle on ties.
- **Fan-out safety**: mixing metrics from facts at different grains produces one
  aggregate CTE per fact joined on shared keys — a test asserts that adding a
  second metric cannot change the first metric's totals.
- **Dialect shims**: nine portable constructs, each rewritten per engine and
  rewritten to a fixed point so nested shims resolve. A guard fails the build if
  SQLGlot ever claims a shim name as a built-in — the failure mode that silently
  bypassed `SAFE_RATIO` and would have returned float money (§27.7).
- **SQL guard** (`packages/sqlguard/`): SQLGlot-parsed, single read-only
  statement, allowlisted relations, forced limit, timeout and warehouse pin.
  38 adversarial tests: stacked statements, comment-hidden payloads, writes
  nested in subqueries, `SYSTEM$`/`GET_DDL` functions, union smuggling, and
  fail-closed defaults.
- **Engines** (`packages/engines/`): the `QueryEngine` protocol, the DuckDB
  engine (guard-enforced, provenance-carrying results), and an RLS-aware result
  cache keyed on `{sql fingerprint, dataset version, RLS context}`.
- **Parity suite** — the critical one. Every metric is executed in both dialect
  renderings against the same fixture data and compared row-set for row-set;
  money matches exactly, with tolerances only for approximate percentiles,
  documented in `docs/PARITY_EXCEPTIONS.md`. Plus 82 golden SQL snapshots
  (41 metrics × 2 dialects). `make test-parity` gates CI.
- **Generated `docs/KPI_CATALOG.md`** (`make catalog`) — from the YAML, never by
  hand.

### Added — Phase 7 (Data product management)

- **Data product registry** (`packages/dataproducts/products/`): four seed
  products declared in YAML — Platform Cost & Attribution, Warehouse & Compute
  Efficiency, Pipeline Reliability, and Security & Access Governance — each
  naming the governed metrics it exposes, its owner, consumers, refresh cadence,
  SLA, classification, and release history. Cross-validated against the semantic
  layer at load: a product that references a metric the platform cannot compute,
  publishes a dimension no entity resolves, promises a freshness its slowest
  source cannot deliver (R7), mixes time grains in one relation, or indexes a
  sensitive column for search does not load at all.
- **Derived data contracts** (`contracts.py`): column-level schema with types,
  nullability, units, and the governed metric behind every measure; grain and
  row-count expectations per relation; a freshness guarantee taken as the
  *maximum* documented source latency, never an average. `validate_against()`
  reports drift when the semantic layer moves under a published contract.
- **Change classification and the version gate**: `diff(old, new)` classifies
  every change breaking or additive and **refuses a version bump too small for
  what changed** — a withdrawn column, a retype, a relaxed nullability, a
  changed grain, a loosened freshness guarantee, or shortened retention cannot
  ship as a patch or a minor (§13.3). Release notes are drafted from the diff.
- **Artifact emitters** (`emitters/`), pure and deterministic, every one pinned
  by a golden file: dbt project (models from the compiled metric SQL with
  `source()` references, `schema.yml`, and generated grain/row-count/freshness
  tests), secure-view DDL, masking and row-access policies, `CREATE SEMANTIC
  VIEW` with the `AI_VERIFIED_QUERIES` clause, `CREATE CORTEX SEARCH SERVICE`,
  `CREATE ORGANIZATION LISTING` with its YAML manifest, and a product-scoped
  `CREATE AGENT` spec. Non-additive measures are published as semantic-view
  facts rather than metrics, so no consumer tool can re-average a ratio into a
  wrong number (R12).
- **Publish workflow** (`publish.py`): draft → proposed → approved → published →
  deprecated → retired, with every transition recording actor, timestamp, and
  reason. Nothing publishes without a recorded approval (R8), and publication
  runs six preflight gates — contract validity, dual-engine compilation,
  freshness achievability, version policy, migration note, and a blanket-grant
  audit of the generated SQL — refusing with the specific failing check. It
  emits a bundle (SQL scripts, listing manifest, contract, dbt project, and a
  runbook with the validation checklist and rollback steps) and **never executes
  DDL against a customer account** (R2).
- **Product API** (`/api/v1/products`): catalogue, product detail with contract
  drift, contract, diff against the previous published version, preflight,
  propose/approve/publish/deprecate transitions, approval history, and bundle
  download. Approvals require a named human and refuse an anonymous request.
- **Generated `docs/DATA_CONTRACTS.md`** (`make contracts`) — from the product
  YAML and the semantic layer, never by hand.
- §25 re-verification of `AI_VERIFIED_QUERIES`, `CREATE ORGANIZATION LISTING`,
  and `CREATE AGENT` syntax recorded in `docs/ASSUMPTIONS.md` §6a, closing most
  of U-4; new assumptions A-18 to A-23.

### Deferred (recorded, not stubbed)

- `Dockerfile.allinone`, `docker-compose.demo.yml`, `make demo`, Terraform,
  release/security workflows — arrive with their owning phases (see
  `docs/BUILD_PROMPT.md` §24 and `docs/ASSUMPTIONS.md` A-14).
