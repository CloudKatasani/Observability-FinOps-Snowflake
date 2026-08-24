# ADR-0002 — Background worker runtime: arq (Redis-backed)

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** snowobs engineering
- **Related:** BUILD_PROMPT §4, §6, §7.3, §19

## Context

The platform needs a background job tier for: upload ingestion (profile → identify →
map → validate → land → warm), scheduled source refresh, capability probes, the daily
reconciliation gate, monthly close, forecasting, anomaly scans, alert evaluation, and
agent eval runs. The build prompt allows `arq` or Celery and asks for the choice to be
justified here, with a stated preference for `arq`'s lighter footprint. Constraints
that matter:

- The whole stack must run on a laptop (`make demo` < 10 min) and as an all-in-one
  demo container; every extra broker process hurts that target.
- The API is FastAPI (async). Ingestion is I/O-heavy (object storage, DuckDB,
  Snowflake network calls); CPU-heavy steps (Parquet writes via pyarrow/polars,
  DuckDB aggregation) release the GIL in native code.
- Redis 7 is already in the stack as the cache/queue substrate (R10 keeps this
  identical locally and on AWS via ElastiCache).
- Jobs must be observable (OpenTelemetry traces, queue-depth metrics for ECS
  autoscaling) and auditable.

## Decision

Use **arq** as the worker runtime, with Redis as its only broker, and a thin
`packages/common` job contract so enqueueing code depends on job names and Pydantic
payloads rather than arq internals (the `queue` provider adapter required by R10).

Conventions:
- One worker service (`apps/worker`) declaring explicit job functions; no dynamic task
  discovery.
- Cron-style schedules use arq's built-in `cron_jobs` (refresh, reconciliation, close,
  forecast, alert evaluation).
- Job payloads are Pydantic models serialised to JSON; every job carries
  `tenant_id` and a trace context for correlation.
- Retries with exponential backoff are configured per job; jobs are idempotent (keyed
  on dataset version / period) so at-least-once delivery is safe.
- Queue depth is exported as a Prometheus metric; it drives worker autoscaling on ECS.

## Consequences

**Positive**
- Single lightweight dependency; no separate beat scheduler, no RabbitMQ/SQS broker;
  the all-in-one demo image only needs Redis (or arq's polling against a bundled
  Redis) rather than a Celery + beat + broker triad.
- Native asyncio: the same async Snowflake/DuckDB/storage adapters used by the API run
  unchanged in jobs; no sync/async bridging layer.
- Small, readable codebase — auditable by client architects, consistent with the
  "boring, auditable code" working style.

**Negative / accepted costs**
- Smaller ecosystem than Celery: no canvas/chord primitives — multi-step pipelines are
  expressed as explicit orchestration code in `apps/worker` (acceptable: the ingestion
  pipeline is a linear, resumable sequence and we want it explicit and testable).
- CPU-bound steps must not block the event loop: heavy transforms run via
  `run_in_executor`/subprocess where profiling shows blocking (pyarrow/polars/DuckDB
  release the GIL for the hot paths).
- Redis is a hard dependency for the multi-service deployment. The single-container
  demo variant (`Dockerfile.allinone`) runs a bundled Redis instance inside the
  container; application code is unchanged (R10 — configuration, not code branches).

## Alternatives considered

- **Celery.** Mature and feature-rich, but: sync-first (async support still awkward),
  needs a separate beat process for schedules, heavier operational surface, and its
  dynamic task registry is harder to audit. Its advanced primitives are not required.
- **Dramatiq.** Lighter than Celery but still sync-first and adds a second
  serialisation/middleware model; no advantage over arq for an asyncio codebase.
- **In-process background tasks (FastAPI `BackgroundTasks`/asyncio).** Rejected: jobs
  must survive API restarts, be retryable, and scale independently (10 GB ingests
  cannot share the API's lifecycle).
- **SQS + custom consumer.** Rejected: breaks local/air-gapped parity (R10) and adds
  an AWS-only code path.

## Revisit trigger

If a future phase needs true DAG orchestration (fan-out/fan-in with partial retry
beyond the linear ingestion pipeline) or arq maintenance stalls, re-evaluate against
Celery or a workflow engine — recorded here so the decision is revisited consciously
rather than eroded ad hoc.
