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

### Added — Phase 3 (Allocation, chargeback, and the reconciliation gate)

- **Allocation waterfall** (`packages/finops/allocation.py`): query-tag → role →
  user → warehouse-default → unattributed, with cost split into its three real
  components — direct attributed compute, a share of the warehouse's idle time,
  and a share of the account's billed cloud services. Apportionment uses the
  largest-remainder method so the parts sum to the whole exactly, with no
  rounding residue left in an "other" bucket.
- **Reconciliation gate** (`packages/finops/reconciliation.py`): allocated
  credits are compared daily against `METERING_DAILY_HISTORY` within a 0.5%
  tolerance, and chargeback figures are withheld entirely when it fails (R6).
  A red gate produces a banner and an empty team table, never a quietly wrong
  one.
- **Chargeback API** (`/api/v1/chargeback/allocation`, `/reconciliation/{date}`)
  carrying the gate's verdict, the unattributed share, and the SQL behind every
  constituent query.
- **Dashboards** (`apps/web`): executive cost, platform health, team chargeback,
  and coverage. Money is handled as fixed-point decimal strings in the browser
  too — `lib/decimal.ts` does BigInt arithmetic rather than `parseFloat` (§27.7).
  Every tile, chart, and table closes with a provenance strip: as-of timestamp,
  freshness floor, sources, and a "show the SQL" disclosure (R5).

### Added — Phase 4 (LIVE mode)

- **Key-pair connection** (`packages/snowflake_live/connection.py`) with the
  private key read from the secrets adapter, never from the database or a log.
- **Grants probe** (`grant_probe.py`): reports exactly which of the granular
  `SNOWFLAKE` database roles are missing and emits the remediation SQL to fix
  each one — never blanket `IMPORTED PRIVILEGES` (R4).
- **Provisioning SQL** generated from the source registry, so the grants an
  operator runs cannot drift from the views the application actually reads.
- **Pushdown engine** (`engine.py`): the same compiled semantic SQL, executed in
  Snowflake with a pinned warehouse, a statement timeout, and the same SQL guard
  the OFFLINE path uses.

### Added — Phase 5 (Domains D4–D9, forecasting, anomaly detection, levers)

- **92 KPIs across nine domains** — storage, pipelines, data quality, security,
  AI/Cortex, and chargeback join cost, warehouse, and query. Nine new entities.
  Every metric executes in both dialects and matches exactly; no new parity
  tolerances were needed.
- **Forecasting** (`packages/analytics/forecast.py`): explicit trend and
  weekly-seasonality decomposition with Theil–Sen robust regression — no
  black-box dependency, and the method is stated next to the number.
- **Anomaly detection** (`anomaly.py`): MAD-based robust z-scores, plus
  `explain_delta`, a greedy contribution decomposition that attributes a change
  to the members of a dimension deterministically.
- **Optimisation levers** (`levers.py`, `savings.py`): right-sizing,
  auto-suspend, and idle-reduction recommendations, each with a dollar impact
  and the assumption behind it.
- **Alerting** (`alerting.py`): rules with severity, suppression, and a runbook
  link — a rule without one does not ship.

### Added — Phase 6 (The agentic layer)

- **In-house tool-use runtime** (`packages/agents/runtime/supervisor.py`): a
  thin, auditable loop — no framework, so every tool call is inspectable in the
  trace (§27.12). Seven specialists, each given only the tools its role needs.
- **Text-to-metric, not text-to-SQL**: agents choose governed metrics; the SQL
  is compiled by the semantic layer and passes the guard like everything else.
- **Grounding enforcement (R12)**: an answer containing a figure no tool
  returned is withheld, not shown with a caveat. The discarded draft is kept on
  the trace for review, and the refusal does not repeat the invented number back
  to the reader.
- **Injection defence (§12.5)**: tool output re-enters the model fenced as data,
  instruction-shaped text is neutralised, and the fence cannot be closed from
  inside it. Five adversarial fixtures in the golden set prove it.
- **Deterministic mode (§19)**: with no LLM key the platform still answers —
  questions route to governed metrics, coverage, and the catalogue by keyword
  and synonym matching, and causal questions get a real contribution
  decomposition over windows derived from the question's own words. It says
  plainly that narration is disabled rather than pretending to be unavailable.
