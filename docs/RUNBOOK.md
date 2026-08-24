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
  - [An organization figure is missing an account](#an-organization-figure-is-missing-an-account)
  - [A source has gone stale](#a-source-has-gone-stale)
  - [Parity fails in CI](#parity-fails-in-ci)
  - [Agent evals fail in CI](#agent-evals-fail-in-ci)
  - [The LLM provider is down](#the-llm-provider-is-down)
- [Alert conditions](#alert-conditions) — one entry per declared alert rule
  - [Daily spend has spiked](#daily-spend-has-spiked)
  - [Spend is tracking over budget](#spend-is-tracking-over-budget)
  - [Unattributed cost is above target](#unattributed-cost-is-above-target)
  - [The cloud services ratio is too high](#the-cloud-services-ratio-is-too-high)
  - [A warehouse is burning idle credits](#a-warehouse-is-burning-idle-credits)
  - [A warehouse is queueing](#a-warehouse-is-queueing)
  - [Query failures are elevated](#query-failures-are-elevated)
  - [Query performance has regressed](#query-performance-has-regressed)
  - [Storage is growing faster than expected](#storage-is-growing-faster-than-expected)
  - [A pipeline is failing or late](#a-pipeline-is-failing-or-late)
  - [Failed logins have spiked](#failed-logins-have-spiked)
  - [A privileged grant appeared](#a-privileged-grant-appeared)
  - [AI services credits have jumped](#ai-services-credits-have-jumped)
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
| `GET /readyz` | Can this instance serve traffic? | Each **required** backing service — Postgres (`SELECT 1`), Redis (`PING`) — with a 2-second timeout, run concurrently | `200` when every required component is `ok`; `503` with a per-component breakdown otherwise |

### Which components are required

Readiness describes *this* deployment, not the topology the compose file draws.
`READINESS__REQUIRE_POSTGRES` and `READINESS__REQUIRE_REDIS` say whether the
instance needs each store to serve traffic; a component that is not required is
reported as `not_required` with the reason and is **not probed**.

| Deployment | Postgres | Redis | Why |
|---|---|---|---|
| `make demo-native` | not required | not required | Starts no containers, and needs none |
| `make demo` (compose) | not required | not required | Starts both to mirror production, but no code path reads either — and both demo paths should show the same page |
| `deploy/compose` (dev stack) | **required** | **required** | Provides both, so a store that failed to start is visible on `/status` |
| AWS (Terraform) | **required** | **required** | RDS and ElastiCache are provisioned and paid for on purpose |

Both default to *not required* because today the API's only consumer of either
store is the readiness check itself: the query cache is in-process, no code
reads the metadata database (A-16, A-18), and Redis is the worker's queue.
Requiring them by default reported a demo as `not_ready` — two red crosses and
a 503 — for services that deployment does not use.

**This flips when the first Alembic migration lands.** At that point application
code reads Postgres, `require_postgres` becomes `true` for every deployment, and
the ALB starts taking a task out of rotation when its database is unreachable —
which it does not do today. Until then, an AWS deployment is the one that gates
on both, so a misconfigured store is caught the day it is deployed rather than
the day something first reads it.

The split is deliberate. The container's own `HEALTHCHECK` and the ECS container
health check use `/healthz`, so a task whose database is briefly unreachable is not
killed for it. The ALB target group health-checks `/readyz`, so such a task is
taken out of rotation instead. `deregistration_delay` is 30 seconds and the
service's health-check grace period is 90 (`health_check_grace_period_seconds`),
which covers loading the SPA bundle and the semantic model at import time.

The SPA's **System status** page (`/status`) renders both, naming each unavailable
component and its error type, and showing a component this deployment does not
require in grey with the reason rather than as a failure.

`/readyz` is unauthenticated, so a component's `detail` is the exception *type*
and never its message: a driver's connection error carries the host, the port,
and often the user name it failed to authenticate with.

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
| `…/data-unavailable` | A source the answer needs has not been landed. 422, not 404: the remedy is to supply the input | Check `/api/v1/datasets/coverage`; the remediation is on the source. Chargeback raises it when neither `warehouse_metering_history` nor `query_attribution_history` has landed |
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

These are conditions the *product* raises. They are not CloudWatch alarms — the
platform surfaces them in the UI and in CI. The conditions that a declared alert
rule watches have their own sections under
[Alert conditions](#alert-conditions); the ones here are raised by the
application without a rule behind them.

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
# Omitting the dates allocates the landed window and echoes it back as
# period_start / period_end.
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

**Multi-account deployments.** The gate runs at whatever scope was asked for.
Add `?scope=account&account=NAME` to narrow the allocation *and* the metered
total it is checked against — a red gate on one account while the organization
is green points at that account's inputs, and is much faster to diagnose than
the roll-up:

```bash
curl -s "$APP_URL/api/v1/chargeback/allocation?scope=account&account=ACME_PROD" \
  | jq '{scope, scope_account, reconciliation: .reconciliation.outcome}'
```

A `422` naming the account means it has landed no chargeback inputs at all; the
response lists the accounts that have. That is deliberate — allocating it would
produce an empty waterfall that reconciles against an empty bill, and the gate
would go green over a chargeback of nothing.

### An organization figure is missing an account

**Symptom.** An organization-wide figure carries `scope_partial: true` and a
non-empty `missing_accounts`. The UI shows the same as a note under the figure.

**What it means.** Billing (`ORGANIZATION_USAGE`) names every account the
organization contains, including accounts that have never uploaded their own
detail. The named account is in the bill but has landed nothing, so any
organization roll-up of operational detail is an **under-count** by whatever
that account consumes.

**Diagnose.**

```bash
curl -s "$APP_URL/api/v1/metrics/q.volume/tile" | jq '{scope_partial, contributing_accounts, missing_accounts}'
curl -s "$APP_URL/api/v1/datasets/coverage" | jq '.accounts'
```

**Act.** Onboard the named account — upload its extracts (OFFLINE) or configure
its connection and run the grants probe (LIVE). The warning clears by itself
once that account's data lands; it is not a setting to dismiss. If the account
is genuinely out of scope for this deployment, that belongs in
[`ASSUMPTIONS.md`](ASSUMPTIONS.md) so the next reader knows the roll-up excludes
it on purpose.

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

## Alert conditions

One section per declared rule in [`config/alert_rules.yaml`](../config/alert_rules.yaml).
The rule's `runbook` field points here, the loader refuses a rule whose link does
not resolve, and a test asserts every link lands on a heading that exists. If you
add a rule, add its section first.

Two commands are the same for every one of them, so they are not repeated below:

```bash
# What is the rule, what has it done, and what does it reach?
curl -s "$APP_URL/api/v1/alerts/rules/<rule-id>" | jq '{threshold, window, persistence, tier, channels, statistics}'
# What would it have done over the landed window, before you change anything?
curl -s -X POST "$APP_URL/api/v1/alerts/rules/<rule-id>/backtest" | jq '{would_have_fired, firing_days, summary}'
```

If the backtest says a rule would have fired thirty times last month, the rule is
wrong, not the account. Fix the threshold or the persistence rather than muting
the channel.

### Daily spend has spiked

**Rule.** `cost.daily_spend_anomaly` (P1) — robust z-score of `cost.billed_credits`
for `WAREHOUSE_METERING`, two consecutive days.

**Diagnose.** Get the decomposition before you get an opinion:

```bash
curl -s -X POST "$APP_URL/api/v1/metrics/query" -H 'content-type: application/json' \
  -d '{"metrics":["cost.by_warehouse_credits"],"dimensions":["warehouse"],
       "start":"'"$SPIKE_DAY"'","end":"'"$SPIKE_DAY"'","limit":20}' | jq '.rows'
```

| Signal | Likely cause | Action |
|---|---|---|
| One warehouse holds most of the delta | A new or resized workload | Confirm with `wh.credits_per_query` and `wh.utilisation_pct` for that warehouse; if utilisation is flat and credits doubled, the size changed |
| The delta is spread across warehouses | A platform-wide change — a schedule moved, a clustering job started | Check `pipe.serverless_task_credits` and `cost.cloud_services_ratio` for the same day |
| The spike is a backfill | Expected | Say so on the alert and let it resolve; do not lower the threshold to hide it |
| The baseline is short | The account has under 14 days landed | The detector returns nothing below its minimum baseline, so this rule cannot have fired for that reason — check the landed window on the coverage page |

**Verify.** The next evaluation resolves the alert once the day's score falls back
under the threshold. Nothing needs acknowledging for it to close.

### Spend is tracking over budget

**Rule.** `cost.budget_breach` (P2) — month-to-date `chargeback.budget_variance_credits`
above the configured budget.

**First, check the budget is real.** The shipped threshold is the metric's declared
warn level and is a placeholder. If nobody has set it to this account's actual
budget, the correct action is to set it, not to investigate the spend.

**Then.** Compare the landing point with the budget rather than the month-to-date
figure — a breach on the 3rd and a breach on the 28th are different problems:

```bash
curl -s "$APP_URL/api/v1/metrics/cost.billed_credits/tile?start=$MONTH_START&end=$TODAY" | jq '{value, as_of, latency_floor_minutes}'
```

| Signal | Action |
|---|---|
| Landing point inside budget, MTD over because of one spike | Handle the spike; the budget is fine |
| Landing point over budget with flat daily spend | A step change in baseline consumption — go to the optimisation workbench, not to the alert |
| Budget was never configured | Set it. A budget alert against a placeholder is noise with a P2 on it |

### Unattributed cost is above target

**Rule.** `cost.unattributed_share_high` (P3) — `cost.unattributed_share` above 15%
for two consecutive weeks.

**Diagnose.** Unattributed cost is a tagging problem, and the leaderboard of
contributors is public by design:

```bash
curl -s -X POST "$APP_URL/api/v1/metrics/query" -H 'content-type: application/json' \
  -d '{"metrics":["cost.unattributed_share"],"dimensions":["warehouse"],"limit":20}' | jq '.rows'
```

| Signal | Action |
|---|---|
| One warehouse dominates | Its jobs are not setting `QUERY_TAG`. Fix the job, not the rule |
| Spread evenly and rising | A tagging convention changed, or a new team onboarded without one. Check the allocation rules in Admin |
| Concentrated in ad-hoc/BI warehouses | Interactive users cannot be expected to tag by hand; map them by role or by warehouse in the allocation rules instead |

Do not raise the threshold to clear the alert. Unattributed cost is the number
that decides whether chargeback is credible.

### The cloud services ratio is too high

**Rule.** `cost.cloud_services_ratio_high` (P4) — `cost.cloud_services_ratio` above
10% of compute for two consecutive weeks.

Above 10% the excess is billable; at or below it the whole day's cloud services is
rebated. This is informational: it goes to the digest, and the fix is a query
pattern, not an incident.

| Signal | Likely cause | Action |
|---|---|---|
| High ratio with low compute | Small warehouses doing metadata-heavy work — `SHOW`/`DESCRIBE` loops, `INFORMATION_SCHEMA` polling | Cache the metadata in the caller; this is the common cause |
| High ratio with heavy DDL | Frequent clones, or a job recreating objects each run | Make the job idempotent rather than recreating |
| Ratio climbing with cloning | Zero-copy clones are cheap to make and not free to track | Review clone lifecycle in the storage hygiene lever |

### A warehouse is burning idle credits

**Rules.** `warehouse.idle_share_sustained` (P3) — `wh.idle_pct` above 60% for seven
consecutive days — and `warehouse.zombie_credits` (P3) — `wh.zombie_credits` above 5
for two consecutive weeks. The first is a warehouse doing some work inefficiently;
the second is a warehouse doing none at all.

**Diagnose.**

```bash
curl -s "$APP_URL/api/v1/metrics/wh.autosuspend_seconds/tile" | jq '.value'
curl -s -X POST "$APP_URL/api/v1/metrics/query" -H 'content-type: application/json' \
  -d '{"metrics":["wh.idle_pct","wh.query_count"],"dimensions":["warehouse"],"limit":25}' | jq '.rows'
```

| Signal | Action |
|---|---|
| Long auto-suspend, steady query volume | Tune auto-suspend. The lever models the saving as measured suspend gap × credit rate × frequency |
| No queries at all for the window | A zombie. Confirm nobody owns it, then suspend it — a proposal with an owner and a rollback statement, never an unannounced change (R8) |
| Idle high only in business hours | A keep-warm pattern someone chose deliberately | Record the decision; it is a cost trade, not a defect |

**Never** hard-suspend a production warehouse from a resource monitor to fix this
(§27.8). Production monitors are notify-only.

### A warehouse is queueing

**Rule.** `warehouse.queue_overload` (P2) — `wh.queue_overload_pct` above 15% for two
consecutive days.

**Diagnose.** Queueing is either not enough warehouse or too much work:

```bash
curl -s -X POST "$APP_URL/api/v1/metrics/query" -H 'content-type: application/json' \
  -d '{"metrics":["wh.queue_overload_pct","wh.utilisation_pct","wh.max_clusters"],
       "dimensions":["warehouse"],"limit":25}' | jq '.rows'
```

| Signal | Action |
|---|---|
| Queue high, utilisation high | Genuinely undersized. Size up, or raise `MAX_CLUSTER_COUNT` if the load is concurrent rather than heavy |
| Queue high, utilisation low | Concurrency limit, not capacity — look at `MAX_CONCURRENCY_LEVEL` and at long-running statements holding slots |
| Queue spikes at one hour | Scheduling collision. The scheduling-consolidation lever ranks co-schedulable jobs |
| `max_clusters` is 1 | Multi-cluster is off; a single burst cannot scale out |

The right-sizing quadrant on the engineering deep-dive plots exactly this pair, so
prefer it to a table when two or more warehouses are involved.

### Query failures are elevated

**Rule.** `query.failure_rate_elevated` (P2) — `q.failure_rate` above 5% for two
consecutive days.

**Diagnose.** Failures cluster; find the cluster before reading any individual
query:

```bash
curl -s -X POST "$APP_URL/api/v1/metrics/query" -H 'content-type: application/json' \
  -d '{"metrics":["q.failure_rate","q.volume"],"dimensions":["error_class","warehouse"],"limit":25}' | jq '.rows'
```

| Error class | Meaning | Action |
|---|---|---|
| Timeout | Statement timeout hit | Either the query regressed (see the next section) or the timeout is too tight for the workload class |
| Permission | A grant changed | Check the privilege-drift diff on the security page; a revoke is the usual cause |
| Syntax / object not found | A deploy landed against a schema that had not migrated | Roll the deploy, not the warehouse |
| Resource | Out of memory, or a spill that could not complete | Size up for that job, or fix the query — the spill leaderboard names it |

A failure rate driven by one retrying job is a single defect, not a platform
problem: check `q.volume` for the same slice before escalating.

### Query performance has regressed

**Rules.** `query.p95_latency_regression` (P3) — `q.p95_elapsed_ms` up more than 50%
week over week — and `query.remote_spill_sustained` (P3) — `q.spill_remote_bytes`
above 100 GiB on three consecutive days.

Remote spill is the expensive kind: the working set did not fit in local SSD
either, so the query is paying for object-storage round trips inside the plan.

**Diagnose.**

```bash
curl -s -X POST "$APP_URL/api/v1/metrics/query" -H 'content-type: application/json' \
  -d '{"metrics":["q.p95_elapsed_ms","q.pruning_efficiency","q.spill_remote_bytes"],
       "dimensions":["fingerprint"],"limit":20}' | jq '.rows'
```

| Signal | Action |
|---|---|
| Pruning efficiency collapsed on one fingerprint | The filter stopped matching the clustering key — usually a cast or a function wrapped around the partition column |
| Spill with unchanged pruning | Data volume grew past the warehouse size. Size up for that workload, or split the job |
| p95 up, p50 flat | A tail problem: one slice of the workload regressed, not all of it. Slice by team and by warehouse before touching the warehouse |
| Both up across every fingerprint | Concurrency, not the queries — check the queueing section |

### Storage is growing faster than expected

**Rule.** `storage.growth_sustained` (P4) — `storage.growth_rate` above 5% a day for
three consecutive days.

**Diagnose.**

```bash
curl -s -X POST "$APP_URL/api/v1/metrics/query" -H 'content-type: application/json' \
  -d '{"metrics":["storage.growth_rate","storage.active_bytes"],"dimensions":["database"],"limit":25}' | jq '.rows'
```

| Signal | Action |
|---|---|
| One database, steady growth | Expected ingestion. Check it against the retention policy rather than the alert |
| Growth without matching active bytes | Time Travel and Fail-safe. Review `storage.time_travel_ratio` and the retention on the largest tables |
| Step change | A backfill, a clone, or a table rebuilt rather than merged | Confirm the clone group on the storage page; an orphan clone retains its whole base |

Time Travel and Fail-safe sizing come from a storage snapshot that carries no time
dimension, so they are reported on the storage page rather than alerted on — there
is no series to compare windows of.

### A pipeline is failing or late

**Rules.** `pipeline.root_failures` (P2, fires on the first occurrence),
`pipeline.dt_lag_breaches` (P2, two consecutive days), and
`quality.freshness_sla_miss` (P2, two consecutive days). They share a section
because they share a first question: *is anything downstream serving stale data
as though it were fresh?*

**Diagnose.**

```bash
curl -s -X POST "$APP_URL/api/v1/metrics/query" -H 'content-type: application/json' \
  -d '{"metrics":["pipe.root_failures","pipe.skipped_downstream"],"dimensions":["graph_root","error_class"],"limit":25}' | jq '.rows'
curl -s "$APP_URL/api/v1/metrics/dq.freshness_sla_attainment/tile" | jq '{value, as_of, latency_floor_minutes}'
```

| Signal | Meaning | Action |
|---|---|---|
| Root failure with skipped downstream | Working as designed: the graph refused to run on stale inputs | Fix the root task; the skips resolve themselves |
| Root failure with downstream still running | The dependency is not declared | A modelling defect — declare it, then re-run |
| DT lag breached, refresh succeeding | `TARGET_LAG` is tighter than the refresh can achieve | Either relax the target or size the refresh warehouse; do not leave a target nobody meets |
| DT lag breached, refreshes failing | Same failure as a task failure | Read the refresh error on the platform health page |
| Freshness attainment falling with no failures | Refreshes are completing, just late | Look at queueing on the refresh warehouse before anything else |

`pipe.repeat_failure_tasks` is the escalation signal: a task that fails, is fixed,
and fails again is a design problem rather than an incident.

### Failed logins have spiked

**Rule.** `security.failed_login_spike` (P1) — robust z-score of `sec.failed_logins`.

**Treat it as a security event until proven otherwise.** The scored condition
exists because normal failure rates differ by an order of magnitude between
accounts, so a spike means "unusual *for this account*".

**Diagnose.**

```bash
curl -s -X POST "$APP_URL/api/v1/metrics/query" -H 'content-type: application/json' \
  -d '{"metrics":["sec.failed_logins"],"dimensions":["user","client_ip","error_class"],"limit":50}' | jq '.rows'
```

| Signal | Likely cause | Action |
|---|---|---|
| One user, one IP, many attempts | An expired credential in an automated job | Rotate it. Check the job did not log the password |
| Many users, one IP | Credential stuffing | Escalate to security now; network-policy the source |
| One user, many IPs | A distributed attempt, or a widely deployed client with a stale secret | Check the client type spread before deciding which |
| Spread across users and IPs after a deploy | A client library upgrade changed the auth path | Correlate with `sec.single_factor_logins` and the deploy time |

Cross-check `sec.privileged_grants` for the same window: a failed-login spike that
precedes a new privileged grant is a different conversation.

### A privileged grant appeared

**Rule.** `security.privileged_grant_created` (P2) — a live grant of `ACCOUNTADMIN`,
`SECURITYADMIN`, or `ORGADMIN`.

These roles can change billing, read every object, and rewrite the access model.
Every grant of one should be expected and short-lived; this alert is how you find
out it was neither.

**Diagnose.**

```bash
curl -s -X POST "$APP_URL/api/v1/metrics/query" -H 'content-type: application/json' \
  -d '{"metrics":["sec.privileged_grants"],"dimensions":["role","grantee","granted_by"],"limit":50}' | jq '.rows'
```

| Signal | Action |
|---|---|
| Grantee is a person, with a change record | Confirm the expiry. A standing ACCOUNTADMIN grant to a person is a finding |
| Grantee is a service user | Almost always wrong. Service users need a scoped role, not an admin role |
| Granted by an unexpected actor | Escalate. Check the privilege-drift diff for what else changed in the same window |
| Grantee is disabled | `sec.disabled_but_granted_users` counts these — a disabled identity holding a live admin grant is a re-enable away from being live |

The platform never revokes anything itself: it reports, and a human disposes (R8).

### AI services credits have jumped

**Rule.** `ai.credit_jump` (P3) — `ai.total_credits` more than doubled week over week.

AI spend grows by adoption, so the useful question is *which function and which
model*, not whether a ceiling was crossed.

**Diagnose.**

```bash
curl -s -X POST "$APP_URL/api/v1/metrics/query" -H 'content-type: application/json' \
  -d '{"metrics":["ai.total_credits","ai.tokens_per_credit"],"dimensions":["ai_function","ai_model"],"limit":25}' | jq '.rows'
```

| Signal | Action |
|---|---|
| One function, new this week | An adoption event. Confirm it is intended and budgeted, then leave it |
| Same function, tokens per credit falling | A model change made the same work more expensive | Compare model choice against the task; the cheapest capable model is usually not the one someone reached for first |
| Growth in an embedding or search workload | Often a re-index loop re-embedding unchanged rows | Check for idempotency before adding budget |


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

### What is wired up

**The declared rule set** lives in
[`config/alert_rules.yaml`](../config/alert_rules.yaml): 18 rules spanning the four
tiers and all nine KPI domains, each naming a metric, a condition, a scope, a
window, a persistence count, a tier, a route, and a runbook link.
`snowobs_analytics.rules.load_rule_set` parses and cross-validates it, and refuses
to load a rule set where any of these is true:

| Refusal | Why |
|---|---|
| The metric is not in the semantic layer | The rule would never fire and nobody would notice (R1) |
| A `scope` key is not a dimension of that metric | The rule would evaluate something other than what it claims |
| The metric sits on a snapshot entity | A rule compares windows; a snapshot has none |
| A `route` names an undeclared channel | The rule would fire into nothing |
| A `threshold` is an unquoted YAML number | It would reach `Decimal` already rounded (§27.7) |
| An `anomaly` condition declares a non-daily window | The detector scores a daily series (§11.2) |
| The `runbook` link is missing or malformed | §27.10 |

**Runbook links are asserted, not trusted.** `runbook_problems()` parses this
file's headings — skipping fenced code blocks, so a `# comment` in a shell block is
not mistaken for a section — and a test fails the build if any rule points at an
anchor that does not exist. That is why every rule above has a section under
[Alert conditions](#alert-conditions).

**Scheduled evaluation** runs in the worker as `evaluate_alert_rules`, registered
in `WorkerSettings.functions` alongside `ping` and on a cron schedule derived from
`ALERTING__EVALUATION_INTERVAL_MINUTES` (hourly by default, at seven minutes past).
Each run:

1. loads the rule set and, for each enabled rule, queries its metric through the
   semantic compiler and the configured engine — never hand-written SQL, so LIVE
   and OFFLINE evaluate the same definition (R1);
2. skips any rule whose sources have not landed, logging the reason. A metric the
   platform cannot compute produces **no alert**, never a firing on assumed zeros
   (R3). The job result counts skips separately from non-firings, so "nothing
   fired" and "nothing could be evaluated" do not look identical;
3. replays the last `persistence` windows of the series into the engine, so the
   streak that fires a rule is the one visible in the data rather than one
   accumulated by however many times the job happened to run;
4. dispatches whatever fires to the channels its route names, filtered by tier.

**Channels** are `snowobs_analytics.channels`: a `WebhookChannel` (Slack blocks or
a Teams `MessageCard`), an `EmailChannel` (SMTP; Amazon SES is configured as the
relay in AWS deployments), and a `NullChannel` used when nothing is configured —
which still logs the full payload, so a deployment without a webhook keeps a record
of what would have been sent. Webhook URLs and SMTP passwords are held as secret
*references* and resolved through the secrets adapter at the moment of dispatch;
neither the value nor the endpoint ever reaches a log line (§17).

**Nothing is sent until `ALERTING__ENABLED=true`.** Until then every rule still
loads, evaluates, and backtests, and firings are logged through the null channel.
Validate the rule set first; let it page people second.

**The API** is `/api/v1/alerts`:

```bash
curl -s "$APP_URL/api/v1/alerts/rules" | jq '{rule_count, domains, dispatch_enabled}'
curl -s "$APP_URL/api/v1/alerts/rules/warehouse.queue_overload" | jq .
curl -s "$APP_URL/api/v1/alerts/prune-proposals" | jq '.proposals'
curl -s -X POST "$APP_URL/api/v1/alerts/rules/cost.unattributed_share_high/backtest" | jq '.summary'
curl -s "$APP_URL/api/v1/alerts/export/ddl?rule_id=pipeline.root_failures" | jq -r '.ddl'
```

There is deliberately no endpoint that creates or edits a rule. An alert rule
decides what wakes somebody at 03:00; it belongs in version control and code
review, not in a form.

### What still does not exist

- **PagerDuty and ServiceNow/Jira ticket creation are not implemented.** §14 lists
  them as channels. What ships is webhook and email. A P1's `page` route and a P2's
  `ticket` route are carried on the tier and shown in the API, but the delivery for
  both is currently a webhook or an email — most PagerDuty and ServiceNow
  deployments accept an inbound webhook, so the shipped `WebhookChannel` reaches
  them, but there is no native integration, no incident deduplication against
  PagerDuty's own key, and no ticket lifecycle. Do not read a P1 route as "somebody
  has been paged" without checking where that channel actually points.
- **Alert state is per process.** The dedup ledger and the per-rule statistics live
  in the process that evaluated the rule; there is no Postgres event store yet.
  A worker restart clears open alerts (the next run re-fires anything still
  breaching, which is the safe direction), and the statistics the API reports are
  the API process's own — which, for a rule only the worker evaluates, means zero.
  See [`ASSUMPTIONS.md`](ASSUMPTIONS.md) A-24.
- **Nothing records that a human acted.** `AlertEngine.acknowledge` exists and
  drives the pruning proposal, but no endpoint calls it, because an acknowledgement
  that only one process can see would be worse than none. Until the event store
  lands, pruning proposals will not appear on their own; the backtest endpoint is
  the practical way to find a rule that fires too often.
- **Guardrail management** — drafting and applying resource monitors, statement
  timeouts by workload class, auto-suspend policy, and Snowflake budgets (§14) — is
  not built. When it is, production monitors are notify-only plus a P1: nothing in
  this platform hard-suspends a production warehouse (§27.8).

The seven CloudWatch alarms above remain the infrastructure-level alerting and are
unaffected by any of this.
