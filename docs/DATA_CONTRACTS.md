# Data contracts

**Generated from `packages/dataproducts/products/*.yaml` — do not edit by hand.**
Regenerate with `make contracts`.

Each data product below exposes a set of governed metrics as a small number of
relations. The contract is *derived* from the product declaration and the
semantic layer, so a product cannot contract for a column the metric layer does
not produce, and a contract that stops matching the semantic layer is reported
as drift rather than quietly served (R1, R5).

**Freshness guarantees are never optimistic.** Each relation's guarantee is the
*maximum* documented latency across the sources behind it, and the product's
guarantee is the maximum across its relations (R7).

**4 products · 11 relations · 81 contracted columns.**

## Contents

- [Security & Access Governance](#access-governance) — `access_governance` 1.0.0, 9 metrics, freshness 2 h
- [Platform Cost & Attribution](#finops-chargeback) — `finops_chargeback` 1.1.0, 7 metrics, freshness 8 h
- [Pipeline Reliability](#pipeline-health) — `pipeline_health` 1.0.0, 10 metrics, freshness 3 h
- [Warehouse & Compute Efficiency](#warehouse-efficiency) — `warehouse_efficiency` 2.0.0, 11 metrics, freshness 8 h

## Breaking-change policy

Removing a column, changing its type, relaxing its nullability, rebinding it to a different governed metric, changing the grain, loosening the freshness guarantee, or shortening retention is a BREAKING change: it requires a major version, a migration note, and the full deprecation notice period. Everything else is additive and ships as a minor or patch version.

Nothing publishes without a recorded human approval naming the actor, the
time, the reason, and the contract diff (R8). The platform emits the
artifacts; a person applies them in their own account (R2).

## Security & Access Governance — `access_governance` 1.0.0

Authentication and privilege posture: failed logins and their error classes, single-factor authentication still in use ahead of the October 2026 MFA cut-off, client-IP spread per user, privileged and newly granted roles, and identities that are dormant or hold grants while disabled. Restricted: the product carries per-user activity and is masked and row-secured on publication.

| | |
|---|---|
| **Owner** | security-engineering |
| **Domain** | security |
| **Status** | approved |
| **Classification** | restricted |
| **Freshness guarantee** | 2 h — the documented latency of the slowest source; no surface may imply fresher |
| **SLA target** | 4 h freshness, 99.9% availability |
| **Retention** | 365 days |
| **Refresh** | every 4 h (`30 */4 * * *`) |
| **Deprecation notice** | 90 days |
| **Support** | security-engineering@internal |
| **Documentation** | https://internal.docs/data-products/access-governance |
| **Governed metrics** | 9 |
| **Relations** | 3 |

### Consumers

| Consumer | Contact | Purpose | Grantee |
|---|---|---|---|
| Security engineering | security-engineering@internal | Authentication posture monitoring and privilege drift review. | `ROLE_SECURITY_ANALYST` |
| Internal audit | internal-audit@internal | Quarterly access recertification evidence. | `ROLE_AUDITOR` |

### Relations

#### `V_ACCESS_GOVERNANCE_LOGIN`

Security & Access Governance: Login attempt.

- **Entity:** `fact_login`
- **Grain:** `TIME_BUCKET`, `USER`, `CLIENT_TYPE`, `FIRST_FACTOR`, `ERROR_CLASS`, `CLIENT_IP` at day buckets
- **Freshness guarantee:** 2 h
- **Row expectation:** at least 1 row(s) per day
- **Sources:** `SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY`

| Column | Type | Null | Governed metric | Description |
|---|---|---|---|---|
| `TIME_BUCKET` | `TIMESTAMP_LTZ` | no | — | Time bucket over EVENT_AT, at the coarsest grain the product's metrics declare. |
| `USER` | `STRING` | yes | — | User the attempt was made against. · **sensitive** |
| `CLIENT_TYPE` | `STRING` | yes | — | Reported driver or client (JDBC, PYTHON_DRIVER, SNOWFLAKE_UI, …). · searchable |
| `FIRST_FACTOR` | `STRING` | yes | — | First authentication factor (PASSWORD, RSA_KEYPAIR, OAUTH, …). · searchable |
| `ERROR_CLASS` | `STRING` | yes | — | Snowflake error code of a failed attempt, or NONE. · searchable |
| `CLIENT_IP` | `STRING` | yes | — | Source IP of the attempt. · **sensitive** |
| `SEC_FAILED_LOGINS` | `NUMBER(38,0)` | yes | `sec.failed_logins` | Count of failed authentication attempts. Per user and per source IP this is the shape a credential-stuffing attempt makes: many failures against many users from few addresses, or many failures against one user from many. |
| `SEC_FAILED_LOGIN_RATE` | `NUMBER(38,15)` | yes | `sec.failed_login_rate` | Share of authentication attempts that failed. A background rate of a few percent is normal — mistyped passwords, expired sessions. A step change on one user or one client type is not, and that is what the slice is for. |
| `SEC_SINGLE_FACTOR_LOGINS` | `NUMBER(38,0)` | yes | `sec.single_factor_logins` | Successful logins that presented no second authentication factor. Snowflake blocks single-factor password sign-in from October 2026, so this is a migration burn-down: every login counted here is one that will stop working. Key-pair service authentication legitimately has no second factor and shows up here too — slice by first_factor to separate the two populations. |
| `SEC_DISTINCT_CLIENT_IPS` | `NUMBER(38,0)` | yes | `sec.distinct_client_ips` | Number of distinct source addresses a user authenticated from. A service account pinned to a network allowlist should be a small constant; a jump is either an infrastructure change nobody mentioned or a credential in the wrong hands. |

#### `V_ACCESS_GOVERNANCE_GRANT`

Security & Access Governance: Role grant to user.

- **Entity:** `fact_grant`
- **Grain:** `TIME_BUCKET`, `ROLE`, `GRANTEE`, `GRANTED_BY`, `GRANTEE_TYPE`, `PRIVILEGE_TIER` at day buckets
- **Freshness guarantee:** 2 h
- **Row expectation:** at least 0 row(s) per day
- **Sources:** `SNOWFLAKE.ACCOUNT_USAGE.GRANTS_TO_USERS`, `SNOWFLAKE.ACCOUNT_USAGE.USERS`

| Column | Type | Null | Governed metric | Description |
|---|---|---|---|---|
| `TIME_BUCKET` | `TIMESTAMP_LTZ` | no | — | Time bucket over GRANTED_AT, at the coarsest grain the product's metrics declare. |
| `ROLE` | `STRING` | yes | — | Role granted. · searchable |
| `GRANTEE` | `STRING` | yes | — | User the role was granted to. · **sensitive** |
| `GRANTED_BY` | `STRING` | yes | — | Role that issued the grant. · **sensitive** |
| `GRANTEE_TYPE` | `STRING` | yes | — | PERSON, SERVICE, or LEGACY_SERVICE. · searchable |
| `PRIVILEGE_TIER` | `STRING` | yes | — | PRIVILEGED for the account-administration roles, STANDARD otherwise. · searchable |
| `SEC_PRIVILEGED_GRANTS` | `NUMBER(38,0)` | yes | `sec.privileged_grants` | Live grants of ACCOUNTADMIN, SECURITYADMIN, or ORGADMIN. These roles can change billing, read every object, and rewrite the access model, so the right number is small, known, and unchanging — and any movement in it should be traceable to a ticket. Revoked grants are excluded so the figure is current holdings rather than everything ever granted. |
| `SEC_NEW_GRANTS` | `NUMBER(38,0)` | yes | `sec.new_grants` | Grants created inside the requested window, from GRANTS_TO_USERS.CREATED_ON. This is the privilege-drift feed: a review reads it in date order and asks who approved each row. Sliced by privilege_tier it separates routine team membership from a new administrator. |
| `SEC_DISABLED_BUT_GRANTED_USERS` | `NUMBER(38,0)` | yes | `sec.disabled_but_granted_users` | Disabled accounts that still hold live role grants. Disabling a user stops them logging in but leaves the grant graph untouched, so this is the deprovisioning tail: the difference between "cannot sign in today" and "has no access", which is the difference that matters if the account is ever re-enabled. |

#### `V_ACCESS_GOVERNANCE_USER`

Security & Access Governance: User.

- **Entity:** `dim_user`
- **Grain:** `USER`, `USER_TYPE`, `DEFAULT_ROLE`, `ACCOUNT_STATUS`, `CREDENTIAL_TYPE`
- **Freshness guarantee:** 2 h
- **Row expectation:** at least 1 row(s) per day
- **Sources:** `SNOWFLAKE.ACCOUNT_USAGE.USERS`

| Column | Type | Null | Governed metric | Description |
|---|---|---|---|---|
| `USER` | `STRING` | yes | — | User name. · **sensitive** |
| `USER_TYPE` | `STRING` | yes | — | PERSON, SERVICE, or LEGACY_SERVICE. · searchable |
| `DEFAULT_ROLE` | `STRING` | yes | — | Role the user assumes on connect when none is specified. · searchable |
| `ACCOUNT_STATUS` | `STRING` | yes | — | Whether the user is currently disabled. · searchable |
| `CREDENTIAL_TYPE` | `STRING` | yes | — | Credentials configured on the user — the key-pair rollout view. · searchable |
| `SEC_DORMANT_USERS` | `NUMBER(38,0)` | yes | `sec.dormant_users` | Enabled users with no successful login in 90 days. Every one is a live credential with no owner watching it — the cheapest access risk to remove and the one most often left alone because nobody is sure who it belongs to. Age is measured against the newest login in the snapshot, so the cohort is stable between runs. |
| `SEC_USERS_WITHOUT_KEY_PAIR` | `NUMBER(38,0)` | yes | `sec.users_without_key_pair` | Enabled users with no RSA public key configured. Sliced by user_type this is the key-pair rollout backlog for service accounts, which cannot use interactive MFA and must move to key-pair authentication before password sign-in is blocked. |

### Free-text search

A Cortex Search service indexes `ERROR_CLASS` over a 90-day window, filterable by `CLIENT_TYPE`, `FIRST_FACTOR`.

### Change history

| Version | Released | Breaking | Summary |
|---|---|---|---|
| 1.0.0 | 2026-08-24 | no | First release — authentication posture, privilege drift, and dormant identity measures. |

## Platform Cost & Attribution — `finops_chargeback` 1.1.0

Fully allocated Snowflake cost by team, warehouse, and database, alongside the metered and billed account totals the allocation reconciles against. Direct attributed compute, unattributed spend, and the reconciliation variance are published together so a chargeback figure is never read without the evidence that it reconciles (R6). Deliberately credit-denominated: spend in contract currency comes from ORGANIZATION_USAGE, which documents 72 hours of latency, and folding it in here would drag this product's whole freshness guarantee from eight hours to three days for every consumer.

| | |
|---|---|
| **Owner** | finops-platform |
| **Domain** | chargeback |
| **Status** | published |
| **Classification** | confidential |
| **Freshness guarantee** | 8 h — the documented latency of the slowest source; no surface may imply fresher |
| **SLA target** | 8 h freshness, 99.5% availability |
| **Retention** | 365 days |
| **Refresh** | every 1 d (`0 5 * * *`) |
| **Deprecation notice** | 90 days |
| **Support** | finops-platform@internal |
| **Documentation** | https://internal.docs/data-products/finops-chargeback |
| **Governed metrics** | 7 |
| **Relations** | 3 |

Published contract snapshots on file: 1.0.0.

### Consumers

| Consumer | Contact | Purpose | Grantee |
|---|---|---|---|
| Finance business partnering | finance-bp@internal | Monthly chargeback close and budget variance reporting. | `ROLE_FINANCE_ANALYST` |
| Engineering leadership | eng-leads@internal | Team-level cost accountability and budget tracking. | `ROLE_ENG_LEAD` |

### Relations

#### `V_FINOPS_CHARGEBACK_QUERY_EXECUTION`

Platform Cost & Attribution: Query execution.

- **Entity:** `fact_query_execution`
- **Grain:** `TIME_BUCKET`, `TEAM`, `WAREHOUSE`, `DATABASE`, `ALLOCATION_METHOD` at day buckets
- **Freshness guarantee:** 8 h
- **Row expectation:** at least 1 row(s) per day
- **Sources:** `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`

| Column | Type | Null | Governed metric | Description |
|---|---|---|---|---|
| `TIME_BUCKET` | `TIMESTAMP_LTZ` | no | — | Time bucket over STARTED_AT, at the coarsest grain the product's metrics declare. |
| `TEAM` | `STRING` | yes | — | Team from the query tag; UNATTRIBUTED when the tag is absent. · searchable |
| `WAREHOUSE` | `STRING` | yes | — | Warehouse the query ran on. · searchable |
| `DATABASE` | `STRING` | yes | — | Database context of the query. · searchable |
| `ALLOCATION_METHOD` | `STRING` | yes | — | How the query's credits reach a team — directly from its own tag, or via the residual pool spread pro-rata over tagged usage (HLD §10.2). · searchable |
| `CHARGEBACK_ALLOCATED_CREDITS` | `NUMBER(38,9)` | yes | `chargeback.allocated_credits` | Credits allocated to a team by its own query tags — the direct component of the three-part model (direct + idle share + cloud-services share, HLD §10.2). Read alone it under-states a team's true cost, because idle time and cloud services are not attributable per query; it is the component that can be defended query by query, which is why it is stated separately. _(unit: credits)_ |
| `COST_BY_TEAM_CREDITS` | `NUMBER(38,9)` | yes | `cost.by_team_credits` | Directly attributed credits by team, from the query tag. This is the direct component only — the full chargeback figure adds idle and cloud-services shares (see the allocation engine). _(unit: credits)_ |
| `CHARGEBACK_UNATTRIBUTED_CREDITS` | `NUMBER(38,9)` | yes | `chargeback.unattributed_credits` | Attributed credits carrying no team tag. This is the residual pool: it has to be spread over the tagged teams pro-rata, which means every credit here is a credit somebody is being charged for on an estimate rather than on evidence. Sliced by warehouse and user it becomes a tagging backlog. _(unit: credits)_ |
| `CHARGEBACK_UNATTRIBUTED_SHARE` | `NUMBER(38,15)` | yes | `chargeback.unattributed_share` | Fraction of allocated credits that reached a team by estimate rather than by tag. It is the confidence figure that belongs next to every chargeback statement: at 5% the allocation is defensible, at 30% it is a negotiation. |

#### `V_FINOPS_CHARGEBACK_COST_DAILY`

Platform Cost & Attribution: Cost by service type (daily).

- **Entity:** `fact_cost_daily`
- **Grain:** `TIME_BUCKET`, `SERVICE_TYPE` at day buckets
- **Freshness guarantee:** 3 h
- **Row expectation:** at least 1 row(s) per day
- **Sources:** `SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY`

| Column | Type | Null | Governed metric | Description |
|---|---|---|---|---|
| `TIME_BUCKET` | `DATE` | no | — | Time bucket over USAGE_DAY, at the coarsest grain the product's metrics declare. |
| `SERVICE_TYPE` | `STRING` | yes | — | Snowflake service type (WAREHOUSE_METERING, SERVERLESS_TASK, AI_SERVICES, …). · searchable |
| `CHARGEBACK_METERED_CREDITS` | `NUMBER(38,9)` | yes | `chargeback.metered_credits` | The account's metered credits by service type — the control total every allocation must sum back to. It is stated in this domain, rather than borrowed from D1, because the reconciliation gate needs both sides of the comparison to carry the same provenance and the same latency floor. _(unit: credits)_ |
| `COST_BILLED_CREDITS` | `NUMBER(38,9)` | yes | `cost.billed_credits` | Credits actually billed — compute plus cloud services net of the daily 10% adjustment. This is the figure the chargeback reconciliation gate reconciles allocated cost against (R6). _(unit: credits)_ |

#### `V_FINOPS_CHARGEBACK_WAREHOUSE_METERING_HOURLY`

Platform Cost & Attribution: Warehouse metering (hourly).

- **Entity:** `fact_warehouse_metering_hourly`
- **Grain:** `TIME_BUCKET`, `WAREHOUSE` at hour buckets
- **Freshness guarantee:** 8 h
- **Row expectation:** at least 1 row(s) per day
- **Sources:** `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY`

| Column | Type | Null | Governed metric | Description |
|---|---|---|---|---|
| `TIME_BUCKET` | `TIMESTAMP_LTZ` | no | — | Time bucket over METERING_HOUR, at the coarsest grain the product's metrics declare. |
| `WAREHOUSE` | `STRING` | yes | — | Warehouse the credits were metered against. · searchable |
| `CHARGEBACK_RECONCILIATION_VARIANCE` | `NUMBER(38,9)` | yes | `chargeback.reconciliation_variance` | Metered warehouse compute minus the compute attributable to individual queries. The gap is expected, not an error: QUERY_ATTRIBUTION_HISTORY excludes idle time, queries under about 100 ms, and Adaptive Warehouse jobs (ASSUMPTIONS §5). What the reconciliation gate checks is that the gap stays explainable — a variance that grows without idle time growing means the attribution view has stopped seeing part of the workload. _(unit: credits)_ |

### Change history

| Version | Released | Breaking | Summary |
|---|---|---|---|
| 1.1.0 | 2026-08-24 | no | Added billed credits — compute plus cloud services net of the daily 10% adjustment — so the reconciliation target sits beside the metered total. Additive only; every column released in 1.0.0 is unchanged. |
| 1.0.0 | 2026-07-06 | no | First release — allocated and metered credits, unattributed spend, and reconciliation variance by team, warehouse, and database. |

## Pipeline Reliability — `pipeline_health` 1.0.0

Task and dynamic-table reliability: success rate, failures attributed to the root of the dependency graph rather than every leaf it broke, duration drift, refresh lag against TARGET_LAG, and freshness SLA attainment. This is the product an on-call engineer reads at 03:00, so it carries the coarsest honest freshness of its sources and says so.

| | |
|---|---|
| **Owner** | data-engineering |
| **Domain** | pipeline |
| **Status** | draft |
| **Classification** | internal |
| **Freshness guarantee** | 3 h — the documented latency of the slowest source; no surface may imply fresher |
| **SLA target** | 3 h freshness, 99.5% availability |
| **Retention** | 365 days |
| **Refresh** | every 1 h (`5 * * * *`) |
| **Deprecation notice** | 60 days |
| **Support** | data-engineering@internal |
| **Documentation** | https://internal.docs/data-products/pipeline-health |
| **Governed metrics** | 10 |
| **Relations** | 2 |

### Consumers

| Consumer | Contact | Purpose | Grantee |
|---|---|---|---|
| Data engineering on-call | data-engineering@internal | Incident triage and root-failure identification. | `ROLE_DATA_ENG` |
| Platform reliability review | platform-engineering@internal | Weekly repeat-failure and lag-breach triage. | `ROLE_PLATFORM_ENG` |

### Relations

#### `V_PIPELINE_HEALTH_TASK_RUN`

Pipeline Reliability: Task run.

- **Entity:** `fact_task_run`
- **Grain:** `TIME_BUCKET`, `TASK`, `GRAPH_ROOT`, `ERROR_CLASS`, `DATABASE`, `TASK_SCHEMA` at day buckets
- **Freshness guarantee:** 45 min
- **Row expectation:** at least 1 row(s) per day
- **Sources:** `SNOWFLAKE.ACCOUNT_USAGE.TASK_HISTORY`

| Column | Type | Null | Governed metric | Description |
|---|---|---|---|---|
| `TIME_BUCKET` | `TIMESTAMP_LTZ` | no | — | Time bucket over SCHEDULED_AT, at the coarsest grain the product's metrics declare. |
| `TASK` | `STRING` | yes | — | Task name. · searchable |
| `GRAPH_ROOT` | `STRING` | yes | — | Root task of the DAG the run belongs to — the roll-up key. · searchable |
| `ERROR_CLASS` | `STRING` | yes | — | Snowflake error code of the run, or NONE when it succeeded. · searchable |
| `DATABASE` | `STRING` | yes | — | Database the task is defined in. · searchable |
| `TASK_SCHEMA` | `STRING` | yes | — | Schema the task is defined in. · searchable |
| `PIPE_TASK_SUCCESS_RATE` | `NUMBER(38,15)` | yes | `pipe.task_success_rate` | Share of task runs that completed successfully. SKIPPED runs are in the denominator on purpose: a graph that is repeatedly suspended is not delivering data, however healthy its individual statements look. |
| `PIPE_TASK_FAILURES` | `NUMBER(38,0)` | yes | `pipe.task_failures` | Count of task runs that ended in FAILED. Sliced by error class this separates the timeouts from the genuinely broken SQL, which want different responses. |
| `PIPE_ROOT_FAILURES` | `NUMBER(38,0)` | yes | `pipe.root_failures` | Failures at the root of a task graph — the incidents worth paging on. A root failure suspends everything beneath it, so this count is what an alert should carry while the downstream SKIPPED runs stay context rather than separate notifications. |
| `PIPE_SKIPPED_DOWNSTREAM` | `NUMBER(38,0)` | yes | `pipe.skipped_downstream` | Runs skipped because something upstream failed. Read against pipe.root_failures this is the blast radius of an incident: one root failure, twelve tables that did not refresh. |
| `PIPE_TASK_DURATION_P95` | `NUMBER(38,9)` | yes | `pipe.task_duration_p95` | 95th-percentile wall-clock duration from scheduled time to completion, per task. Measured from *scheduled* rather than started time deliberately: a task that waits ten minutes for a warehouse has been late by ten minutes, whatever its statement timing says. The percentile is taken from a nearest-rank window computed over the task's retained history rather than through the PERCENTILE shim, because Snowflake estimates percentiles from a t-digest and DuckDB computes them exactly — a difference no task-duration tail has enough observations to absorb. _(unit: seconds)_ |

#### `V_PIPELINE_HEALTH_DYNAMIC_TABLE_REFRESH`

Pipeline Reliability: Dynamic table refresh.

- **Entity:** `fact_dynamic_table_refresh`
- **Grain:** `TIME_BUCKET`, `DYNAMIC_TABLE`, `DATABASE`, `TABLE_SCHEMA`, `SLA_STATUS` at day buckets
- **Freshness guarantee:** 3 h
- **Row expectation:** at least 1 row(s) per day
- **Sources:** `SNOWFLAKE.ACCOUNT_USAGE.DYNAMIC_TABLE_REFRESH_HISTORY`

| Column | Type | Null | Governed metric | Description |
|---|---|---|---|---|
| `TIME_BUCKET` | `TIMESTAMP_LTZ` | no | — | Time bucket over REFRESH_START_AT, at the coarsest grain the product's metrics declare. |
| `DYNAMIC_TABLE` | `STRING` | yes | — | Fully qualified dynamic table name. · searchable |
| `DATABASE` | `STRING` | yes | — | Database the dynamic table lives in. · searchable |
| `TABLE_SCHEMA` | `STRING` | yes | — | Schema the dynamic table lives in. · searchable |
| `SLA_STATUS` | `STRING` | yes | — | Whether the refresh met its declared TARGET_LAG. · searchable |
| `PIPE_DT_LAG_VS_TARGET` | `NUMBER(38,15)` | yes | `pipe.dt_lag_vs_target` | Actual lag as a fraction of declared TARGET_LAG. At or below 100% the table is meeting the freshness contract it advertises; above it, downstream consumers are reading data older than they were promised. Expressed as a ratio rather than raw seconds so tables with different targets are comparable on one axis. |
| `PIPE_DT_LAG_BREACHES` | `NUMBER(38,0)` | yes | `pipe.dt_lag_breaches` | Refreshes that started against data older than the table's TARGET_LAG. Refreshes with no declared target are excluded rather than counted as passing — an unstated SLA is not a met one. |
| `PIPE_DT_REFRESH_FAILURES` | `NUMBER(38,0)` | yes | `pipe.dt_refresh_failures` | Refreshes that did not succeed, including UPSTREAM_FAILED — a dynamic table whose source failed is just as stale as one that failed itself, and the consumer cannot tell the difference. |
| `DQ_FRESHNESS_SLA_ATTAINMENT` | `NUMBER(38,15)` | yes | `dq.freshness_sla_attainment` | Share of dynamic-table refreshes that met the table's own declared TARGET_LAG. Refreshes on tables with no target are excluded from both sides of the ratio rather than counted as successes — this measures attainment against stated SLAs, not the absence of one. |
| `DQ_SLA_BREACH_COUNT` | `NUMBER(38,0)` | yes | `dq.sla_breach_count` | Number of refreshes that missed their declared target lag. Counted per refresh rather than per table so a table that breaches every hour is visibly worse than one that breached once. |

### Free-text search

A Cortex Search service indexes `TASK` over a 30-day window, filterable by `DATABASE`, `TASK_SCHEMA`, `GRAPH_ROOT`, `ERROR_CLASS`.

### Change history

| Version | Released | Breaking | Summary |
|---|---|---|---|
| 1.0.0 | 2026-08-24 | no | First release — task reliability, dynamic-table refresh lag, and freshness SLA attainment at hourly grain. |

## Warehouse & Compute Efficiency — `warehouse_efficiency` 2.0.0

Per-warehouse utilisation, idle and zombie credits, queueing, spill, and cost per query — the evidence behind every right-sizing and consolidation recommendation. Published hourly so a warehouse that starts queueing is visible the same working day rather than at month end.

| | |
|---|---|
| **Owner** | platform-engineering |
| **Domain** | warehouse |
| **Status** | published |
| **Classification** | internal |
| **Freshness guarantee** | 8 h — the documented latency of the slowest source; no surface may imply fresher |
| **SLA target** | 8 h freshness, 99.0% availability |
| **Retention** | 365 days |
| **Refresh** | every 1 h (`15 * * * *`) |
| **Deprecation notice** | 60 days |
| **Support** | platform-engineering@internal |
| **Documentation** | https://internal.docs/data-products/warehouse-efficiency |
| **Governed metrics** | 11 |
| **Relations** | 3 |

Published contract snapshots on file: 1.0.0.

### Consumers

| Consumer | Contact | Purpose | Grantee |
|---|---|---|---|
| Platform engineering | platform-engineering@internal | Right-sizing, consolidation, and auto-suspend policy enforcement. | `ROLE_PLATFORM_ENG` |
| FinOps | finops-platform@internal | Sizing the idle and zombie components of the optimisation backlog. | `ROLE_FINOPS_ANALYST` |

### Relations

#### `V_WAREHOUSE_EFFICIENCY_WAREHOUSE_METERING_HOURLY`

Warehouse & Compute Efficiency: Warehouse metering (hourly).

- **Entity:** `fact_warehouse_metering_hourly`
- **Grain:** `TIME_BUCKET`, `WAREHOUSE` at hour buckets
- **Freshness guarantee:** 8 h
- **Row expectation:** at least 24 row(s) per day
- **Sources:** `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY`

| Column | Type | Null | Governed metric | Description |
|---|---|---|---|---|
| `TIME_BUCKET` | `TIMESTAMP_LTZ` | no | — | Time bucket over METERING_HOUR, at the coarsest grain the product's metrics declare. |
| `WAREHOUSE` | `STRING` | yes | — | Warehouse the credits were metered against. · searchable |
| `WH_UTILISATION_PCT` | `NUMBER(38,15)` | yes | `wh.utilisation_pct` | Share of metered compute credits that queries actually accounted for. Low utilisation with no queueing is the signature of an over-sized warehouse. |
| `WH_IDLE_PCT` | `NUMBER(38,15)` | yes | `wh.idle_pct` | Metered credits with no attributable query, as a share of compute. |
| `WH_ZOMBIE_CREDITS` | `NUMBER(38,9)` | yes | `wh.zombie_credits` | Credits metered in hours where no query was attributed at all — a warehouse running with nothing to do. _(unit: credits)_ |
| `WH_CLOUD_SERVICES_CREDITS` | `NUMBER(38,9)` | yes | `wh.cloud_services_credits` | Raw per-warehouse cloud-services credits. The daily 10% adjustment is an account-level calculation and is deliberately NOT applied here — summing this column does not give a billable figure (see cost.cloud_services_credits). _(unit: credits)_ |
| `COST_BY_WAREHOUSE_CREDITS` | `NUMBER(38,9)` | yes | `cost.by_warehouse_credits` | Metered compute credits per warehouse. _(unit: credits)_ |

#### `V_WAREHOUSE_EFFICIENCY_WAREHOUSE_LOAD_HOURLY`

Warehouse & Compute Efficiency: Warehouse load (hourly).

- **Entity:** `fact_warehouse_load_hourly`
- **Grain:** `TIME_BUCKET`, `WAREHOUSE` at hour buckets
- **Freshness guarantee:** 45 min
- **Row expectation:** at least 24 row(s) per day
- **Sources:** `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`

| Column | Type | Null | Governed metric | Description |
|---|---|---|---|---|
| `TIME_BUCKET` | `TIMESTAMP_LTZ` | no | — | Time bucket over LOAD_HOUR, at the coarsest grain the product's metrics declare. |
| `WAREHOUSE` | `STRING` | yes | — | Warehouse the load was observed on. · searchable |
| `WH_QUEUE_OVERLOAD_PCT` | `NUMBER(38,15)` | yes | `wh.queue_overload_pct` | Share of total elapsed query time spent queued because the warehouse was already saturated — the signal that a warehouse is under-provisioned. |
| `WH_QUEUE_PROVISIONING_PCT` | `NUMBER(38,15)` | yes | `wh.queue_provisioning_pct` | Share of elapsed time spent waiting for compute to be provisioned. |
| `WH_QUERY_COUNT` | `NUMBER(38,0)` | yes | `wh.query_count` | Queries executed per warehouse-hour. |
| `WH_AVG_EXECUTION_MS` | `NUMBER(38,3)` | yes | `wh.avg_execution_ms` | Mean execution time per query on the warehouse. |

#### `V_WAREHOUSE_EFFICIENCY_QUERY_EXECUTION`

Warehouse & Compute Efficiency: Query execution.

- **Entity:** `fact_query_execution`
- **Grain:** `TIME_BUCKET`, `WAREHOUSE`, `WAREHOUSE_SIZE` at day buckets
- **Freshness guarantee:** 8 h
- **Row expectation:** at least 1 row(s) per day
- **Sources:** `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`

| Column | Type | Null | Governed metric | Description |
|---|---|---|---|---|
| `TIME_BUCKET` | `TIMESTAMP_LTZ` | no | — | Time bucket over STARTED_AT, at the coarsest grain the product's metrics declare. |
| `WAREHOUSE` | `STRING` | yes | — | Warehouse the query ran on. · searchable |
| `WAREHOUSE_SIZE` | `STRING` | yes | — | Warehouse size at execution time. · searchable |
| `WH_CREDITS_PER_QUERY` | `NUMBER(38,9)` | yes | `wh.credits_per_query` | Attributed credits per query, sliced by warehouse and size. _(unit: credits)_ |
| `WH_SPILL_QUERY_SHARE` | `NUMBER(38,15)` | yes | `wh.spill_query_share` | Fraction of queries spilling to storage — the signal that a warehouse is under-sized for its workload, as opposed to merely busy. |

### Change history

| Version | Released | Breaking | Summary |
|---|---|---|---|
| 2.0.0 | 2026-08-24 | **yes** | Warehouse configuration columns (max clusters, auto-suspend seconds) moved out of this product; they are point-in-time account metadata rather than metered history and belong with the warehouse inventory. Query-side efficiency measures (cost per query, spill share, average execution time) added in their place. |
| 1.0.0 | 2026-07-06 | no | First release — utilisation, idle, queueing, and per-warehouse compute credits at hourly grain. |

**Migration note, 2.0.0:** Consumers reading WH_MAX_CLUSTERS or WH_AUTOSUSPEND_SECONDS from V_WAREHOUSE_EFFICIENCY_WAREHOUSE must read them from SHOW WAREHOUSES or the warehouse inventory product instead. All other columns are unchanged; 1.x remains available for the 60-day deprecation window.
