# Assumptions & verified facts

Verification of BUILD_PROMPT §25, performed **2026-08-24** by web-searching current
official documentation (docs.snowflake.com, duckdb.org, platform.claude.com, PyPI,
GitHub releases). Facts below are grouped as **Verified** (with source), **Could not
confirm** (must be re-checked before the dependent code ships), and **Assumptions**
(each with rationale and a revisit trigger).

Rule R7 applies: latencies and view facts below are recorded here and belong in the
source registry YAML (`packages/semantics/sources/`), never hardcoded.

---

## 1. Verified — ACCOUNT_USAGE views, latencies, retention

Index: <https://docs.snowflake.com/en/sql-reference/account-usage>. Historical views
retain **365 days**; latencies are documented per-view maxima. All view names in
BUILD_PROMPT §7.1 were confirmed to exist. Key figures (per-view doc pages,
`https://docs.snowflake.com/en/sql-reference/account-usage/<view>`):

| View | Documented max latency |
|---|---|
| QUERY_HISTORY | 45 min |
| TASK_HISTORY | 45 min |
| **QUERY_ATTRIBUTION_HISTORY** | **8 h** (commonly misquoted as 6 h) |
| METERING_DAILY_HISTORY | 3 h |
| METERING_HISTORY | 3 h; `CREDITS_USED_CLOUD_SERVICES` column up to **6 h**; SNOWPIPE_STREAMING service-type credits up to **12 h** |
| WAREHOUSE_METERING_HISTORY | 3 h (cloud-services column 6 h) |
| WAREHOUSE_LOAD_HISTORY / WAREHOUSE_EVENTS_HISTORY | 3 h |
| ACCESS_HISTORY | 3 h — **Enterprise Edition or higher** |
| LOGIN_HISTORY | 2 h |
| SESSIONS | 3 h |
| COPY_HISTORY | 2 h; up to **2 days** for tables with <32 DML statements since last update |
| PIPE_USAGE_HISTORY, DYNAMIC_TABLE_REFRESH_HISTORY | 3 h |
| STORAGE_USAGE, STAGE_STORAGE_USAGE_HISTORY | 2 h |
| DATABASE_STORAGE_USAGE_HISTORY | 3 h |
| TABLE_STORAGE_METRICS | 90 min (current metrics, not a 365-day history view) |
| GRANTS_TO_ROLES / GRANTS_TO_USERS | 2 h (include dropped grants) |
| POLICY_REFERENCES, MASKING_POLICIES, ROW_ACCESS_POLICIES, USERS, ROLES | 2 h |
| OBJECT_DEPENDENCIES | 3 h |
| SERVERLESS_TASK_HISTORY, AUTOMATIC_CLUSTERING_HISTORY, SEARCH_OPTIMIZATION_HISTORY, MATERIALIZED_VIEW_REFRESH_HISTORY, REPLICATION_USAGE_HISTORY | 3 h |
| DATA_TRANSFER_HISTORY | 2 h |
| SNOWPIPE_STREAMING_CLIENT_HISTORY | 2 h (CHANNEL_HISTORY and FILE_MIGRATION_HISTORY views also exist) |

**Cortex/AI usage views (this area moved a lot in 2025–26):**
`CORTEX_FUNCTIONS_USAGE_HISTORY`, `CORTEX_FUNCTIONS_QUERY_USAGE_HISTORY`,
`CORTEX_ANALYST_USAGE_HISTORY`, `CORTEX_SEARCH_DAILY_USAGE_HISTORY`,
`CORTEX_SEARCH_SERVING_USAGE_HISTORY`, `CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY`,
`CORTEX_FINE_TUNING_USAGE_HISTORY`, `CORTEX_REST_API_USAGE_HISTORY` all exist. Newer:
**`CORTEX_AI_FUNCTIONS_USAGE_HISTORY`** (~5 min latency — prefer for near-real-time AI
cost), **`CORTEX_AISQL_USAGE_HISTORY`** (GA Dec 2025), **`CORTEX_AGENT_USAGE_HISTORY`**
(GA Feb 2026). `DOCUMENT_AI_USAGE_HISTORY` exists but **Document AI is documented as
decommissioned** — the source registry marks it `optional`/legacy and nothing new
depends on it.

