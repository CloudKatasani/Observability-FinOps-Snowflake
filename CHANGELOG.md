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

### Deferred (recorded, not stubbed)

- `Dockerfile.allinone`, `docker-compose.demo.yml`, `make demo`, Terraform,
  release/security workflows — arrive with their owning phases (see
  `docs/BUILD_PROMPT.md` §24 and `docs/ASSUMPTIONS.md` A-14).