- **Eval harness** (`packages/agents/evals/`): 76 golden questions across nine
  domains and nine categories. `make eval` gates on tool selection ≥ 90%,
  numeric correctness 100%, **zero** fabricated figures, and **zero** injections
  obeyed. Categories the deterministic path cannot be held to are reported as
  unassertable rather than scored as passes.

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

### Added — Phase 8 (Deployment, hardening, documentation)

- **`make demo`**: `git clone` to a populated application in one command, with
  no Snowflake account, no cloud credentials, and no LLM key. An all-in-one
  image serves the API, worker, and SPA on one port over a synthetic account
  ingested through the real OFFLINE path.
- **Terraform** (`deploy/terraform/`): network, security, data, platform,
  compute, edge, observability, and CI modules for a private AWS deployment,
  wired up for dev and prod. Rollback is documented and tested.
- **`scripts/doctor.py`** (`make doctor`): pre-flight check of ports, Docker
  resources, and configuration before a first run.
- **`security.yml`**: scheduled dependency, secret-history, CodeQL, and image
  scanning — for the CVE published after the code merged, which the per-commit
  gate cannot catch.
- **`release.yml`**: re-runs the full merge gate against the tagged commit,
  publishes a multi-architecture image with build provenance attested, and
  attaches the CycloneDX SBOM to the release.
- **Documentation set**: `ARCHITECTURE.md`, `SECURITY.md`, `RUNBOOK.md`,
  `DEMO.md`, `AWS_COST.md`, and a `USER_GUIDE.md` written for the FinOps analyst
  rather than the engineer.

### Added — Phase 8b (Alerting, wired up)

- **A declared rule set** (`config/alert_rules.yaml`): 18 rules spanning §14's four
  tiers and all nine KPI domains, each naming a metric, a condition (threshold,
  delta, or anomaly score), a scope, an evaluation window, a persistence count, a
  tier, a route, and a runbook URL.
- **Rule loader and validator** (`snowobs_analytics.rules`): refuses a rule whose
  metric the semantic layer does not define, whose scope names a dimension the
  metric cannot be sliced by, whose metric sits on a snapshot entity with no
  windows to compare, whose route names an undeclared channel, whose threshold is
  an unquoted YAML float (§27.7), or whose anomaly condition declares a non-daily
  window. `runbook_problems()` parses `docs/RUNBOOK.md`'s headings — skipping
  fenced code blocks — and a test fails the build on any rule pointing at an
  anchor that does not exist (§27.10).
- **Notification channels** (`snowobs_analytics.channels`): an adapter protocol
  with a `WebhookChannel` (Slack blocks / Teams `MessageCard`), an `EmailChannel`
  (SMTP; SES as the relay on AWS), and a `NullChannel` that still records the
  firing when nothing is configured. Payloads carry KPI, value, threshold, scope,
  and runbook link — and **cannot** be constructed carrying query text (§14),
  which is asserted rather than reviewed.
- **Secrets adapter** (`snowobs_common.secrets`): env, file, and AWS Secrets
  Manager providers behind one protocol. Webhook URLs and SMTP passwords are held
  as references and resolved at the moment of dispatch; a failed resolution names
  the reference and never the value (§17).
- **Scheduled evaluation** (`snowobs_worker.alerts.evaluate_alert_rules`):
  registered in the worker's job registry alongside `ping` and on a cron schedule
  derived from `ALERTING__EVALUATION_INTERVAL_MINUTES`. It queries each rule's
  metric through the semantic compiler and the configured engine — never
  hand-written SQL — applies persistence from the data rather than from process
  memory, dedups, and dispatches. A metric that cannot be computed produces a
  logged reason and no alert, never a firing on assumed zeros (R3).
- **`/api/v1/alerts`**: rule list with per-rule fire/action/suppression statistics
  and the channels each rule's tier actually reaches, single-rule fetch, pruning
  proposals, an isolated backtest (rule + window → what it would have fired, with
  the SQL it replayed), and the OFFLINE `CREATE ALERT` DDL export. There is
  deliberately no endpoint that writes a rule.