**Newer views to exploit where present:** `QUERY_METERING_HISTORY` (per-query credits
on Adaptive Warehouses — QUERY_ATTRIBUTION_HISTORY excludes Adaptive Warehouse jobs),
`QUERY_INSIGHTS`, `AGGREGATE_QUERY_HISTORY`/`AGGREGATE_ACCESS_HISTORY`.
Pending change: BCR-2225 (2026_02 bundle, preview) adds columns to QUERY_HISTORY and
WAREHOUSE_EVENTS_HISTORY; BCR-1616 changed WAREHOUSE_EVENTS_HISTORY event semantics.

## 2. Verified — ORGANIZATION_USAGE

Index: <https://docs.snowflake.com/en/sql-reference/organization-usage>.

| View | Latency |
|---|---|
| USAGE_IN_CURRENCY_DAILY, REMAINING_BALANCE_DAILY | up to **72 h** |
| RATE_SHEET_DAILY, CONTRACT_ITEMS, WAREHOUSE_METERING_HISTORY (org), STORAGE_DAILY_HISTORY, DATA_TRANSFER_DAILY_HISTORY | up to **24 h** |
| METERING_DAILY_HISTORY (org) | up to 2 h |

Month-end restatement confirmed: billing views can change intramonth ("until month
close, data for a given day can change") for adjustments, contract amendments, and
account transfers (<https://docs.snowflake.com/en/user-guide/billing-reconcile>).
The `provisional` flag on currency metrics must therefore stay true until month close.
Access: ORGANIZATION_USAGE lives in the organization account (GLOBALORGADMIN; GA Jan
2025) or a regular account with ORGADMIN enabled; grantable roles
`ORGANIZATION_USAGE_VIEWER`, `ORGANIZATION_BILLING_VIEWER`, `ORGANIZATION_ACCOUNTS_VIEWER`.

## 3. Verified — granular SNOWFLAKE database roles (R4)

<https://docs.snowflake.com/en/sql-reference/snowflake-db-roles> confirms
**OBJECT_VIEWER, USAGE_VIEWER, GOVERNANCE_VIEWER, SECURITY_VIEWER** plus
READER_USAGE_VIEWER, the three ORGANIZATION_* viewer roles, CORE_VIEWER, CORTEX_USER,
BUDGET_CREATOR. Specifics needed by the provisioning script:

- QUERY_HISTORY + all METERING views → **USAGE_VIEWER**
- QUERY_ATTRIBUTION_HISTORY → **USAGE_VIEWER or GOVERNANCE_VIEWER** (per its page)
- ACCESS_HISTORY, TAG_REFERENCES, POLICY_REFERENCES, MASKING_POLICIES, ROW_ACCESS_POLICIES → **GOVERNANCE_VIEWER**
- LOGIN_HISTORY, SESSIONS → **SECURITY_VIEWER**
- Object metadata (TABLES, VIEWS, …) → **OBJECT_VIEWER**

## 4. Verified — cloud-services billing (reconciliation gate, R6)

<https://docs.snowflake.com/en/user-guide/cost-understanding-compute>:
cloud services are billed only for the portion exceeding **10% of daily (UTC)
warehouse compute usage**, computed daily; the adjustment never exceeds that day's
actual cloud-services usage. In **METERING_DAILY_HISTORY**:
`CREDITS_USED_CLOUD_SERVICES` is raw usage and `CREDITS_ADJUSTMENT_CLOUD_SERVICES` is
a **negative** rebate; billed = `credits_used_cloud_services +
credits_adjustment_cloud_services` (net reflected in `CREDITS_BILLED`).
**WAREHOUSE_METERING_HISTORY** shows raw per-warehouse cloud-services credits with
**no adjustment** — the adjustment is account-level/daily and cannot be attributed
per warehouse. The allocation engine therefore spreads the *net* account-level
cloud-services figure pro-rata to compute (HLD §10.2) rather than summing
warehouse-level cloud-services columns.

## 5. Verified — QUERY_ATTRIBUTION_HISTORY semantics (allocation engine)

`CREDITS_ATTRIBUTED_COMPUTE` covers warehouse compute including resize/autoscale
effects; **excludes warehouse idle time and cloud services** — confirming the HLD's
three-component model (direct + idle share + cloud-services share). Queries ≤ ~100 ms
are excluded; Adaptive Warehouse jobs are excluded (use QUERY_METERING_HISTORY).
`PARENT_QUERY_ID`/`ROOT_QUERY_ID` allow stored-procedure roll-ups. Columns fully
populated only from **mid-August 2024** onward.

## 6. Verified — Snowflake DDL for data-product publication (Phase 7)

- **CREATE SEMANTIC VIEW** — GA. Clause order enforced: `TABLES`, `RELATIONSHIPS`,
  `FACTS`, `DIMENSIONS`, `METRICS`, `COMMENT`, then **`AI_VERIFIED_QUERIES`** (not
  "VERIFIED_QUERIES"), `COPY GRANTS`. Synonyms via `WITH SYNONYMS = ('…')`.
  <https://docs.snowflake.com/en/sql-reference/sql/create-semantic-view>
- **CREATE CORTEX SEARCH SERVICE** — GA. Required: `ON <col>`, `WAREHOUSE`,
  `TARGET_LAG`; optional `ATTRIBUTES`, `EMBEDDING_MODEL`.
  <https://docs.snowflake.com/en/sql-reference/sql/create-cortex-search>
- **CREATE AGENT** — exists as SQL DDL (Cortex Agents GA Nov 2025, same day as
  Snowflake Intelligence): `CREATE AGENT <name> FROM SPECIFICATION $$<YAML ≤100 KB>$$`
  with `models`, `orchestration` budgets, `instructions`, `tools`
  (`cortex_analyst_text_to_sql`, `cortex_search`, `sql_exec`, `data_to_chart`,
  `generic`), `tool_resources`.
  <https://docs.snowflake.com/en/sql-reference/sql/create-agent>
- **Internal marketplace** — the DDL is **`CREATE ORGANIZATION LISTING`** (not plain
  `CREATE LISTING`; external listings use `CREATE EXTERNAL LISTING`). GA since Nov
  2024. YAML manifest: `title`, `organization_profile: INTERNAL`,
  `organization_targets.access`, `locations.access_regions`, `auto_fulfillment`.
  ORGADMIN/GLOBALORGADMIN provisioning required.
  <https://docs.snowflake.com/en/user-guide/collaboration/listings/organizational/org-listing-create>

### 6a. Re-verified at Phase 7 (2026-08-24) — exact clause syntax

Re-checked when writing the emitters in `packages/dataproducts/emitters/`. Note that
`docs.snowflake.com` is unreachable from the build environment's egress proxy, so these
were confirmed from indexed documentation search results rather than by fetching the
pages directly; the pages themselves remain authoritative.

- **`AI_VERIFIED_QUERIES`** sits **after `COMMENT` and before `COPY GRANTS`**, and each
  entry takes the form
  `<name> AS ( QUESTION '<question>' VERIFIED_AT <unix seconds> ONBOARDING_QUESTION <bool>
  VERIFIED_BY '(<purpose> = <contact>)' SQL '<query>' )`. Support for verified queries in
  semantic views shipped 2026-04-05.
  <https://docs.snowflake.com/en/user-guide/views-semantic/sql> ·
  <https://docs.snowflake.com/en/release-notes/2026/other/2026-04-05-semantic-views-verified-queries>
- **`CREATE ORGANIZATION LISTING`** —
  `CREATE ORGANIZATION LISTING [IF NOT EXISTS] <name> [SHARE <share> | APPLICATION PACKAGE <pkg>]
  AS '<yaml manifest>' [PUBLISH = TRUE|FALSE]`, or `FROM '<stage location>'` for a manifest on
  a stage. `REVIEW =` belongs to `CREATE EXTERNAL LISTING`, **not** to organization listings.
  Manifest: `title` (required, ≤110 chars), `description` (≤7500), `organization_profile`
  (defaults to `INTERNAL`), `organization_targets` (required — access list plus support and
  approver contacts), `locations` (optional), `auto_fulfillment`, plus optional `resources`,
  `listing_terms`, `data_dictionary`, `usage_examples`, `data_attributes`.
  <https://docs.snowflake.com/en/sql-reference/sql/create-organization-listing> ·
  <https://docs.snowflake.com/en/user-guide/collaboration/listings/organizational/org-listing-manifest-reference>
- **`CREATE AGENT`** —
  `CREATE [OR REPLACE] AGENT [IF NOT EXISTS] <name> [COMMENT = '<comment>']
  [PROFILE = '<profile object>'] FROM SPECIFICATION $$<spec>$$`. `COMMENT` precedes
  `PROFILE`. The warehouse is not a clause on the statement; it is named inside the
  spec's `tool_resources` for the tools that execute SQL.
  <https://docs.snowflake.com/en/sql-reference/sql/create-agent>

This closes the `AI_VERIFIED_QUERIES`, `CREATE ORGANIZATION LISTING`, and `CREATE AGENT`
halves of **U-4**. The region/edition availability matrix and `SHOW AGENTS`/`ALTER AGENT`
syntax remain unconfirmed; the emitters therefore avoid pinning anything region-specific
(see A-20) and publication stays a human act against a real account, where a feature that
is unavailable fails loudly at apply time rather than silently in generated text.

## 7. Verified — snowflake-connector-python

Current **4.7.2** (Aug 2026), Python ≥ 3.10. Extras: `[pandas]` (pyarrow/pandas),
`[secure-local-storage]`. Auth: key-pair (RSA ≥ 2048, **two active public keys for
zero-downtime rotation**), OAuth (code + client-credentials, refresh tokens), PAT
(`authenticator='programmatic_access_token'`), `externalbrowser`,
`username_password_mfa`, WIF. **MFA enforcement rollout ends Oct 2026: service users
will be limited to key-pair / OAuth / PAT / WIF; single-factor passwords blocked** —
vindicates key-pair as the default and the UI "discouraged" warning on passwords.
<https://docs.snowflake.com/en/user-guide/security-mfa-rollout>

## 8. Verified — DuckDB (target: current stable 1.5.x)

- Stable **v1.5.5** (Jul 2026); v1.4.x is LTS; v2.0 previewed but not shipped.
- `QUALIFY` supported. <https://duckdb.org/docs/current/sql/query_syntax/qualify>
- `DECIMAL` max width 38; `+ - *` stay exact fixed-point (error on overflow >38);
  **division returns floating-point** — the safe-divide shim must therefore CAST the
  quotient back to `DECIMAL(38,9)` (or compute via scaled integer arithmetic) for
  credit/currency ratios, with a parity test. Width >19 uses INT128 (slower — fine).
  <https://duckdb.org/docs/stable/sql/data_types/numeric>
- Parquet predicate + projection pushdown confirmed (row-group/page skipping via
  min-max zonemaps). <https://duckdb.org/docs/current/data/parquet/overview>

## 9. Verified — LLM model identifiers (§21 defaults)

Anthropic API (docs now at platform.claude.com): flagship-tier `claude-opus-5`
(also `claude-fable-5`, `claude-sonnet-5`); fast tier `claude-haiku-4-5` (pinned
`claude-haiku-4-5-20251001`). Since the 4.6 generation, first-party IDs are dateless
pinned snapshots. Bedrock: legacy InvokeModel/Converse path uses date-versioned IDs
(e.g. `anthropic.claude-haiku-4-5-20251001-v1:0`) with `us./eu./jp./au.` cross-region
inference profiles; the newer "Claude in Amazon Bedrock" Messages-API path uses plain
`anthropic.claude-opus-5`-style IDs; global endpoints exist for current models,
regional endpoints carry a ~10% premium. Defaults chosen for `.env.example`:
`LLM__MODEL_STRONG=claude-opus-5`, `LLM__MODEL_FAST=claude-haiku-4-5` (commented out;
provider defaults to `none`). <https://platform.claude.com/docs/en/about-claude/models/overview>

---

## 10. Could not confirm (re-verify before dependent code ships)

| # | Item | Needed by | Action |
|---|---|---|---|
| U-1 | Exact numeric latencies for CORTEX_FUNCTIONS_*, CORTEX_ANALYST, Cortex Search usage views ("a few hours" per docs) and TAG_REFERENCES (2 h implied) | Phase 1 source registry | Set `documented_latency_minutes` conservatively (180) with a `verified: false` marker in the YAML; re-check per-view pages when writing each registry entry |
| U-2 | Complete per-role view lists for the four viewer roles (key views confirmed; full table not extracted) | Phase 4 provisioning SQL | Extract the full table from the snowflake-db-roles page when writing `snowflake/provisioning/*.sql` |
| U-3 | Which ORGANIZATION_USAGE views map to ORGANIZATION_USAGE_VIEWER vs ORGANIZATION_BILLING_VIEWER | Phase 4 | Same as U-2 |
| U-4 | Region/edition matrices for semantic views, Cortex Search, Cortex Agents; `SHOW AGENTS`/`ALTER AGENT` syntax | Phase 7 emitters | Feature-detect at publish time (`SELECT SYSTEM$…`/trial DDL in a sandbox schema) rather than assuming; re-read docs at Phase 7 per §25 |
| U-5 | Per-driver minimum versions in the MFA rollout doc | Phase 4 | Pin connector ≥ 4.7.2 (current); re-check when writing the connection wizard |

## 11. Assumptions (rationale + revisit trigger)

| # | Assumption | Rationale | Revisit trigger |
|---|---|---|---|
| A-1 | Latency figures in §1–2 above are treated as *floors* for freshness banners and stored in the source registry, not code | R7; per-view pages are authoritative as of 2026-08-24 | Any Snowflake release-note bundle touching ACCOUNT_USAGE (check at each phase start) |
| A-2 | Reconciliation gate (R6) reconciles against **net** cloud services (`CREDITS_USED_CLOUD_SERVICES + CREDITS_ADJUSTMENT_CLOUD_SERVICES`) at account-day grain | §4 above — the adjustment is not attributable per warehouse | If Snowflake ever exposes per-warehouse adjusted cloud services |
| A-3 | Idle credits per warehouse-day = `WAREHOUSE_METERING_HISTORY.CREDITS_USED_COMPUTE − Σ QUERY_ATTRIBUTION_HISTORY.CREDITS_ATTRIBUTED_COMPUTE`, with the ≤100 ms-query and Adaptive-Warehouse exclusions accepted as noise inside the 0.5% tolerance | §5 above; HLD model | Reconciliation variance > 0.5% attributable to excluded query classes; Adaptive Warehouses in the target account (then include QUERY_METERING_HISTORY) |
| A-4 | Accounts using **Adaptive Warehouses** need QUERY_METERING_HISTORY as an additional registered source | QUERY_ATTRIBUTION_HISTORY excludes them (verified) | First LIVE customer with Gen2/adaptive warehouses; Phase 4 capability probe detects and surfaces this |
| A-5 | Document AI metrics are out of scope (view kept `optional` for historical data only) | Document AI documented as decommissioned | Client explicitly requests historical Document AI cost |
| A-6 | The DuckDB safe-divide/percentile shims will CAST back to DECIMAL(38,9) after division | DuckDB division returns floating-point (§8) | Parity test failure at tolerance 0 for ratio metrics |
| A-7 | DuckDB pinned to current stable 1.5.x; v2.0 not adopted until parity suite passes on it | v2.0 unreleased | DuckDB 2.0 GA |
| A-8 | Semantic-view emitter targets the `AI_VERIFIED_QUERIES` clause syntax in §6 | Verified against current CREATE SEMANTIC VIEW page | Phase 7 §25 re-check (U-4) |
| A-9 | Internal-marketplace emitter produces `CREATE ORGANIZATION LISTING` (BUILD_PROMPT says "CREATE LISTING") | Verified current DDL name; BUILD_PROMPT predates rename | none — spec updated here |
| A-10 | LLM defaults: strong `claude-opus-5`, fast `claude-haiku-4-5`; `claude-fable-5` offered as an opt-in strong model | §9 above; Opus 5 is the flagship for enterprise/agentic work | New Claude model family GA |
| A-11 | Password auth is implemented but flagged *discouraged*, and service-user connections default to key-pair | MFA rollout ends Oct 2026 blocking single-factor passwords (§7) | Snowflake fully removing password auth (then drop the code path) |
| A-12 | Postgres 16 / Redis 7 as pinned in BUILD_PROMPT §6, even though newer majors exist | Spec pins them; boring choice | Client platform standard requires newer major |
| A-13 | Compose worker healthcheck relies on `arq --check`; job cron schedules arrive with the jobs that need them | ADR-0002 | arq behaviour change on upgrade |
| A-14 | Phase 0 defers (not stubs): `Dockerfile.allinone`, `docker-compose.demo.yml`, `make demo/test-parity/eval/migrate/seed/generate-fixtures/scan`, Terraform, release/security workflows, OpenTelemetry wiring, `/metrics` endpoint — each owned by a later phase per §24; no placeholder files exist for them | §27.15 requires deferrals be recorded, not silently stubbed | The owning phase (1, 2, 6, 8) |
| A-15 | LICENSE is a proprietary all-rights-reserved placeholder | BUILD_PROMPT names a LICENSE file but no licence; this is a client deliverable — granting open-source rights is not ours to decide | Owner picks the delivery licence |
| A-16 | `apps/api` deps include SQLAlchemy 2 async + asyncpg now (readiness checks); Alembic + models arrive with the first metadata tables | §6 pins SQLModel/SQLAlchemy + Alembic; readiness needs a real DB ping today | First persistent model (Phase 1 uploads) |
| A-17 | **The HLD worked example's stated total of $156 is treated as including non-compute components the summary does not itemise; the engine asserts $126 for compute + cloud services.** | BUILD_PROMPT §10.2 gives: 40 metered credits, direct 18/9/3, 10 idle, $6 cloud services, Marketing $75.60, total $156. The engine reproduces **every per-team figure exactly** (Marketing $75.60, Finance $37.80, Ops $12.60) — those are what validate the allocation maths. But 40 credits x $3 = $120, plus $6 cloud services = **$126**, and the three per-team figures sum to precisely that. The $30 gap is exactly 10 credits. Since storage allocates by database owner tag and serverless/AI by object tag (§10.2), the most likely reading is that $156 is the team's full chargeback line including those components. Asserting $156 against a compute-only calculation would require inventing 10 credits from nowhere. | HLD owner confirms whether $156 includes storage/serverless/AI; if it is a typo, no code changes — only this note and the test comment. If it turns out compute-only, the allocation model is wrong and must be revisited before chargeback publishes. |
| A-18 | **Phase 7 defers durable persistence of data-product approval events.** The lifecycle ledger (`snowobs_dataproducts.publish.LifecycleLedger`) is append-only but in-process: a restart loses the recorded transitions, and each API worker holds its own. The approval *evidence requirements* (named actor, timestamp, non-trivial reason, refusal on an unevidenced transition) are fully implemented and tested. | R8 requires the approval to be recorded, and it is — but the durable store is the Postgres audit table in §17, which needs the app-metadata schema and Alembic migrations that A-16 defers to the first persistent model. Building a second, product-specific persistence layer now would have to be torn out when the audit table lands. | The first Alembic migration for app metadata. The ledger's `ApprovalEvent.to_record()` is already shaped for that table, and `ProductService` takes a ledger by injection so the swap is a constructor change. Until then, a multi-worker deployment must not be told its approvals survive a restart. |
| A-19 | The approving human's identity reaches the API through an `X-Snowobs-Actor` header rather than an authenticated session. A request without it is **refused**, never attributed to "system". | §17's OIDC/RBAC layer is not built yet; R8's requirement is that a human is named on the record, and refusing an anonymous approval satisfies that today. Defaulting the actor would have been the failure mode worth avoiding. | OIDC lands (§17): the header becomes a fallback for service-to-service calls or is removed entirely, and the actor comes from the verified session. Until then this header is trusted, so the API must not be exposed outside an authenticated perimeter. |
| A-20 | The emitted `CREATE AGENT` spec **omits** `models.orchestration` unless a deployment pins one via `SnowflakeTarget.orchestration_model`, and the organization listing manifest omits `locations` unless regions are configured. | Cortex model availability and listing fulfilment regions vary by account region and edition, and U-4's availability matrix is still unconfirmed. An unpinned spec takes the account default; a hard-coded identifier would be a guess that fails at apply time in some regions and, worse, silently selects a different model in others. | U-4 closes, or a client account's region/edition matrix is known — then pin per deployment in configuration, not in code. |
| A-21 | `organization_targets.access` is emitted as `- all_accounts: true` when no specific consumer accounts are configured, and as `- account: <locator>` entries when they are. | The manifest reference confirms `organization_targets` is required and carries an access list plus support and approver contacts, but the exact key for "the whole organization" could not be read first-hand (docs egress blocked, see §6a). The value is deployment configuration on `SnowflakeTarget`, so correcting it is a config edit rather than a code change, and the listing is created with `PUBLISH = FALSE` so a wrong target cannot expose the product before a human reviews the manifest. | First real organization-listing apply, or direct access to the manifest reference page. |
| A-22 | Phase 7 ships as **one** workspace package, `packages/dataproducts` (`snowobs_dataproducts`), holding the registry, contracts, emitters, and publish workflow — rather than the `packages/products` + `packages/artifacts` split named in CLAUDE.md §5. | Every emitter reads the product declaration and its derived contract, and the contract is derived from the same resolution the emitters use; splitting them across two packages would put a hard dependency edge between them and duplicate the resolution layer for no isolation benefit. The `emitters/` subpackage keeps the artifact code separable if it ever needs to move. | CLAUDE.md §5 is updated to match, or a second consumer of the artifact emitters appears that does not already depend on the product registry. |
| A-23 | A data product's relations are grouped **one per semantic entity**, and a product may not mix declared time grains within one relation (the registry refuses it at load). Ratio, percentile, and distinct-count measures are published as semantic-view `FACTS`, never `METRICS`. | Metrics from two facts cannot share a relation without a fan-out join or an invented key (§8.3); and the compiler resolves a mixed-grain request to the coarsest grain, which silently regrains the finer metric so every figure read from it means something other than its definition says. Non-additive measures published as `METRICS` would be re-aggregated by any consumer tool into a wrong number (R12). | A semantic-layer change that makes cross-entity relations safe, or a Snowflake semantic-view feature that expresses non-additivity directly. |
| A-24 | **Alert state is in-process.** The dedup ledger and the per-rule fire/action statistics (`snowobs_analytics.alerting.DedupLedger`, `RuleStatistics`) live in whichever process evaluated the rule. A worker restart clears open alerts, and the statistics the API reports are the API process's own — which, for a rule only the worker evaluates, means zero. Consequently there is also **no acknowledgement endpoint**: `AlertEngine.acknowledge` exists and drives the pruning proposal, but nothing calls it, so pruning proposals will not appear on their own. | Same reasoning as A-18: the durable store is the Postgres audit/event table in §17, which waits on the app-metadata schema and Alembic migrations A-16 defers. Two competing persistence layers would be worse than one honest gap. The failure direction is safe — a cleared ledger re-fires a condition that is still breaching, rather than suppressing one that is. The backtest endpoint is the practical substitute for firing statistics: it answers "how often would this have fired" from the data rather than from process memory. | The first Alembic migration for app metadata. `AlertEvent` is already shaped for that table (`dedup_key`, `fired_at`, `acknowledged_by`, `actioned`), and `AlertService` takes its rule set and channels by injection, so the swap is a constructor change plus an acknowledgement route. |
| A-25 | **PagerDuty and ServiceNow/Jira ticket creation are not implemented**, and neither is §14's guardrail management (drafting and applying resource monitors, statement timeouts by workload class, auto-suspend policy, Snowflake budgets). What ships is webhook (Slack/Teams-shaped) and email. A P1's `page` route and a P2's `ticket` route are carried on the tier and shown in the API, but delivery for both is a webhook or an email. | Both PagerDuty and ServiceNow accept inbound webhooks, so the shipped `WebhookChannel` reaches them and a deployment is not blocked — what is missing is native integration: incident deduplication against PagerDuty's own dedup key, and ticket lifecycle (open/update/resolve). Building either against a stubbed client would have produced exactly the "looks finished, is not" defect §27.15 exists to prevent. Guardrail management is a *write* path into a customer account and belongs with the approval workflow, not with a read-only scheduler. | A client requires native incident or ticket lifecycle; or the approval-gated write path (R8) lands, at which point guardrail management is built on it — with production resource monitors notify-only plus a P1, never hard-suspending (§27.8). |
| A-26 | `snowobs_common.secrets` is the **only** module besides `config.py` that reads `os.environ`, and it does so through an injected mapping (`EnvSecretResolver(environ=…)`). Separately, `EmailChannel` speaks SMTP; Amazon SES is configured as an SMTP relay rather than called through the SES API. | §21 says environment variables are read in the settings module. The intent — configuration is typed and validated in one place — is preserved: secret *material* never enters `Settings` at all, which is stronger than reading it there would be. SES publishes an SMTP endpoint, so relaying through it is a real integration rather than a partial one, and it keeps `boto3` out of the analytics package's dependency closure (it stays an optional extra for the `aws` secrets provider and Bedrock). | A deployment needs SES features SMTP does not expose (configuration sets, per-message event publishing), or the secrets adapter grows a provider that cannot take an injected client. |
| A-27 | An alert rule's evaluation `window` is one of `day`, `week`, or `month` — the calendar grains the semantic compiler buckets by. `AlertRule.window_days` (1/7/30) is a *nominal* width used for display and for the DDL export's schedule comment; the query itself is bucketed by the grain. | An arbitrary N-day window would have to be implemented either as N separate single-figure queries per rule per run, or by summing a ratio metric across days — which is wrong for every percentage KPI in the catalogue. Restricting to calendar grains makes one query per rule correct for additive and non-additive measures alike. | A rule genuinely needs a rolling window (for example a trailing 28-day spend comparison), at which point it is a new condition kind with its own compiled query rather than a wider `window` enum. |
| A-28 | `apps/worker` depends on `apps/api` (`snowobs-api`) so the scheduled evaluation runs the same `AlertService` the API serves. | A rule must mean one thing. The alternative — a second evaluation implementation in the worker — is the R1 violation in a different layer: the worker would fire on numbers the API could not reproduce. The worker image already built and copied `apps/api`; only the runtime `COPY` was added. | The service layer outgrows `apps/api` and becomes its own workspace package, at which point both apps depend on that instead. |
