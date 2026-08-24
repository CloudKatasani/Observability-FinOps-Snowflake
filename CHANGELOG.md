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

### Deferred (recorded, not stubbed)

- `Dockerfile.allinone`, `docker-compose.demo.yml`, `make demo`, Terraform,
  release/security workflows — arrive with their owning phases (see
  `docs/BUILD_PROMPT.md` §24 and `docs/ASSUMPTIONS.md` A-14).