- **`docs/RUNBOOK.md`**: a new "Alert conditions" section with one entry per
  declared rule — symptom, diagnosis commands against real endpoints, a
  signal/action table, and what *not* to do. The alert-engine section now
  describes what exists and states plainly what does not (A-24, A-25).
- New settings: `ALERTING__*` and `SECRETS__FILE_PATH` (§21).

### Added — Phase 9 (Organization-wide observability foundations)

- **Multi-account fixture generation** (`fixtures/generator`, new
  `snowobs_fixtures.organization`): an `OrganizationConfig` of `AccountProfile`s
  produces a fleet of Snowflake accounts under one organization through the
  *same* `generate()` call as before. The shipped fleet is four accounts with
  genuinely different characters — a large EU production account, a US
  analytics account whose spend compounds, a poorly-tagged GCP sandbox, and a
  Business-Critical Azure APAC account — across three clouds and four regions.
  `generate(GeneratorConfig(...))` is unchanged in signature, return type, and
  output; the new profile knobs (`scale_factor`, `compute_growth_per_day`,
  `workload_mix`, `untagged_warehouses`) all default to neutral.
- **All seven ORGANIZATION_USAGE views generated, and derived from the
  accounts.** `WAREHOUSE_METERING_HISTORY` (org) and `STORAGE_DAILY_HISTORY`
  are computed from each account's own ACCOUNT_USAGE rows, so the roll-up
  reconciles to the accounts *exactly* — asserted to the cent in Decimal, never
  a tolerance. `USAGE_IN_CURRENCY_DAILY` is priced off `RATE_SHEET_DAILY`
  (`USAGE x EFFECTIVE_RATE`, to the cent), and `CONTRACT_ITEMS` /
  `REMAINING_BALANCE_DAILY` are a real drawdown of a real commitment: free
  usage, then rollover, then capacity, with the four balances tying to
  commitment-minus-cumulative-spend every single day (A-30).
- **Five organization-wide planted phenomena** in `ground_truth.py`, each with
  subjects, a window, and a test: a runaway account, a stranded capacity
  commitment, cross-cloud egress concentrated on one account, an account with
  materially worse tagging discipline, and an account paying a worse effective
  rate than its peers.
- **`write_organization_csv`** writes one directory per account plus one
  directory for the organization-scoped extracts, mirroring how an enterprise
  exports; `snowobs-generate --organization` drives it from the CLI.
- **Account-aware ingestion**: `_ACCOUNT` joins `_LOADED_AT`, `_SOURCE_VIEW`,
  and `_BATCH_ID` as ingest metadata. `IngestPipeline` / `LakeWriter` take an
  `account`, `ingest_directory(dir, account=...)` stamps every row of the
  batch, and an undeclared account lands as NULL rather than a guess (A-29).
- **`scope: account | organization`** on `SourceDefinition`, set for the seven
  ORGANIZATION_USAGE sources. Account-scoped sources now deduplicate on their
  declared grain **plus** the account stamp — without it, two accounts' extracts
  collide on `QUERY_ID` or `(WAREHOUSE_ID, START_TIME)` and one account's entire
  history disappears behind last-write-wins with no error (A-31).
- **Per-account coverage**: `DuckDBCatalog.accounts()` / `accounts_for()` /
  `stats(source, account)`, and `SourceCoverage.accounts` plus
  `CoverageMatrix.account_matrix(account)` — so "account X has query history
  landed, account Y only has billing" is a reportable answer. The existing
  single-account shape is unchanged, with an empty per-account list.
- **The `account` dimension on every entity.** All nineteen account-bearing
  entities now project an account and declare an `account` dimension, so every
  KPI in the catalogue can be read at organization level or narrowed to one
  account. Account-scoped entities take it from the `ACCOUNT_OF()` shim — the
  ingest stamp OFFLINE, the connected account LIVE; organization-scoped
  entities keep the real `ACCOUNT_NAME` the view publishes. `dim_contract_item`
  and `fact_commitment_balance_daily` deliberately have none: a contract belongs
  to the organization, and the compiler skips the account predicate for them
  rather than labelling an organization figure with an account's name (R3).
