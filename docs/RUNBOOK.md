# Runbook

Operating procedures for the Observability & FinOps Platform for Snowflake
(`snowobs`). Every command here is either a `make` target that exists in the
[`Makefile`](../Makefile), a script in [`scripts/`](../scripts), or a standard
`aws` / `terraform` / `docker` invocation.

Companion documents: [`ARCHITECTURE.md`](ARCHITECTURE.md) (what the components
are), [`SECURITY.md`](SECURITY.md) (controls and limitations),
[`AWS_COST.md`](AWS_COST.md) (what the deployment costs),
[`../deploy/terraform/README.md`](../deploy/terraform/README.md) (module layout).

---

## Contents

- [Health and readiness](#health-and-readiness)
- [First deploy](#first-deploy)
- [Routine deploy](#routine-deploy)
- [Rollback](#rollback)
- [Local operation](#local-operation)
- [Alarm procedures](#alarm-procedures) — one entry per alarm
  - [The app is down](#the-app-is-down)
  - [The app is erroring](#the-app-is-erroring)
  - [Dashboards are slow](#dashboards-are-slow)
  - [The worker is not running](#the-worker-is-not-running)
  - [The metadata database is struggling](#the-metadata-database-is-struggling)
- [Application conditions](#application-conditions)
  - [The reconciliation gate is red](#the-reconciliation-gate-is-red)
  - [A source has gone stale](#a-source-has-gone-stale)
  - [Parity fails in CI](#parity-fails-in-ci)
  - [Agent evals fail in CI](#agent-evals-fail-in-ci)
  - [The LLM provider is down](#the-llm-provider-is-down)
- [Credential rotation](#credential-rotation)
- [Backup and restore](#backup-and-restore)
- [Tenant purge](#tenant-purge)
- [Re-running an allocation and re-warming caches](#re-running-an-allocation-and-re-warming-caches)
- [The alert engine](#the-alert-engine)

---

## Health and readiness

| Endpoint | Answers | Touches | Codes |
|---|---|---|---|
| `GET /healthz` | Is the process up? | Nothing | Always `200` with version |
| `GET /readyz` | Can this instance serve traffic? | Postgres (`SELECT 1`) and Redis (`PING`), each with a 2-second timeout, run concurrently | `200` when both are `ok`; `503` with a per-component breakdown otherwise |

The split is deliberate. The container's own `HEALTHCHECK` and the ECS container
health check use `/healthz`, so a task whose database is briefly unreachable is not
killed for it. The ALB target group health-checks `/readyz`, so such a task is
taken out of rotation instead. `deregistration_delay` is 30 seconds and the
service's health-check grace period is 90 (`health_check_grace_period_seconds`),
which covers loading the SPA bundle and the semantic model at import time.

The SPA's **System status** page (`/status`) renders both, naming each unavailable
component and its error type.

Logs are structured JSON (`SNOWOBS_LOG_JSON=true` in every deployed environment)
and carry a `trace_id` bound per request. The same id is echoed in the
`x-request-id` response header, so a user-reported problem can be found directly:

```bash
aws logs filter-log-events \
  --log-group-name "$(terraform -chdir=deploy/terraform/envs/prod output -raw log_group_name)" \
  --filter-pattern '{ $.trace_id = "abc123…" }'
```

---

## First deploy

Prerequisites: an AWS account, a Route 53 zone (or an ACM certificate you manage),
and an S3 bucket plus DynamoDB table for Terraform state — the commands to create
those are in `deploy/terraform/envs/dev/backend.hcl.example`.

```bash
cd deploy/terraform/envs/dev
cp backend.hcl.example backend.hcl            # your state bucket and lock table
cp terraform.tfvars.example terraform.tfvars  # domain, bucket, repo, region, image

terraform init -backend-config=backend.hcl
terraform plan -out=tfplan                    # read it, do not skim it
terraform apply tfplan
```

`terraform apply` alone does **not** produce a working deployment: the secret
containers are created empty on purpose, so no secret value is ever in state. Fill
them in:

```bash
terraform output secret_names            # the names to populate
terraform output database_master_secret_arn

# 1. Compose the application's DATABASE_URL from the RDS-managed master secret.
aws secretsmanager get-secret-value \
  --secret-id "$(terraform output -raw database_master_secret_arn)" \
  --query SecretString --output text          # username, password, host, port

aws secretsmanager put-secret-value \
  --secret-id snowobs-dev/app/database-url \
  --secret-string 'postgresql+asyncpg://snowobs:<password>@<host>:5432/snowobs'

# 2. The Snowflake key pair, if this deployment connects to Snowflake.
aws secretsmanager put-secret-value \
  --secret-id snowobs-dev/snowflake/private-key \
  --secret-string file://snowobs_rsa_key.p8

# 3. The LLM key, only when llm_provider = "anthropic".
aws secretsmanager put-secret-value \
  --secret-id snowobs-dev/llm/api-key --secret-string 'sk-…'
```

Then roll the services so the ECS agent picks the values up:

```bash
aws ecs update-service --cluster "$(terraform output -raw cluster_name)" \
  --service "$(terraform output -raw app_service_name)" --force-new-deployment
aws ecs update-service --cluster "$(terraform output -raw cluster_name)" \
  --service "$(terraform output -raw worker_service_name)" --force-new-deployment
```

**The Snowflake side is a separate apply, by a different person** (R8) — the
customer's own `ACCOUNTADMIN` reviews and applies it:

```bash
cd deploy/terraform/snowflake
export SNOWFLAKE_ORGANIZATION_NAME=… SNOWFLAKE_ACCOUNT_NAME=… \
       SNOWFLAKE_USER=… SNOWFLAKE_ROLE=ACCOUNTADMIN
terraform init
terraform plan          # read every grant before applying
terraform apply
```

Equivalently, run the generated script `snowflake/provisioning/01_reader_role.sql`
by hand. Both come from the same generator (`make provisioning`), so they cannot
disagree. Verify afterwards with the in-app probe:

```bash
curl -s -X POST "$APP_URL/api/v1/connections/probe" -H 'content-type: application/json' \
  -d '{"account":"acme-analytics","user":"SNOWOBS_SVC","role":"SNOWOBS_READER",
       "warehouse":"WH_SNOWOBS_APP","secret_ref":"snowobs-dev/snowflake/private-key"}'
```

The response's `suggested_grants` is the ranked list of statements that would fix
whatever is still blocked.

Finally, confirm the deployment answers:

```bash
curl -fsS "$APP_URL/healthz"
curl -fsS "$APP_URL/readyz"
curl -fsS "$APP_URL/api/v1/meta"              # version, mode, tenancy, branding
curl -fsS "$APP_URL/api/v1/datasets/coverage" # what the platform can answer today
```

Production (`envs/prod`) follows the same sequence with `create_ci_resources =
false`: ECR and the GitHub OIDC role are created once, in dev, and production
consumes the images.

---

## Routine deploy

Tagging `v*.*.*` runs `release.yml`, which re-runs the entire merge gate against
the tagged commit, then builds and pushes a multi-architecture image to GHCR with
a provenance attestation and an attached CycloneDX SBOM. **It does not touch
ECS** — promoting an image to a running environment is a deliberate act:

```bash
cd deploy/terraform/envs/prod
# 1. Point Terraform at the new digest-pinned image and apply.
#    This registers a new task-definition revision; the service is untouched,
#    because it declares `ignore_changes = [task_definition]`.
terraform apply -var 'image=ghcr.io/acme/snowobs@sha256:…'

# 2. Move the services onto it.
CLUSTER=$(terraform output -raw cluster_name)
for svc in app_service_name worker_service_name; do
  aws ecs update-service --cluster "$CLUSTER" \
    --service "$(terraform output -raw $svc)" \
    --task-definition "$(terraform output -raw ${svc%_service_name}_task_definition_family)"
done

# 3. Watch it land.
aws ecs wait services-stable --cluster "$CLUSTER" \
  --services "$(terraform output -raw app_service_name)"
```

**Which registry?** `release.yml` publishes to **GHCR**
(`ghcr.io/<owner>/<repo>`), while the `ci` Terraform module creates **ECR**
repositories (immutable tags, scan-on-push, KMS-encrypted) and the network module
provisions `ecr.api`/`ecr.dkr` interface endpoints for pulling from them. A
zero-egress deployment can only pull from ECR, so mirror the released digest into
ECR and deploy that reference:

```bash
ECR=$(terraform -chdir=deploy/terraform/envs/dev output -json ecr_repository_urls | jq -r '.snowobs')
docker buildx imagetools create --tag "$ECR:v0.1.0" ghcr.io/acme/snowobs:v0.1.0
```

Then use the `$ECR` reference as `image` above. Pulling straight from GHCR needs
internet egress and registry credentials on the task, neither of which this stack
provides by default.

The rollout is blue/green at the task level:
`deployment_minimum_healthy_percent = 100`, `deployment_maximum_percent = 200`,
and a **deployment circuit breaker with `rollback = true`**. A revision whose tasks
never pass their health checks is rolled back automatically, without anyone
watching.

Post-deploy checks: `/healthz`, `/readyz`, `/api/v1/meta` reports the expected
version, one dashboard tile renders, and the `…-app-5xx` alarm stays in `OK`.

---

## Rollback

Automatic first: the circuit breaker reverts a deployment whose tasks fail health
checks. If a release is bad in a way health checks do not catch — wrong numbers,
a broken page — roll back explicitly:

```bash
CLUSTER=$(terraform output -raw cluster_name)
FAMILY=$(terraform output -raw app_task_definition_family)

# List revisions, newest first, and pick the last known-good one.
aws ecs list-task-definitions --family-prefix "$FAMILY" --sort DESC --max-items 5

aws ecs update-service --cluster "$CLUSTER" \
  --service "$(terraform output -raw app_service_name)" \
  --task-definition "$FAMILY:<previous-revision>"
aws ecs wait services-stable --cluster "$CLUSTER" \
  --services "$(terraform output -raw app_service_name)"
```

Roll the worker back the same way, using its own family. Then set `image` in
`terraform.tfvars` back to the previous digest so the next `terraform apply` does
not silently re-introduce the bad revision.

**Rollback is safe because the application owns no schema.** There are no
migrations to reverse today (see [`SECURITY.md`](SECURITY.md) L3), so an older
image cannot meet a newer database. When the first Alembic migration lands, this
section needs a compatibility rule and this sentence must be corrected.

Terraform-level rollback for an infrastructure change: `git revert` the change and
re-apply. Never `terraform destroy` a production environment to fix it —
`deletion_protection = true` and `skip_final_snapshot = false` in prod are there to
make that difficult on purpose.

---

## Local operation

| Command | What it does |
|---|---|
| `make doctor` | Checks Python, `uv`, Node, Docker daemon and resources, the six ports the stacks bind, free disk, the settings object, and the fixture lake. Exit `0` means `make demo` will run |
| `make demo` | Whole platform on synthetic data at `http://localhost:8080`; smoke-tests five endpoints before telling you it is up |
| `make demo-native` | The same, as host processes, no Docker |
| `make demo-down` | Stops the demo stack and deletes its volumes, including the fixture lake |
| `make dev` | Postgres/Redis/MinIO in containers; API on `:8000`, worker, and Vite on `:5173` as host processes with hot reload |
| `make infra` / `make infra-down` | Just the infrastructure containers (volumes preserved on down) |
| `make seed` | Generate and ingest the demo dataset into `.data` |
| `make test` / `make test-parity` / `make eval` / `make lint` / `make typecheck` | The merge gates, runnable locally |
| `make catalog` / `make contracts` / `make provisioning` | Regenerate the three generated artefacts |
| `make build` / `make build-allinone` / `make scan` / `make sbom` | Images, Trivy scan, CycloneDX SBOM |
| `make terraform-fmt` / `make terraform-validate` | Terraform gates |

`make help` lists every target.

---

## Alarm procedures

Seven CloudWatch alarms are created by
`deploy/terraform/modules/observability`. `terraform output alarm_names` lists
them for the environment. Each notifies `alarm_topic_arn`; **without that variable
set the alarms page nobody**, so setting it is part of going live.

| Alarm | Condition | Tier | Procedure |
|---|---|---|---|
| `<name>-unhealthy-targets` | `UnHealthyHostCount > 0` for 3 × 60 s | P1 | [The app is down](#the-app-is-down) |
| `<name>-app-5xx` | `HTTPCode_Target_5XX_Count > 10` in 2 × 5 min | P1 | [The app is erroring](#the-app-is-erroring) |
| `<name>-app-latency-p95` | `TargetResponseTime > 3 s` for 3 × 5 min | P2 | [Dashboards are slow](#dashboards-are-slow) |
| `<name>-app-cpu-high` | ECS app `CPUUtilization > 85%` for 3 × 5 min | P2 | [Dashboards are slow](#dashboards-are-slow) |
| `<name>-worker-not-running` | ECS worker `CPUUtilization <= 0` for 2 × 5 min | P2 | [The worker is not running](#the-worker-is-not-running) |
| `<name>-rds-cpu-high` | RDS `CPUUtilization > 80%` for 3 × 5 min | P2 | [The metadata database is struggling](#the-metadata-database-is-struggling) |
| `<name>-rds-storage-low` | `FreeStorageSpace` below the threshold (default 4 GiB) for 2 × 5 min | P2 | [The metadata database is struggling](#the-metadata-database-is-struggling) |

Thresholds are variables: `error_count_threshold`, `latency_p95_threshold_seconds`,
`database_free_storage_threshold_bytes`. The latency default of 3 s is deliberately
above the 300 ms warm-tile target from BUILD_PROMPT §22.3, because it must tolerate
cold tiles and agent turns without paging.

### The app is down

**Alarm:** `<name>-unhealthy-targets`. One or more app tasks are failing `/readyz`.

**Diagnose, in this order.**

1. Is it the app or its dependencies? `/readyz` names the failing component:
   ```bash
   curl -s "$APP_URL/readyz" | jq .
   ```
   `postgres: unavailable` or `redis: unavailable` moves you to the data tier;
   both `ok` while the ALB still sees unhealthy targets means the tasks are not
   reaching a listening state.
2. What do the tasks say?
   ```bash
   aws ecs describe-services --cluster "$CLUSTER" --services "$APP_SERVICE" \
     --query 'services[0].events[:10]'
   aws logs tail "$LOG_GROUP" --since 30m --filter-pattern '?ERROR ?Traceback'
   ```
3. Configuration errors are loud by design: `snowobs_common.config.load_settings`
   fails fast with `Invalid configuration — <field>: <reason>`. A task that never
   starts after a config change is almost always this.

**Act.**

| Finding | Action |
|---|---|
| A bad release | [Rollback](#rollback) |
| An empty or wrong secret | Re-put the secret value, then `--force-new-deployment` |
| Postgres unreachable | [The metadata database is struggling](#the-metadata-database-is-struggling) |
| Redis unreachable | Check the ElastiCache replication group and the `cache` security group. The API process still starts, but `/readyz` reports `redis: unavailable` and returns 503, so the ALB keeps the task out of rotation |
| Tasks killed on memory | Raise `app_memory` in the environment's tfvars and apply; see [`AWS_COST.md`](AWS_COST.md) for the effect on the bill |

**Verify:** targets healthy, `curl -fsS "$APP_URL/readyz"` returns `200`, alarm
returns to `OK`.

### The app is erroring

**Alarm:** `<name>-app-5xx`. The application is returning server errors.

**Diagnose.** Errors are RFC 7807 problem documents; the `problem_type` URI names
the failure class, and the `trace_id` ties the response to the log line.

```bash
aws logs tail "$LOG_GROUP" --since 30m --format short | grep -i 'error\|exception'
```

| `problem_type` | Meaning | Action |
|---|---|---|
| `…/sql-guard` | A statement was refused by the guard | Expected for a bad ad-hoc query. A *burst* of these against dashboard traffic means a compiler change is emitting SQL the guard rejects — treat as a bad release |
| `…/compilation` | A metric request could not be compiled | Usually a client sending an unknown metric or dimension. Check `GET /api/v1/metrics/catalog` |
| `…/snowflake-connection` | LIVE connection failure | Key expired or rotated, role dropped, network path lost. See [Credential rotation](#credential-rotation) |
| `…/agent-budget` | A caller hit the per-turn or daily agent budget | Working as designed. Raise the limits only deliberately |
| `…/approver-identity` | An approval arrived without `X-Snowobs-Actor` | Client bug; the refusal is correct |
| No problem type, bare 500 | An unhandled exception | Capture the traceback and the trace id, then [Rollback](#rollback) |

**Verify:** the 5xx rate returns to zero over two evaluation periods.

### Dashboards are slow

**Alarms:** `<name>-app-latency-p95`, `<name>-app-cpu-high`.

**Diagnose.**

1. Cold or warm? The result cache is per-process, bounded (512 entries), with a
   300-second TTL, keyed on
   `{sql fingerprint, dataset version, RLS context}`. After a deploy, a scale-out,
   or an upload, every tile is cold. The budgets asserted in
   `apps/api/tests/test_performance.py` are 300 ms warm and 3 s cold.
2. Is it one metric or all of them? Time a tile directly:
   ```bash
   time curl -s -o /dev/null "$APP_URL/api/v1/metrics/cost.total_credits/tile?start=…&end=…"
   ```
3. Is the app at its scaling ceiling? Target-tracking policies hold 60% CPU
   (`app_target_cpu_percent`) and 400 requests per task
   (`app_target_requests_per_task`), between `app_min_count` and `app_max_count`.
   Pinned at max means raise the ceiling.

**Act.**

| Finding | Action |
|---|---|
| Cold caches after a deploy | Wait one TTL, or [re-warm](#re-running-an-allocation-and-re-warming-caches) |
| At the autoscaling ceiling | Raise `app_max_count` (and `app_cpu`/`app_memory` if a single request is slow), apply, redeploy |
| One metric is slow | Read its SQL — every response carries it — and check the time range. A tile must run **one** aggregate statement, not one per day; `test_a_tile_runs_one_statement_not_one_per_day` guards that |
| Slow in LIVE mode | The pinned warehouse is `WH_SNOWOBS_APP` (XSMALL). Statement timeout is `SNOWFLAKE__STATEMENT_TIMEOUT_S` (default 300 s) |

Never fix latency by silently narrowing the time window. If the honest answer is
slow, say it is slow.

### The worker is not running

**Alarm:** `<name>-worker-not-running`.

**What it actually means today.** The worker ships the arq harness and one job,
`ping`. Scheduled refresh, reconciliation, close, forecast, and alert-evaluation
jobs are **not implemented** (see [`ARCHITECTURE.md`](ARCHITECTURE.md) §11): the
API computes allocations, coverage, and metrics on request. So a stopped worker
today degrades self-diagnostics, not the numbers. The alarm still matters — it is
the first signal of a crash-looping task or an unreachable Redis, and the moment
scheduled jobs land it becomes a data-freshness alarm.

**Diagnose.**

```bash
aws ecs describe-services --cluster "$CLUSTER" --services "$WORKER_SERVICE" \
  --query 'services[0].{desired:desiredCount,running:runningCount,events:events[:5]}'
aws logs tail "$LOG_GROUP" --since 30m --filter-pattern 'worker'
```

The all-in-one entrypoint exits the container as soon as **any** supervised
component exits, so a crash-looping worker is visible rather than masked by a
healthy API.

**Act.** Redis unreachable → check the replication group and security group. Task
repeatedly killed → raise `worker_memory`. Bad release → [Rollback](#rollback).
`desiredCount = 0` → someone scaled it to zero; restore `worker_min_count`.

**Verify:** `runningCount >= 1` and the alarm clears.

### The metadata database is struggling

**Alarms:** `<name>-rds-cpu-high`, `<name>-rds-storage-low`.

**Context first.** Postgres holds app metadata only — never telemetry (R2). It
should be almost idle. Sustained high CPU or growing storage on this instance means
something is writing that should not be, or the instance is undersized for
connection churn, not that the platform is busy.

**Diagnose.**

```bash
aws rds describe-db-instances --db-instance-identifier "$DB_ID" \
  --query 'DBInstances[0].{class:DBInstanceClass,storage:AllocatedStorage,max:MaxAllocatedStorage,az:MultiAZ,status:DBInstanceStatus}'
aws logs tail "/aws/rds/instance/$DB_ID/postgresql" --since 1h
```

Statements over 1000 ms are logged (`log_min_duration_statement = 1000`).

**Act.**

| Finding | Action |
|---|---|
| Storage genuinely full | Autoscaling should handle it: `db_max_allocated_storage_gb` defaults to 100 (Small) / 500 (Standard). Raise it and apply |
| Connection churn | The API uses one pooled async engine per process with `pool_pre_ping`. Many app tasks × pool size can still exhaust `max_connections`; reduce `app_max_count` or move up an instance class |
| Undersized | Raise `db_instance_class`. In prod `apply_immediately = false`, so the change lands in the maintenance window (`sun:03:30-sun:04:30` UTC by default) unless you force it |
| Telemetry being written | A defect. Nothing in the current tree writes telemetry to Postgres; find the writer before resizing anything |

---

## Application conditions

These are conditions the *product* raises. They are not CloudWatch alarms today —
the platform surfaces them in the UI and in CI. When the alerting engine is wired
to routes, these become the first rules, and the linked sections are their runbook
URLs.

### The reconciliation gate is red

**Symptom.** The chargeback page shows the reconciliation banner in its failed
form, the team table is **empty**, and `figures_published` is `false`. This is
correct behaviour, not a bug: R6 says allocated cost reconciles or does not
publish.

**Read the banner.** It states allocated credits, metered credits, the variance in
credits and percent, the tolerance, and the three days with the largest absolute
variance.

**Diagnose.**

```bash
curl -s "$APP_URL/api/v1/chargeback/allocation?start=2026-08-01&end=2026-08-31" | jq '.reconciliation'
curl -s "$APP_URL/api/v1/chargeback/reconciliation/2026-08-14" | jq .
```

| Signal | Likely cause | Action |
|---|---|---|
| `outcome: "no_data"` | No metered credits in the period | Not a failure. Check that `warehouse_metering_history` has landed — `/api/v1/datasets/coverage` |
| Variance concentrated on one or two days | A partial load, or a source that landed mid-window | Re-export and re-upload those days; the loader merges last-write-wins on the grain, so a re-upload corrects rather than double-counts |
| Allocated consistently **below** metered | An input source is incomplete — usually `query_attribution_history`, whose documented latency is 8 h | Wait out the latency, then re-check. If it persists, probe the source |
| Allocated consistently **above** metered | Duplicate rows in the attribution input, or a mismatched period | Check the coverage page's row counts and window for the two inputs |
| Variance just over 0.5% and stable | The excluded query classes (queries under ~100 ms, Adaptive Warehouse jobs) may exceed the tolerance in this account (A-3) | Investigate before touching the tolerance. Raising `FINOPS__RECONCILE_TOLERANCE_PCT` means publishing figures that do not reconcile |

**Never** publish around the gate. If the variance is genuine and understood, it
belongs in the assessment as a finding, not in a chargeback line.

**Verify:** re-request the allocation; the banner reads `Reconciled: …, within
±0.5%` and the team table repopulates.

### A source has gone stale

**Symptom.** The Coverage & sources page shows a source as `stale`, or a freshness
banner reports a floor larger than expected. `stale` means the source landed once
and its newest row is now older than its documented latency allows — it is not
`missing`, and the remediation text says so.

**Diagnose.**

```bash
curl -s "$APP_URL/api/v1/datasets/coverage" | jq '.sources[] | select(.status != "available")'
```

Each entry carries `documented_latency_minutes`, `freshness_minutes`, and a
copy-pastable `remediation`.

**Act.**

- **OFFLINE:** the extract schedule has stopped firing, or the last export was
  narrower than the window. Re-run the extract kit
  (`GET /api/v1/exports/extract-kit`) and upload again.
- **LIVE:** run the probe. A permission error comes back as the exact
  `GRANT DATABASE ROLE …` statement that fixes it; an edition gate (for example
  `ACCESS_HISTORY`, which needs Enterprise) comes back as `not_applicable`, and no
  grant will fix that.
- **Neither:** the view's own latency may have changed. Latency lives in
  `packages/semantics/sources/*.yaml` and never in code (R7); correct the YAML,
  re-run `make catalog`, and record the change in
  [`ASSUMPTIONS.md`](ASSUMPTIONS.md).

Do not paper over staleness in the UI. Every affected KPI already states its floor;
a stale source that is silently ignored is exactly the failure R7 exists to prevent.

### Parity fails in CI

**Symptom.** `make test-parity` fails: a metric's Snowflake-dialect and
DuckDB-dialect renderings disagree, or a golden SQL snapshot moved.

**Diagnose.** The failure names the metric, the row, the column, and both values.

| Failure | Meaning | Action |
|---|---|---|
| Golden snapshot changed | The compiled SQL changed | If intended, review the diff line by line and commit the new snapshot. If not, revert the change |
| Values differ, no tolerance declared | A real divergence | Fix the metric or the shim. **Do not** add a tolerance to make it pass |
| Values differ on a percentile metric | Expected: `APPROX_PERCENTILE` is a t-digest estimate, `quantile_cont` is exact | Confirm the divergence is inside the declared tolerance in [`PARITY_EXCEPTIONS.md`](PARITY_EXCEPTIONS.md) |
| A new shim is needed | A construct one engine lacks | Add it to `dialect_shims.py` with a parity test — never fork the metric definition (R1) |

Any new tolerance must be added to `PARITY_EXCEPTIONS` in
`packages/engines/src/snowobs_engines/parity.py` **with a written justification**;
a test asserts every entry names a real metric and carries one.

### Agent evals fail in CI

**Symptom.** `make eval` exits non-zero. The runner prints all four gates:

```
Tool selection : … (gate ≥ 90%)
Numeric        : … (gate = 100%)
Fabricated     : … (gate = 0)
Injection      : … complied (gate = 0)
```

| Gate | Meaning | Action |
|---|---|---|
| **Fabricated > 0** | An answer stated a figure no tool returned. The most serious failure in the suite (R12) | Stop. Read the trace on the failing question; find whether the grounding check was weakened or the model was allowed to narrate an unreturned number. Never relax the gate |
| **Injection > 0** | An adversarial fixture was obeyed | Same severity. Check `neutralise()` patterns and the fence in `wrap_untrusted()`. Reporting an attempt is not compliance — the scorer already distinguishes them |
| **Numeric < 100%** | An assertable question returned the wrong number | Usually a metric or fixture change. Recompute the expectation from the fixture independently before changing it |
| **Tool selection < 90%** | Routing regressed | Look at synonyms and metric descriptions in the YAML first; the router matches on them |

The suite needs a landed dataset, which CI seeds with
`uv run python scripts/demo_seed.py --root .data --end-date 2026-08-24 --force`.
An eval run against an empty lake fails for the wrong reason.

### The LLM provider is down

**Symptom.** Agent answers fail or time out; dashboards are unaffected.

**Act.** Nothing about the numbers depends on the LLM (R12): every figure comes
from a governed metric query, and the model only narrates. The supported fallback
is to switch the provider off:

```bash
cd deploy/terraform/envs/prod
terraform apply -var 'llm_provider=none'
aws ecs update-service --cluster "$CLUSTER" --service "$APP_SERVICE" --force-new-deployment
```

With `LLM__PROVIDER=none` the agent console keeps working: questions route to
governed metrics by keyword and synonym matching, tools run, figures are reported
with their provenance, and each answer states plainly that narrative generation is
disabled. Dashboards, chargeback, coverage, and the product surfaces are untouched.

Switching provider (`anthropic` → `bedrock`) is the same change with the
corresponding secret populated and, for Bedrock, `bedrock_model_arns` listing the
exact models the task role may invoke — but read the caveat under
[Credential rotation](#credential-rotation) first: neither vendor SDK is installed
in the published image, so both providers currently fail with a readable
`LLMError` and `none` is the working fallback.

---

## Credential rotation

**Snowflake key pair.** Snowflake supports two keys per user, which is what makes
this a zero-downtime rotation.

1. Generate a new key pair; keep the old one live.
2. Register the new public key in the user's **second** key slot, leaving the
   first in place. Snowflake supports two active public keys per user precisely
   for this (verified — [`ASSUMPTIONS.md`](ASSUMPTIONS.md) §7); check the current
   `ALTER USER … SET RSA_PUBLIC_KEY_2` syntax on the key-pair authentication page
   before running it rather than from memory.
3. Put the new private key into the secret:
   ```bash
   aws secretsmanager put-secret-value \
     --secret-id snowobs-prod/snowflake/private-key --secret-string file://new_key.p8
   ```
4. Roll the services (`--force-new-deployment`) and confirm with
   `POST /api/v1/connections/probe`.
5. Only then unset the old public key and promote the new one into the first
   slot. Removing the old key before step 4 has confirmed the new one works is the
   way this procedure locks you out.

**LLM API key.** Put the new value into `<name>/llm/api-key`, roll the app service,
ask the agent console one question, then revoke the old key at the provider.

Before relying on that: the key path is not wired end to end today. The task
definition injects the secret as `LLM__API_KEY`, which is not a field on
`LLMSettings`, and `AgentService` builds the provider without an `api_key`; the
vendor SDKs are also optional extras (`snowobs-llm[anthropic]`, `[bedrock]`) that
the published image does not install. Selecting `anthropic` or `bedrock` therefore
fails with a readable `LLMError` naming the missing package. Until that is fixed,
run `LLM__PROVIDER=none` or `cortex`. Recorded in
[`SECURITY.md`](SECURITY.md) §9, L8.

**Database.** The RDS master password is managed and rotated by AWS
(`manage_master_user_password`). After a rotation, recompose
`<name>/app/database-url` from the RDS-managed secret and roll the services — the
application has a single `DATABASE_URL` setting with no separate password field,
which is why the whole URL is the secret.

**Suspected exposure.** Rotate first, investigate second. Then check the log group
for the trace ids around the exposure window, and — for a Snowflake credential —
ask the customer to review `LOGIN_HISTORY` and `QUERY_HISTORY` for the service
user. The platform tags every session `SNOWOBS:<tenant>:<surface>:<trace id>`
(`snowobs_live.connection.query_tag`, prefix from `SNOWFLAKE__QUERY_TAG_PREFIX`), so its activity is
separable from anything else done with the credential.

---

## Backup and restore

| Asset | Protection | RPO |
|---|---|---|
| Postgres (app metadata) | RDS automated backups, `backup_retention_days` = 7 (dev) / 14 (prod), 02:00–03:00 UTC window; Multi-AZ in prod; final snapshot on delete unless explicitly skipped | ≤ 24 h |
| S3 data lake | Versioning on; `uploads/` expire after `upload_retention_days` (30); `curated/` transitions to STANDARD_IA at 90 days; non-current versions expire at 7 / 30 days | Object-level |
| Redis | `snapshot_retention_limit` = 1 day. The queue is not a system of record; snapshots are a convenience, not an RPO | None claimed |
| Curated telemetry | **Re-derivable.** LIVE: re-read from Snowflake. OFFLINE: re-ingest the retained uploads | Not backed up separately |

Stated targets: **RPO 24 h, RTO 4 h for the app tier**, and honestly, curated data
is re-derivable rather than restored.

**Restore Postgres from a snapshot:**

```bash
aws rds describe-db-snapshots --db-instance-identifier "$DB_ID" \
  --query 'sort_by(DBSnapshots,&SnapshotCreateTime)[-5:].[DBSnapshotIdentifier,SnapshotCreateTime]'

aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier "${DB_ID}-restore" \
  --db-snapshot-identifier "<snapshot>" \
  --db-subnet-group-name "<name>-db" \
  --vpc-security-group-ids "<database-sg>" \
  --no-publicly-accessible
```

Then point `<name>/app/database-url` at the restored endpoint and roll the
services. Keep the original instance until the restore is verified; renaming
instances to swap them in is faster but leaves you nothing to go back to.

---

## Tenant purge

Removing a tenant's data means removing its storage prefix. Tenant ids are
validated slugs and every path is containment-checked
(`packages/ingest/src/snowobs_ingest/tenancy.py`), so a prefix is exactly one tenant's data.

```bash
# OFFLINE lake on local disk (demo / single-node):
rm -rf .data/<tenant>

# S3, once the object-storage adapter is in use:
aws s3 rm "s3://$BUCKET/<tenant>/" --recursive
```

Because the bucket is versioned, `s3 rm` leaves delete markers and prior versions.
For a genuine erasure request, remove the non-current versions too, and record the
action — the platform has no durable audit log yet
([`SECURITY.md`](SECURITY.md) L3), so this record has to live in your own change
management until it does.

App metadata for the tenant lives in the in-process ledger, so restarting the
service clears it. There is no tenant-scoped delete API.

---

## Re-running an allocation and re-warming caches

**Re-running an allocation (the "re-run a failed close" case).** Allocation and
reconciliation are computed **on request** from the landed data; nothing is stored
and there is no close job to re-run. Once the underlying data is corrected, simply
re-request the period:

```bash
curl -s "$APP_URL/api/v1/chargeback/allocation?start=2026-08-01&end=2026-08-31" \
  | jq '{outcome: .reconciliation.outcome, published: .figures_published}'
```

The response is recomputed from scratch, so a corrected upload is reflected as soon
as the result cache's 300-second TTL expires for the constituent metric queries.

**Re-warming caches.** The cache is per-process and in-memory, so a deploy or a
scale-out starts cold and there is no cache to invalidate by hand. Warm it by
requesting the tiles the dashboards ask for:

```bash
for m in cost.total_credits cost.billed_credits cost.spend_usd \
         cost.unattributed_share wh.idle_pct cost.per_query; do
  curl -s -o /dev/null "$APP_URL/api/v1/metrics/$m/tile?start=$START&end=$END"
done
```

Cache entries carry the dataset version in their key, so an upload cannot serve a
stale answer: the key changes with the data.

---

## The alert engine

`packages/analytics/src/snowobs_analytics/alerting.py` implements the four-tier
model from BUILD_PROMPT §14 as a library:

| Tier | Meaning | Channels | Ack |
|---|---|---|---|
| P1 | Business impact now | page, chat | 15 min |
| P2 | Degraded or drifting | chat, ticket | 480 min (same business day) |
| P3 | Waste or early warning | chat | Weekly triage |
| P4 | Informational | digest | Monthly |

Properties that are enforced in code:

- **A rule without a runbook URL does not construct.** `AlertRule.__post_init__`
  raises `AlertRuleError`, and the URL must be an `http(s)` URL or an in-app path.
  This is why every alarm in this document has a section of its own.
- **Deduplication.** A dedup key of `rule id | sorted scope` suppresses re-fires
  while an alert is open; suppressed counts are kept.
- **Persistence.** A rule with `persistence > 1` needs consecutive breaching
  windows before it fires. Magnitude without persistence is noise.
- **An unknown value never fires.** `evaluate(None)` returns `False` — R3 applied
  to alerting.
- **Pruning proposals.** A rule that has fired at least 5 times over 60 days with
  zero actions taken is proposed for disabling, with its statistics.
- **Payloads never carry query text.**
- **Backtest.** `backtest(rule, series)` replays a rule over history — "this rule
  would have fired 4 times last month" — before anyone turns it on.
- **OFFLINE export.** `to_snowflake_alert_ddl(rule, warehouse=…)` emits deployable
  `CREATE ALERT` DDL carrying the tier and the runbook URL in its comments.

**What does not exist yet:** no alert rules are declared in this repository, there
is no alerts API or UI, and nothing evaluates rules on a schedule (the worker ships
only `ping`). The operational alerting that is live today is the seven CloudWatch
alarms above. When rules are authored, each must link to a section in this file, or
it will not construct.
