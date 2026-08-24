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