- **Six organization entities**: `fact_org_warehouse_metering_hourly`,
  `fact_org_storage_daily`, `fact_org_data_transfer_daily`,
  `fact_rate_sheet_daily`, `dim_contract_item`, and
  `fact_commitment_balance_daily` — covering the six ORGANIZATION_USAGE views
  that had no entity, joining the contract total onto the daily balance so the
  burn-down can be stated as a share rather than as bare dollars.
- **D10 — Organization & multi-account**: 16 KPIs. Organization spend and spend
  by account, an account's share of the bill, egress cost and egress volume, the
  org-roll-up control total and compute/cloud-services credits per account,
  organization storage in bytes and credits, the effective rate per credit and
  the premium an account pays against the fleet, the contracted amount, and the
  commitment triple — remaining, consumed share, and runway in days. The
  catalogue is now **108 KPIs across ten domains**; every one of them is
  parity-tested in both dialects with no new tolerance.
- **Account-aware joins and windows inside account-scoped entities.** Warehouse
  names, user names, task names, and dates all repeat across a fleet, so
  `fact_warehouse_metering_hourly`, `fact_storage_daily`, `fact_grant`,
  `fact_ai_usage_daily`, `fact_task_run`, `fact_serverless_daily`,
  `fact_warehouse_load_hourly`, and `dim_user` now carry the account in their
  join keys, group keys, and window partitions. Without it a four-account lake
  multiplied one account's credits by four, and one account's task failures were
  counted as another's — silently, and only in a multi-account lake (A-33). A
  single-account lake is bit-for-bit unaffected.

### Fixed

- **The generator was not deterministic across processes.** `QUERY_HISTORY`'s
  `SESSION_ID` was derived from Python's `hash()`, which is randomised per
  process, so the same seed produced different output on every run. It is now a
  SHA-256 digest of the query id; every other generated file is byte-for-byte
  unchanged, and pinned digests now guard the whole single-account extract
  against silent drift (A-32).

### Fixed — found by running the software, not by reading it

- **LIVE mode was unreachable.** `packages/snowflake_live` was complete and
  tested, but every service compiled `Dialect.DUCKDB` and constructed a
  `DuckDBEngine` directly, so a deployment configured for LIVE served
  dashboards, chargeback, and agent answers from uploaded extracts without
  saying so. Engine selection now happens in one module; `mode=live` with an
  unusable connection refuses rather than falling back; and a test forbids any
  other service from naming an engine.
- **The LLM API-key path was broken in three places at once** — a settings
  field that did not exist, a service that never passed a key, and an image
  without the SDKs installed. The key is now held by reference and resolved
  through the secrets adapter at the moment of use, and `build_provider` keeps
  the promise in its own docstring that a missing key degrades rather than
  fails.
- **`/api/v1/chargeback/allocation` required explicit dates** while every metric
  endpoint defaults them — the demo's own smoke test hit a 422. It now
  allocates the landed window in OFFLINE and the last 30 complete days in LIVE,
  and reports which it used.
- **`Settings(mode=…)` silently discarded its argument.** Fields carrying a
  validation alias could only be set by that alias, so code and tests
  constructing settings by name were operating on defaults.
- **`DATE_DIFF_DAYS` transposed its arguments in the Snowflake dialect**,
  producing a day count from the wrong pair with nothing raised. A permanent
  guard now fails any shim whose rendering changes meaning when re-parsed.
- **Deterministic routing answered the wrong question three ways** — a word
  appearing anywhere in a metric's identity counted as much as one naming its
  subject. Matches are now weighted by where they land, and a breakdown metric
  is not offered when the question asked for no breakdown.
- **Two documents stated figures that had gone stale** — the parity snapshot
  count and the worker alarm's description of what it protects. The first is
  now asserted by a test.

### Added — Phase 9b (Reading the catalogue at organization or account scope)

- **A scope filter beside the time range.** `GET /api/v1/metrics/scopes` lists
  the organization and every account with landed data, each with a count of how
  many of the 108 KPIs it can answer — a sandbox that has only had its billing
  uploaded visibly narrows the catalogue rather than returning blanks. The
  choice is global, in the URL, and part of every query key, so switching
  accounts cannot leave the previous account's figure on screen under the new
  account's name.
