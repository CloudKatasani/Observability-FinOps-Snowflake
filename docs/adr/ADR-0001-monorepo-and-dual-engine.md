# ADR-0001 — Monorepo with a single semantic layer over two execution engines

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** snowobs engineering
- **Related:** BUILD_PROMPT §2 (R1, R10), §4, §5, §8

## Context

The product must run in two operating modes that produce identical numbers from
identical inputs:

- **LIVE** — pushdown SQL against a customer's Snowflake account; the app stores no
  telemetry rows.
- **OFFLINE** — embedded DuckDB over Parquet built from CSV/Parquet extracts of the
  same usage views, for air-gapped assessments.

The delivery surface is broad (FastAPI API, arq worker, React SPA, Terraform,
generated Snowflake artifacts) and heavily interdependent: the semantic YAML feeds
the API, the worker, the dbt emitter, the agent tools, and the docs generators. The
number-one failure mode called out in the build prompt is drift between "the
Snowflake version" and "the DuckDB version" of a metric.

## Decision

1. **One repository (monorepo).** `apps/` (api, worker, web), `packages/` (shared
   Python libraries), `fixtures/`, `deploy/`, `snowflake/`, `docs/` live together and
   version together. A change to a metric YAML, its compiled-SQL snapshots, its parity
   test, its API exposure, and its documentation lands as one reviewable commit.
2. **One semantic layer, two engines (R1).** All metrics, entities, sources, and
   allocation rules are declarative YAML under `packages/semantics/`. A single
   compiler (YAML → validated Pydantic IR → SQLGlot expression tree) renders
   dialect-specific SQL for Snowflake and DuckDB. Engine adapters in
   `packages/engines/` implement a shared `QueryEngine` protocol and execute compiled
   SQL only.
3. **Divergences are shims, not forks.** Constructs that do not transpile cleanly are
   handled in `packages/semantics/dialect_shims.py` — one shim per construct, each
   with a dual-engine parity test. Business logic never branches per engine.
4. **Python workspace via `uv`.** `apps/api`, `apps/worker`, and each
   `packages/*` member are workspace projects with explicit dependency edges, so the
   dependency direction (apps → packages, never the reverse) is enforced by packaging
   rather than convention.

## Consequences

**Positive**
- Parity is testable as a first-class CI gate (`make test-parity`): both dialects are
  compiled from the same IR in the same process, on the same fixtures.
- Atomic cross-cutting changes; no version-skew matrix between services and libraries.
- One CI pipeline can gate lint, typecheck, unit, parity, agent evals, and image builds.

**Negative / accepted costs**
- CI must be path-filtered as the repo grows or pipelines get slow.
- A monorepo requires discipline about package boundaries; `import-linter`-style
  enforcement may be added if violations appear.
- SQLGlot becomes a load-bearing dependency (also required by R9's SQL guard); its
  version is pinned and upgrades require the parity suite to pass.

## Alternatives considered

- **Polyrepo (api / web / semantics / infra).** Rejected: the semantic layer changes
  with every KPI and would need constant cross-repo version choreography; parity
  regressions would surface only at integration time.
- **Two hand-maintained SQL trees (per engine).** Rejected outright — this is the
  exact failure mode R1 exists to prevent; identical-numbers-in-both-modes is the
  core product requirement.
- **dbt as the runtime semantic layer.** Rejected for the app runtime: dashboard and
  agent queries need parameterised, per-request compilation (dimensions, filters,
  RLS predicates, limits) and a guarded ad-hoc path — not a batch model DAG. dbt is
  instead an *output artifact* (`packages/artifacts/dbt_emitter.py`) for customers
  who want materialisation in their own account.
- **A third-party semantic-layer service (Cube, MetricFlow, …).** Rejected: adds a
  server dependency to the air-gapped OFFLINE mode, obscures compiled SQL (conflicts
  with R5 "show the SQL"), and constrains dialect control needed for the shims.