- **One scope verdict, four callers.** `snowobs_semantics.scope.assess` decides
  whether a metric can answer at a scope and why not when it cannot; the tiles,
  the metric query endpoint, chargeback, and the agents all call it. A metric
  the UI refuses to scope therefore cannot be scoped by asking an agent
  instead. Two refusals are distinguished: a metric that *describes the
  organization* (a contract has no per-account value) and one that *cannot be
  narrowed* (its source records no account) — each stated with its remedy (R3).
- **Scoped chargeback.** `/api/v1/chargeback/allocation` takes `scope` and
  `account`. The allocation, the cloud-services apportionment, and the metered
  total the reconciliation gate checks against are scoped together — checking
  one account's allocation against the organization's bill would report a
  variance of most of the fleet and block a figure that is correct. An account
  with no landed inputs is refused by name: an empty waterfall reconciles
  perfectly against an empty bill, and R6 must not go green over a chargeback
  of nothing.
- **Agents can be asked about one account.** `query_metric` takes an `account`;
  a new `list_accounts` tool reports the fleet and any account whose detail has
  not landed; every tool result carries the scope, the contributing accounts,
  and the missing ones. With no LLM configured the deterministic path scopes to
  an account the question names — one account, never two, because "compare PROD
  and SANDBOX" is not a request to answer for either.
- **`make demo` seeds the whole organization** — four accounts uploaded
  separately, as an enterprise's extracts actually arrive, plus the
  organization-wide export. All 108 KPIs populate, 104 of them at account scope.

### Fixed — Phase 9b

- **"Partial roll-up" meant "a roll-up happened".** Every organization figure
  carried the warning, so the warning meant nothing. It now means the platform
  can *name* an account that is missing: the account roster comes from billing,
  which knows every account whether or not its detail was ever uploaded, and
  the difference against what landed is reported as `missing_accounts`.
- **The scope endpoint took 208 seconds.** It re-opened the engine and
  re-registered the catalog once per metric per scope — around 540 lake scans.
  Memoised for the life of the request: 0.88s cold, 0.40s warm.
- **Coverage counted the organization as one of its own accounts.** An
  `ORGANIZATION_USAGE` extract is stamped like any other, so `ACME_GROUP`
  appeared in the coverage matrix beside its four members while the scope
  picker excluded it, and the UI had to work around the disagreement.
  `catalog.accounts()` now skips organization-scoped sources, so both read one
  definition of the fleet; an account's coverage matrix omits organization
  sources rather than reporting every account as missing one none of them owns.
- **"Which accounts do we have?" was answered with a number.** It matched an
  organization metric on the word "accounts" and returned a figure for a
  question that asked for a list. Fleet questions now reach `list_accounts`,
  and "which account spends the most?" slices by account instead of returning
  the fleet's total under a question that named no account.

### Deferred (recorded, not stubbed)

- Everything deferred in earlier phases has landed. The remaining limitations
  are recorded as numbered assumptions in `docs/ASSUMPTIONS.md`, each with its
  rationale and the trigger that should prompt a revisit — notably the sources
  the fixture generator does not land (`remaining_balance_daily`, `TABLES`),
  which make two metrics read a documented proxy rather than the ideal column.
- Alerting ships webhook and email channels; **PagerDuty and ServiceNow/Jira
  ticket creation are not implemented**, and neither is §14's guardrail
  management (resource monitors, statement timeouts, auto-suspend policy,
  budgets) — both recorded as A-25. Alert dedup state and per-rule statistics
  are per-process, and no endpoint records that a human acted on a firing (A-24).
- **An `ACCOUNT_USAGE` metric has no organization-wide figure in LIVE mode**
  (A-35). `ORGANIZATION_USAGE` metrics answer organization-wide in both modes,
  and OFFLINE rolls up every landed account; LIVE would need one query per
  account and a merge, and averaging a rate or a percentile across accounts is
  wrong. The scope selector declines with that reason and names the two routes
  that do work, rather than shipping a merge that is right for additive
  measures and quietly wrong for the rest.
