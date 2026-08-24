# KPI catalogue

**Generated from `packages/semantics/metrics/*.yaml` — do not edit by hand.**
Regenerate with `make catalog`.

Every KPI below is defined once, in YAML, and compiled to both Snowflake and
DuckDB SQL by the same compiler (R1). Each declares the source views it needs,
which is what drives the coverage matrix: a KPI whose sources are missing renders
as *"Unavailable — requires …"* with a remediation, never as a zero (R3).

The **freshness floor** is the documented latency of the slowest source a KPI
reads. No surface may imply a figure is fresher than this (R7).

**92 KPIs across 9 domains.**

## Contents

- [D1 — Cost & spend](#d1-cost-spend) (15)
- [D2 — Warehouse & compute efficiency](#d2-warehouse-compute-efficiency) (12)
- [D3 — Query & workload performance](#d3-query-workload-performance) (14)
- [D4 — Storage & data lifecycle](#d4-storage-data-lifecycle) (8)
- [D5 — Pipeline & orchestration reliability](#d5-pipeline-orchestration-reliability) (10)
- [D6 — Data quality & freshness](#d6-data-quality-freshness) (7)
- [D7 — Security, access & governance](#d7-security,-access-governance) (10)
- [D8 — AI / Cortex & advanced features](#d8-ai-/-cortex-advanced-features) (7)
- [D9 — Chargeback, budget & commitment](#d9-chargeback,-budget-commitment) (9)

## D1 — Cost & spend

| KPI | Name | Freshness floor | Direction |
|---|---|---|---|
| `cost.attributed_credits` | Attributed credits | 8 h | neutral |
| `cost.billed_credits` | Billed credits | 3 h | lower is better |
| `cost.by_team_credits` | Credits by team (direct) | 8 h | lower is better |
| `cost.by_warehouse_credits` | Credits by warehouse | 3 h | lower is better |
| `cost.cloud_services_credits` | Cloud services credits (billed) | 3 h | lower is better |
| `cost.cloud_services_ratio` | Cloud services ratio | 3 h | lower is better |
| `cost.compute_credits` | Compute credits | 3 h | lower is better |
| `cost.idle_credits` | Idle credits | 8 h | lower is better |
| `cost.per_query` | Cost per query | 8 h | lower is better |
| `cost.per_tb_scanned` | Cost per TB scanned | 8 h | lower is better |
| `cost.platform_self_cost` | Platform self-cost | 8 h | lower is better |
| `cost.spend_usd` | Spend in currency | 3 d | lower is better |
| `cost.top5_concentration` | Spend concentration (top 5 warehouses) | 3 h | neutral |
| `cost.total_credits` | Total credits consumed | 3 h | lower is better |
| `cost.unattributed_share` | Unattributed spend share | 8 h | lower is better |

#### `cost.attributed_credits` — Attributed credits

Compute credits attributable to individual queries. Excludes warehouse idle time, cloud services, and (per the documented view behaviour) queries under ~100 ms and Adaptive Warehouse jobs.

| | |
|---|---|
| **Entity** | `fact_warehouse_metering_hourly` |
| **Expression** | `SUM(CREDITS_ATTRIBUTED)` |
| **Grain** | hour |
| **Format** | number (credits) |
| **Direction** | neutral |
| **Freshness floor** | 8 h |
| **Owner** | finops |
| **Allocation method** | direct |
| **Dimensions** | `metering_hour`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY` |
| **Also known as** | direct credits, query-attributed credits |

#### `cost.billed_credits` — Billed credits

Credits actually billed — compute plus cloud services net of the daily 10% adjustment. This is the figure the chargeback reconciliation gate reconciles allocated cost against (R6).

| | |
|---|---|
| **Entity** | `fact_cost_daily` |
| **Expression** | `SUM(CREDITS_BILLED)` |
| **Grain** | day |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 3 h |
| **Owner** | finops |
| **Allocation method** | metered |
| **Dimensions** | `service_type`, `usage_day` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY` |
| **Also known as** | billed credits, chargeable credits, net credits |

Verified questions:
- *billed credits this month*
- *billed credits by day*

#### `cost.by_team_credits` — Credits by team (direct)

Directly attributed credits by team, from the query tag. This is the direct component only — the full chargeback figure adds idle and cloud-services shares (see the allocation engine).

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `SUM(CREDITS_ATTRIBUTED)` |
| **Grain** | day |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 8 h |
| **Owner** | finops |
| **Allocation method** | direct |
| **Dimensions** | `database`, `team`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY` |
| **Also known as** | team spend, cost by team, departmental cost |

Verified questions:
- *credits by team this month*
- *which team spent the most last week*

#### `cost.by_warehouse_credits` — Credits by warehouse

Metered compute credits per warehouse.

| | |
|---|---|
| **Entity** | `fact_warehouse_metering_hourly` |
| **Expression** | `SUM(CREDITS_COMPUTE)` |
| **Grain** | hour |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 3 h |
| **Owner** | finops |
| **Dimensions** | `metering_hour`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY` |
| **Also known as** | warehouse spend, credits per warehouse |

Verified questions:
- *top warehouses by credits*
- *credits for WH_ELT_CORE last week*

#### `cost.cloud_services_credits` — Cloud services credits (billed)

Cloud-services credits net of the daily adjustment. Snowflake bills only the portion exceeding 10% of that day's compute; the adjustment column carries a negative rebate and is added here.

| | |
|---|---|
| **Entity** | `fact_cost_daily` |
| **Expression** | `SUM(CREDITS_CLOUD_SERVICES_BILLED)` |
| **Grain** | day |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 3 h |
| **Owner** | finops |
| **Dimensions** | `service_type`, `usage_day` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY` |
| **Also known as** | cloud services, cloud services billing |

#### `cost.cloud_services_ratio` — Cloud services ratio

Raw cloud-services credits as a fraction of compute credits. Above 10% the excess is billable; at or below it the whole day's cloud services is rebated.

| | |
|---|---|
| **Entity** | `fact_cost_daily` |
| **Expression** | `SAFE_RATIO(SUM(CREDITS_CLOUD_SERVICES_RAW), SUM(CREDITS_COMPUTE))` |
| **Grain** | day |
| **Format** | percent |
| **Direction** | lower is better |
| **Freshness floor** | 3 h |
| **Owner** | finops |
| **Dimensions** | `usage_day` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY` |
| **Thresholds** | warn: 0.08, critical: 0.1 |
| **Also known as** | cloud services percentage, cloud services vs compute |

#### `cost.compute_credits` — Compute credits

Virtual-warehouse compute credits, excluding cloud services.

| | |
|---|---|
| **Entity** | `fact_cost_daily` |
| **Expression** | `SUM(CREDITS_COMPUTE)` |
| **Grain** | day |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 3 h |
| **Owner** | finops |
| **Dimensions** | `service_type`, `usage_day` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY` |
| **Also known as** | warehouse credits, compute consumption |

#### `cost.idle_credits` — Idle credits

Metered compute credits with no attributable query — the warehouse was running but not working. Allocated to teams pro-rata to their direct usage on that warehouse (HLD §10.2).

| | |
|---|---|
| **Entity** | `fact_warehouse_metering_hourly` |
| **Expression** | `SUM(CREDITS_IDLE)` |
| **Grain** | hour |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 8 h |
| **Owner** | finops |
| **Allocation method** | idle_share |
| **Dimensions** | `metering_hour`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY` |
| **Also known as** | idle, wasted credits, unattributed compute |

Verified questions:
- *idle credits by warehouse*
- *which warehouse wastes the most*

#### `cost.per_query` — Cost per query

Attributed compute credits divided by query count.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `SAFE_RATIO(SUM(CREDITS_ATTRIBUTED), COUNT(*))` |
| **Grain** | day |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 8 h |
| **Owner** | finops |
| **Dimensions** | `fingerprint`, `query_type`, `team`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY` |
| **Also known as** | unit cost per query, average query cost |

#### `cost.per_tb_scanned` — Cost per TB scanned

Attributed credits per terabyte scanned — a unit-cost trend line.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `SAFE_RATIO(SUM(CREDITS_ATTRIBUTED), SUM(BYTES_SCANNED) / 1099511627776.0)` |
| **Grain** | day |
| **Format** | number (credits/TB) |
| **Direction** | lower is better |
| **Freshness floor** | 8 h |
| **Owner** | finops |
| **Dimensions** | `team`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY` |
| **Also known as** | unit cost per terabyte, scan efficiency cost |

#### `cost.platform_self_cost` — Platform self-cost

Credits consumed by this platform's own queries, identified by its query tag. The NFR is that the platform costs under 2% of the spend it observes — the watcher's own bill, reported honestly.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `SUM(CASE WHEN REGEX_CONTAINS(COALESCE(QUERY_TAG_TEAM, ''), '^SNOWOBS') THEN CREDITS_ATTRIBUTED ELSE 0 END)` |
| **Grain** | day |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 8 h |
| **Owner** | platform |
| **Dimensions** | `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY` |
| **Also known as** | tool cost, observability platform cost, self cost |

#### `cost.spend_usd` — Spend in currency

Spend in contract currency from ORGANIZATION_USAGE.USAGE_IN_CURRENCY_DAILY. Values restate until month close, so recent figures carry a provisional badge.

| | |
|---|---|
| **Entity** | `fact_spend_currency_daily` |
| **Expression** | `SUM(SPEND_IN_CURRENCY)` |
| **Grain** | day |
| **Format** | currency (USD) |
| **Direction** | lower is better |
| **Freshness floor** | 3 d |
| **Owner** | finops |
| **Provisional window** | 35 days (restatement) |
| **Dimensions** | `account`, `usage_day`, `usage_type` |
| **Required sources** | `SNOWFLAKE.ORGANIZATION_USAGE.USAGE_IN_CURRENCY_DAILY` |
| **Also known as** | spend, dollars, cost in currency, bill |

Verified questions:
- *spend this month*
- *spend by account last quarter*

#### `cost.top5_concentration` — Spend concentration (top 5 warehouses)

Share of compute credits consumed by the five largest warehouses — how concentrated the bill is, and therefore how much a single change can move it.

| | |
|---|---|
| **Entity** | `fact_warehouse_metering_hourly` |
| **Expression** | `SAFE_RATIO( SUM(CASE WHEN WAREHOUSE_RANK <= 5 THEN CREDITS_COMPUTE ELSE 0 END), SUM(CREDITS_COMPUTE) )` |
| **Grain** | day |
| **Format** | percent |
| **Direction** | neutral |
| **Freshness floor** | 3 h |
| **Owner** | finops |
| **Dimensions** | `metering_hour` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY` |
| **Also known as** | concentration, top 5 share |

#### `cost.total_credits` — Total credits consumed

Total Snowflake credits consumed, from METERING_DAILY_HISTORY. Includes compute, cloud services (raw, before the daily 10% adjustment), and serverless.

| | |
|---|---|
| **Entity** | `fact_cost_daily` |
| **Expression** | `SUM(CREDITS_USED)` |
| **Grain** | day |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 3 h |
| **Owner** | finops |
| **Dimensions** | `service_type`, `usage_day` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY` |
| **Also known as** | credits, credit consumption, total credits |

Verified questions:
- *total credits last 30 days*
- *credits by service type this month*

#### `cost.unattributed_share` — Unattributed spend share

Fraction of attributed credits carrying no team tag. The FinOps target is below 5%; the leaderboard of contributing warehouses is public by design.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `SAFE_RATIO( SUM(CASE WHEN QUERY_TAG_TEAM IS NULL THEN CREDITS_ATTRIBUTED ELSE 0 END), SUM(CREDITS_ATTRIBUTED) )` |
| **Grain** | day |
| **Format** | percent |
| **Direction** | lower is better |
| **Freshness floor** | 8 h |
| **Owner** | finops |
| **Dimensions** | `database`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY` |
| **Thresholds** | warn: 0.05, critical: 0.15 |
| **Also known as** | untagged spend, unattributed percentage, tagging coverage gap |

Verified questions:
- *how much spend is untagged*
- *untagged spend by warehouse*

## D2 — Warehouse & compute efficiency

| KPI | Name | Freshness floor | Direction |
|---|---|---|---|
| `wh.autosuspend_seconds` | Configured auto-suspend | point-in-time | lower is better |
| `wh.avg_execution_ms` | Average execution time | 45 min | lower is better |
| `wh.cloud_services_credits` | Cloud services credits by warehouse | 3 h | lower is better |
| `wh.credits_per_query` | Credits per query by warehouse | 8 h | lower is better |
| `wh.idle_pct` | Idle credit share | 8 h | lower is better |
| `wh.max_clusters` | Configured max clusters | point-in-time | neutral |
| `wh.query_count` | Queries per warehouse | 45 min | neutral |
| `wh.queue_overload_pct` | Queue overload time share | 45 min | lower is better |
| `wh.queue_provisioning_pct` | Queue provisioning time share | 45 min | lower is better |
| `wh.spill_query_share` | Share of queries spilling | 45 min | lower is better |
| `wh.utilisation_pct` | Warehouse utilisation | 8 h | higher is better |
| `wh.zombie_credits` | Zombie warehouse credits | 8 h | lower is better |

#### `wh.autosuspend_seconds` — Configured auto-suspend

Configured auto-suspend delay. Policy is ≤60 s for ELT warehouses and ≤300 s for BI warehouses; longer settings burn idle credits between queries.

| | |
|---|---|
| **Entity** | `dim_warehouse` |
| **Expression** | `MAX(AUTO_SUSPEND_SECONDS)` |
| **Grain** | day |
| **Format** | integer (seconds) |
| **Direction** | lower is better |
| **Freshness floor** | point-in-time |
| **Owner** | platform |
| **Dimensions** | `configured_size`, `warehouse` |
| **Required sources** | `SHOW WAREHOUSES` |
| **Thresholds** | warn: 300.0, critical: 600.0 |
| **Also known as** | auto suspend, suspend setting |

Verified questions:
- *warehouses with long auto-suspend*

#### `wh.avg_execution_ms` — Average execution time

Mean execution time per query on the warehouse.

| | |
|---|---|
| **Entity** | `fact_warehouse_load_hourly` |
| **Expression** | `SAFE_RATIO(SUM(EXECUTION_MS), SUM(QUERY_COUNT))` |
| **Grain** | hour |
| **Format** | duration_ms |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `load_hour`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` |
| **Also known as** | average runtime per warehouse |

#### `wh.cloud_services_credits` — Cloud services credits by warehouse

Raw per-warehouse cloud-services credits. The daily 10% adjustment is an account-level calculation and is deliberately NOT applied here — summing this column does not give a billable figure (see cost.cloud_services_credits).

| | |
|---|---|
| **Entity** | `fact_warehouse_metering_hourly` |
| **Expression** | `SUM(CREDITS_CLOUD_SERVICES)` |
| **Grain** | hour |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 3 h |
| **Owner** | platform |
| **Dimensions** | `metering_hour`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY` |
| **Also known as** | per-warehouse cloud services |

#### `wh.credits_per_query` — Credits per query by warehouse

Attributed credits per query, sliced by warehouse and size.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `SAFE_RATIO(SUM(CREDITS_ATTRIBUTED), COUNT(*))` |
| **Grain** | day |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 8 h |
| **Owner** | platform |
| **Dimensions** | `warehouse`, `warehouse_size` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY` |
| **Also known as** | per query cost by warehouse |

#### `wh.idle_pct` — Idle credit share

Metered credits with no attributable query, as a share of compute.

| | |
|---|---|
| **Entity** | `fact_warehouse_metering_hourly` |
| **Expression** | `SAFE_RATIO(SUM(CREDITS_IDLE), SUM(CREDITS_COMPUTE))` |
| **Grain** | hour |
| **Format** | percent |
| **Direction** | lower is better |
| **Freshness floor** | 8 h |
| **Owner** | platform |
| **Dimensions** | `metering_hour`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY` |
| **Thresholds** | warn: 0.4, critical: 0.6 |
| **Also known as** | idle percentage, waste percentage |

#### `wh.max_clusters` — Configured max clusters

Maximum clusters a multi-cluster warehouse may scale out to.

| | |
|---|---|
| **Entity** | `dim_warehouse` |
| **Expression** | `MAX(MAX_CLUSTERS)` |
| **Grain** | day |
| **Format** | integer |
| **Direction** | neutral |
| **Freshness floor** | point-in-time |
| **Owner** | platform |
| **Dimensions** | `scaling_policy`, `warehouse` |
| **Required sources** | `SHOW WAREHOUSES` |
| **Also known as** | multi cluster limit, scale out limit |

#### `wh.query_count` — Queries per warehouse

Queries executed per warehouse-hour.

| | |
|---|---|
| **Entity** | `fact_warehouse_load_hourly` |
| **Expression** | `SUM(QUERY_COUNT)` |
| **Grain** | hour |
| **Format** | integer |
| **Direction** | neutral |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `load_hour`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` |
| **Also known as** | warehouse query volume |

#### `wh.queue_overload_pct` — Queue overload time share

Share of total elapsed query time spent queued because the warehouse was already saturated — the signal that a warehouse is under-provisioned.

| | |
|---|---|
| **Entity** | `fact_warehouse_load_hourly` |
| **Expression** | `SAFE_RATIO(SUM(QUEUED_OVERLOAD_MS), SUM(ELAPSED_MS))` |
| **Grain** | hour |
| **Format** | percent |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `load_hour`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` |
| **Thresholds** | warn: 0.05, critical: 0.15 |
| **Also known as** | queueing, queue time, overload queueing |

Verified questions:
- *which warehouses are queueing*
- *queue time last week*

#### `wh.queue_provisioning_pct` — Queue provisioning time share

Share of elapsed time spent waiting for compute to be provisioned.

| | |
|---|---|
| **Entity** | `fact_warehouse_load_hourly` |
| **Expression** | `SAFE_RATIO(SUM(QUEUED_PROVISIONING_MS), SUM(ELAPSED_MS))` |
| **Grain** | hour |
| **Format** | percent |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `load_hour`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` |
| **Also known as** | provisioning delay, cold start time |

#### `wh.spill_query_share` — Share of queries spilling

Fraction of queries spilling to storage — the signal that a warehouse is under-sized for its workload, as opposed to merely busy.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `SAFE_RATIO( SUM(CASE WHEN BYTES_SPILLED_LOCAL > 0 OR BYTES_SPILLED_REMOTE > 0 THEN 1 ELSE 0 END), COUNT(*) )` |
| **Grain** | day |
| **Format** | percent |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `warehouse`, `warehouse_size` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` |
| **Thresholds** | warn: 0.02, critical: 0.1 |
| **Also known as** | spilling queries, memory pressure |

#### `wh.utilisation_pct` — Warehouse utilisation

Share of metered compute credits that queries actually accounted for. Low utilisation with no queueing is the signature of an over-sized warehouse.

| | |
|---|---|
| **Entity** | `fact_warehouse_metering_hourly` |
| **Expression** | `SAFE_RATIO(SUM(CREDITS_ATTRIBUTED), SUM(CREDITS_COMPUTE))` |
| **Grain** | hour |
| **Format** | percent |
| **Direction** | higher is better |
| **Freshness floor** | 8 h |
| **Owner** | platform |
| **Dimensions** | `metering_hour`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY` |
| **Thresholds** | warn: 0.4, critical: 0.25 |
| **Also known as** | utilisation, utilization, warehouse efficiency, busy fraction |

Verified questions:
- *warehouse utilisation last 30 days*
- *least utilised warehouses*

#### `wh.zombie_credits` — Zombie warehouse credits

Credits metered in hours where no query was attributed at all — a warehouse running with nothing to do.

| | |
|---|---|
| **Entity** | `fact_warehouse_metering_hourly` |
| **Expression** | `SUM(CASE WHEN CREDITS_ATTRIBUTED = 0 THEN CREDITS_COMPUTE ELSE 0 END)` |
| **Grain** | hour |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 8 h |
| **Owner** | platform |
| **Dimensions** | `metering_hour`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY` |
| **Also known as** | zombie warehouses, credits with no queries, dead warehouses |

Verified questions:
- *zombie warehouses*
- *warehouses burning credits with no queries*

## D3 — Query & workload performance

| KPI | Name | Freshness floor | Direction |
|---|---|---|---|
| `q.bytes_scanned` | Bytes scanned | 45 min | lower is better |
| `q.cache_hit_rate` | Result cache hit rate | 45 min | higher is better |
| `q.compilation_share` | Compilation time share | 45 min | lower is better |
| `q.failure_rate` | Query failure rate | 45 min | lower is better |
| `q.full_scan_count` | Full-scan queries | 45 min | lower is better |
| `q.offender_credits` | Fingerprint cost (offender ranking) | 8 h | lower is better |
| `q.p50_elapsed_ms` | p50 elapsed time | 45 min | lower is better |
| `q.p95_elapsed_ms` | p95 elapsed time | 45 min | lower is better |
| `q.p99_elapsed_ms` | p99 elapsed time | 45 min | lower is better |
| `q.pruning_efficiency` | Pruning efficiency | 45 min | higher is better |
| `q.queue_share` | Queue time share | 45 min | lower is better |
| `q.spill_local_bytes` | Local spill bytes | 45 min | lower is better |
| `q.spill_remote_bytes` | Remote spill bytes | 45 min | lower is better |
| `q.volume` | Query volume | 45 min | neutral |

#### `q.bytes_scanned` — Bytes scanned

Total bytes read by queries.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `SUM(BYTES_SCANNED)` |
| **Grain** | day |
| **Format** | bytes |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `database`, `fingerprint`, `team`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` |
| **Also known as** | data scanned, scan volume |

#### `q.cache_hit_rate` — Result cache hit rate

Mean share of a query's scan served from cache. Repeated dashboard refreshes against unchanged data should be near 1; a low value on identical queries means the cache is being defeated.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `SAFE_RATIO(SUM(CACHE_FRACTION), COUNT(*))` |
| **Grain** | day |
| **Format** | percent |
| **Direction** | higher is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `query_type`, `team`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` |
| **Also known as** | cache usage, result cache |

#### `q.compilation_share` — Compilation time share

Share of elapsed time spent compiling rather than executing. A high share on short queries points at metadata or complexity overhead, not compute size.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `SAFE_RATIO(SUM(COMPILATION_MS), SUM(ELAPSED_MS))` |
| **Grain** | day |
| **Format** | percent |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `query_type`, `team`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` |
| **Also known as** | compile time, planning overhead |

#### `q.failure_rate` — Query failure rate

Share of queries that did not complete successfully.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `SAFE_RATIO( SUM(CASE WHEN EXECUTION_STATUS <> 'SUCCESS' THEN 1 ELSE 0 END), COUNT(*) )` |
| **Grain** | day |
| **Format** | percent |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `database`, `error_class`, `team`, `user`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` |
| **Thresholds** | warn: 0.02, critical: 0.05 |
| **Also known as** | failures, error rate, failed queries |

Verified questions:
- *query failure rate this week*
- *failures by error class*

#### `q.full_scan_count` — Full-scan queries

Queries scanning ≥95% of available micro-partitions.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `SUM(CASE WHEN PARTITIONS_TOTAL > 0 AND PARTITIONS_SCANNED >= PARTITIONS_TOTAL * 0.95 THEN 1 ELSE 0 END)` |
| **Grain** | day |
| **Format** | integer |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `fingerprint`, `team`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` |
| **Also known as** | table scans, full scans |

#### `q.offender_credits` — Fingerprint cost (offender ranking)

Attributed credits per query fingerprint — the ranking that drives the optimisation backlog. Fingerprints, not individual queries, because the same parameterised statement running 10,000 times is one problem, not 10,000.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `SUM(CREDITS_ATTRIBUTED)` |
| **Grain** | day |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 8 h |
| **Owner** | platform |
| **Dimensions** | `fingerprint`, `team`, `user`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY` |
| **Also known as** | top queries by cost, expensive queries, offenders |

Verified questions:
- *most expensive query fingerprints last 30 days*
- *top cost offenders for TEAM_DATA_ENG*

#### `q.p50_elapsed_ms` — p50 elapsed time

Median end-to-end query duration.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `PERCENTILE(0.5, ELAPSED_MS)` |
| **Grain** | day |
| **Format** | duration_ms |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `fingerprint`, `query_type`, `team`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` |
| **Also known as** | median query time, typical latency |

#### `q.p95_elapsed_ms` — p95 elapsed time

95th-percentile query duration. Snowflake computes this approximately and DuckDB exactly; the tolerance is documented in docs/PARITY_EXCEPTIONS.md.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `PERCENTILE(0.95, ELAPSED_MS)` |
| **Grain** | day |
| **Format** | duration_ms |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `fingerprint`, `query_type`, `team`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` |
| **Also known as** | p95 latency, tail latency |

Verified questions:
- *p95 query time by warehouse*

#### `q.p99_elapsed_ms` — p99 elapsed time

99th-percentile query duration (approximate on Snowflake).

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `PERCENTILE(0.99, ELAPSED_MS)` |
| **Grain** | day |
| **Format** | duration_ms |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `query_type`, `team`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` |
| **Also known as** | p99 latency, worst case latency |

#### `q.pruning_efficiency` — Pruning efficiency

Fraction of micro-partitions the optimiser skipped. A collapse here — the same query suddenly scanning everything — is the classic cause of a query cost regression, and is what the offender detector looks for.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `1 - SAFE_RATIO(SUM(PARTITIONS_SCANNED), SUM(PARTITIONS_TOTAL))` |
| **Grain** | day |
| **Format** | percent |
| **Direction** | higher is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `database`, `fingerprint`, `team`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` |
| **Thresholds** | warn: 0.7, critical: 0.4 |
| **Also known as** | partition pruning, pruning, scan efficiency |

Verified questions:
- *worst pruning fingerprints*
- *which queries scan every partition*

#### `q.queue_share` — Queue time share

Share of elapsed time spent queued rather than running.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `SAFE_RATIO(SUM(QUEUED_OVERLOAD_MS + QUEUED_PROVISIONING_MS), SUM(ELAPSED_MS))` |
| **Grain** | day |
| **Format** | percent |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `team`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` |
| **Also known as** | waiting time, queue share |

#### `q.spill_local_bytes` — Local spill bytes

Bytes spilled to local SSD — the first sign of memory pressure.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `SUM(BYTES_SPILLED_LOCAL)` |
| **Grain** | day |
| **Format** | bytes |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `fingerprint`, `team`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` |
| **Also known as** | local spill, disk spill |

#### `q.spill_remote_bytes` — Remote spill bytes

Bytes spilled to remote storage — a fire alarm, not a warning: the query is orders of magnitude slower than it should be and the warehouse is far too small for it.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `SUM(BYTES_SPILLED_REMOTE)` |
| **Grain** | day |
| **Format** | bytes |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `fingerprint`, `team`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` |
| **Thresholds** | warn: 1073741824.0, critical: 10737418240.0 |
| **Also known as** | remote spill, spill to remote storage |

Verified questions:
- *which queries spill to remote storage*
- *remote spill by warehouse*

#### `q.volume` — Query volume

Number of queries executed.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `COUNT(*)` |
| **Grain** | day |
| **Format** | integer |
| **Direction** | neutral |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `database`, `execution_status`, `query_type`, `team`, `user`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` |
| **Also known as** | queries, query count, workload volume |

Verified questions:
- *how many queries ran yesterday*
- *query volume by team*

## D4 — Storage & data lifecycle

| KPI | Name | Freshness floor | Direction |
|---|---|---|---|
| `storage.active_bytes` | Active storage | 3 h | neutral |
| `storage.clone_retained_bytes` | Clone-retained storage | 1.5 h | lower is better |
| `storage.failsafe_bytes` | Fail-safe storage | 3 h | lower is better |
| `storage.growth_rate` | Storage growth rate | 3 h | neutral |
| `storage.stale_table_bytes` | Stale table storage | 1.5 h | lower is better |
| `storage.time_travel_bytes` | Time Travel storage | 1.5 h | lower is better |
| `storage.time_travel_ratio` | Time Travel ratio | 1.5 h | lower is better |
| `storage.top_tables_bytes` | Table storage (all components) | 1.5 h | lower is better |

#### `storage.active_bytes` — Active storage

Daily average bytes of live table data per database — the storage a query can actually read. Snowflake averages over the day, so a table created and dropped within one day shows as a fraction of its real size rather than not at all.

| | |
|---|---|
| **Entity** | `fact_storage_daily` |
| **Expression** | `SUM(ACTIVE_BYTES)` |
| **Grain** | day |
| **Format** | bytes |
| **Direction** | neutral |
| **Freshness floor** | 3 h |
| **Owner** | finops |
| **Dimensions** | `database`, `usage_day` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.DATABASE_STORAGE_USAGE_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.STORAGE_USAGE` |
| **Also known as** | storage, data volume, active bytes, database size |

Verified questions:
- *storage by database*
- *how much data do we store*

#### `storage.clone_retained_bytes` — Clone-retained storage

Bytes a table keeps alive solely because a clone still references them. Zero-copy cloning is free at creation and stops being free the moment either side diverges; an un-dropped clone of a large table is one of the few ways storage grows with nobody having written anything.

| | |
|---|---|
| **Entity** | `dim_table_storage` |
| **Expression** | `SUM(RETAINED_FOR_CLONE_BYTES)` |
| **Grain** | day |
| **Format** | bytes |
| **Direction** | lower is better |
| **Freshness floor** | 1.5 h |
| **Owner** | finops |
| **Dimensions** | `clone_group`, `database`, `table`, `table_schema` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.TABLE_STORAGE_METRICS` |
| **Also known as** | clone storage, retained for clone, zero copy clone cost |

Verified questions:
- *clone storage by table*
- *which clones are costing us*

#### `storage.failsafe_bytes` — Fail-safe storage

Daily average Fail-safe bytes per database — the seven-day, non-configurable window Snowflake keeps after Time Travel expires. It cannot be shortened or disabled, so the only lever on this figure is writing less churn; transient and temporary tables have no Fail-safe at all.

| | |
|---|---|
| **Entity** | `fact_storage_daily` |
| **Expression** | `SUM(FAILSAFE_BYTES)` |
| **Grain** | day |
| **Format** | bytes |
| **Direction** | lower is better |
| **Freshness floor** | 3 h |
| **Owner** | finops |
| **Dimensions** | `database`, `usage_day` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.DATABASE_STORAGE_USAGE_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.STORAGE_USAGE` |
| **Also known as** | failsafe, fail-safe, disaster recovery storage |

#### `storage.growth_rate` — Storage growth rate

Day-over-day change in active bytes, per database. The prior day's figure travels on each row, so this is a single-pass aggregate rather than a self-join; a database's first observed day compares against itself and therefore contributes zero growth rather than an infinite one.

| | |
|---|---|
| **Entity** | `fact_storage_daily` |
| **Expression** | `SAFE_RATIO( SUM(ACTIVE_BYTES - COALESCE(ACTIVE_BYTES_PRIOR_DAY, ACTIVE_BYTES)), SUM(COALESCE(ACTIVE_BYTES_PRIOR_DAY, ACTIVE_BYTES)) )` |
| **Grain** | day |
| **Format** | percent |
| **Direction** | neutral |
| **Freshness floor** | 3 h |
| **Owner** | finops |
| **Dimensions** | `database`, `usage_day` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.DATABASE_STORAGE_USAGE_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.STORAGE_USAGE` |
| **Thresholds** | warn: 0.02, critical: 0.05 |
| **Also known as** | storage growth, data growth, growth by database |

Verified questions:
- *fastest growing databases*
- *storage growth last 30 days*

#### `storage.stale_table_bytes` — Stale table storage

Total bytes held by tables with no recorded change in 90 days — the candidates for archival or deletion. TABLE_STORAGE_METRICS carries no LAST_ALTERED (that column belongs to ACCOUNT_USAGE.TABLES), so the age is measured from the newest change stamp the storage snapshot does publish. That makes this a lower bound: every table it names is stale, but a table it passes over may still be.

| | |
|---|---|
| **Entity** | `dim_table_storage` |
| **Expression** | `SUM(CASE WHEN DAYS_SINCE_LAST_CHANGE >= 90 THEN TOTAL_BYTES ELSE 0 END)` |
| **Grain** | day |
| **Format** | bytes |
| **Direction** | lower is better |
| **Freshness floor** | 1.5 h |
| **Owner** | finops |
| **Dimensions** | `database`, `is_transient`, `table`, `table_schema` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.TABLE_STORAGE_METRICS` |
| **Also known as** | stale tables, abandoned tables, unused storage, cold data |

Verified questions:
- *stale tables*
- *which tables have not changed in 90 days*

> Land ACCOUNT_USAGE.TABLES and re-source the age from LAST_ALTERED to make this exact rather than conservative.

#### `storage.time_travel_bytes` — Time Travel storage

Bytes held to satisfy Time Travel retention. This is billed storage that no query reads in normal operation; it is insurance, and the premium is set by DATA_RETENTION_TIME_IN_DAYS on each table.

| | |
|---|---|
| **Entity** | `dim_table_storage` |
| **Expression** | `SUM(TIME_TRAVEL_BYTES)` |
| **Grain** | day |
| **Format** | bytes |
| **Direction** | lower is better |
| **Freshness floor** | 1.5 h |
| **Owner** | finops |
| **Dimensions** | `database`, `retention_policy`, `table`, `table_schema` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.TABLE_STORAGE_METRICS` |
| **Also known as** | time travel, retention storage, historical versions |

Verified questions:
- *time travel storage by database*
- *which tables hold the most history*

#### `storage.time_travel_ratio` — Time Travel ratio

Time Travel bytes as a fraction of live data. The policy is that non-prod databases carry at most 10%: a ratio far above that means either a long retention setting on a high-churn table or a full rewrite where a merge would do. Both are fixable without touching the data itself.

| | |
|---|---|
| **Entity** | `dim_table_storage` |
| **Expression** | `SAFE_RATIO(SUM(TIME_TRAVEL_BYTES), SUM(ACTIVE_BYTES))` |
| **Grain** | day |
| **Format** | percent |
| **Direction** | lower is better |
| **Freshness floor** | 1.5 h |
| **Owner** | finops |
| **Dimensions** | `database`, `is_transient`, `retention_policy`, `table_schema` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.TABLE_STORAGE_METRICS` |
| **Thresholds** | warn: 0.1, critical: 0.25 |
| **Also known as** | time travel percentage, retention compliance, time travel vs active |

Verified questions:
- *time travel ratio by database*
- *which databases breach the retention policy*

#### `storage.top_tables_bytes` — Table storage (all components)

Active plus Time Travel plus Fail-safe plus clone-retained bytes per table — the whole of what a table costs, not just what it holds. Ranked descending this is the storage optimisation backlog, and the four components tell you which lever to pull on each row.

| | |
|---|---|
| **Entity** | `dim_table_storage` |
| **Expression** | `SUM(TOTAL_BYTES)` |
| **Grain** | day |
| **Format** | bytes |
| **Direction** | lower is better |
| **Freshness floor** | 1.5 h |
| **Owner** | finops |
| **Dimensions** | `database`, `is_transient`, `retention_policy`, `table`, `table_schema` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.TABLE_STORAGE_METRICS` |
| **Also known as** | biggest tables, top tables by storage, table size ranking |

Verified questions:
- *largest tables by total storage*
- *top 20 tables by storage*

## D5 — Pipeline & orchestration reliability

| KPI | Name | Freshness floor | Direction |
|---|---|---|---|
| `pipe.dt_lag_breaches` | Dynamic table lag breaches | 3 h | lower is better |
| `pipe.dt_lag_vs_target` | Dynamic table lag vs target | 3 h | lower is better |
| `pipe.dt_refresh_failures` | Dynamic table refresh failures | 3 h | lower is better |
| `pipe.repeat_failure_tasks` | Repeat-failure tasks | 45 min | lower is better |
| `pipe.root_failures` | Root task failures | 45 min | lower is better |
| `pipe.serverless_task_credits` | Serverless task credits | 3 h | lower is better |
| `pipe.skipped_downstream` | Skipped downstream runs | 45 min | lower is better |
| `pipe.task_duration_p95` | p95 task duration | 45 min | lower is better |
| `pipe.task_failures` | Task failures | 45 min | lower is better |
| `pipe.task_success_rate` | Task success rate | 45 min | higher is better |

#### `pipe.dt_lag_breaches` — Dynamic table lag breaches

Refreshes that started against data older than the table's TARGET_LAG. Refreshes with no declared target are excluded rather than counted as passing — an unstated SLA is not a met one.

| | |
|---|---|
| **Entity** | `fact_dynamic_table_refresh` |
| **Expression** | `SUM(CASE WHEN TARGET_LAG_SEC IS NOT NULL AND ACTUAL_LAG_SEC > TARGET_LAG_SEC THEN 1 ELSE 0 END)` |
| **Grain** | day |
| **Format** | integer |
| **Direction** | lower is better |
| **Freshness floor** | 3 h |
| **Owner** | platform |
| **Dimensions** | `database`, `dynamic_table`, `sla_status`, `table_schema` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.DYNAMIC_TABLE_REFRESH_HISTORY` |
| **Also known as** | lag breaches, stale dynamic tables, target lag misses |

Verified questions:
- *lag breaches by dynamic table*
- *which dynamic tables breached yesterday*

#### `pipe.dt_lag_vs_target` — Dynamic table lag vs target

Actual lag as a fraction of declared TARGET_LAG. At or below 100% the table is meeting the freshness contract it advertises; above it, downstream consumers are reading data older than they were promised. Expressed as a ratio rather than raw seconds so tables with different targets are comparable on one axis.

| | |
|---|---|
| **Entity** | `fact_dynamic_table_refresh` |
| **Expression** | `SAFE_RATIO(SUM(ACTUAL_LAG_SEC), SUM(TARGET_LAG_SEC))` |
| **Grain** | day |
| **Format** | percent |
| **Direction** | lower is better |
| **Freshness floor** | 3 h |
| **Owner** | platform |
| **Dimensions** | `database`, `dynamic_table`, `refresh_action`, `table_schema` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.DYNAMIC_TABLE_REFRESH_HISTORY` |
| **Thresholds** | warn: 0.8, critical: 1.0 |
| **Also known as** | dynamic table lag, dt lag, target lag ratio, freshness vs target |

Verified questions:
- *dynamic tables missing their target lag*
- *dt lag ratio this week*

#### `pipe.dt_refresh_failures` — Dynamic table refresh failures

Refreshes that did not succeed, including UPSTREAM_FAILED — a dynamic table whose source failed is just as stale as one that failed itself, and the consumer cannot tell the difference.

| | |
|---|---|
| **Entity** | `fact_dynamic_table_refresh` |
| **Expression** | `SUM(CASE WHEN REFRESH_STATE <> 'SUCCEEDED' THEN 1 ELSE 0 END)` |
| **Grain** | day |
| **Format** | integer |
| **Direction** | lower is better |
| **Freshness floor** | 3 h |
| **Owner** | platform |
| **Dimensions** | `database`, `dynamic_table`, `refresh_action`, `refresh_state` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.DYNAMIC_TABLE_REFRESH_HISTORY` |
| **Also known as** | dt failures, failed refreshes, dynamic table errors |

#### `pipe.repeat_failure_tasks` — Repeat-failure tasks

Number of distinct tasks that have failed more than once over the retained history. A single failure is an event; a second one is a defect, and this is the count that should be shrinking rather than the raw failure total.

| | |
|---|---|
| **Entity** | `fact_task_run` |
| **Expression** | `COUNT(DISTINCT CASE WHEN STATE = 'FAILED' AND TASK_FAILURE_COUNT > 1 THEN TASK_NAME END)` |
| **Grain** | day |
| **Format** | integer |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `database`, `graph_root`, `task_schema` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.TASK_HISTORY` |
| **Also known as** | chronic failures, flaky tasks, repeatedly failing tasks |

Verified questions:
- *tasks that keep failing*
- *chronic task failures*

#### `pipe.root_failures` — Root task failures

Failures at the root of a task graph — the incidents worth paging on. A root failure suspends everything beneath it, so this count is what an alert should carry while the downstream SKIPPED runs stay context rather than separate notifications.

| | |
|---|---|
| **Entity** | `fact_task_run` |
| **Expression** | `SUM(CASE WHEN STATE = 'FAILED' AND TASK_NAME = GRAPH_ROOT_TASK_ID THEN 1 ELSE 0 END)` |
| **Grain** | day |
| **Format** | integer |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `database`, `error_class`, `graph_root`, `task` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.TASK_HISTORY` |
| **Thresholds** | warn: 1.0, critical: 3.0 |
| **Also known as** | root cause failures, dag root failures, upstream failures |

Verified questions:
- *root task failures*
- *which DAG root failed last night*

#### `pipe.serverless_task_credits` — Serverless task credits

Credits consumed by tasks running on Snowflake-managed compute. These never appear against a warehouse, so every warehouse-shaped cost view misses them entirely — which is how serverless spend grows for months without anyone noticing it in the warehouse leaderboard.

| | |
|---|---|
| **Entity** | `fact_serverless_daily` |
| **Expression** | `SUM(CREDITS_USED)` |
| **Grain** | day |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 3 h |
| **Owner** | finops |
| **Dimensions** | `database`, `task`, `task_schema`, `usage_day` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.SERVERLESS_TASK_HISTORY` |
| **Also known as** | serverless credits, serverless tasks, managed compute cost |

Verified questions:
- *serverless task credits this month*
- *most expensive serverless tasks*

#### `pipe.skipped_downstream` — Skipped downstream runs

Runs skipped because something upstream failed. Read against pipe.root_failures this is the blast radius of an incident: one root failure, twelve tables that did not refresh.

| | |
|---|---|
| **Entity** | `fact_task_run` |
| **Expression** | `SUM(CASE WHEN STATE = 'SKIPPED' THEN 1 ELSE 0 END)` |
| **Grain** | day |
| **Format** | integer |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `database`, `graph_root`, `task`, `task_schema` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.TASK_HISTORY` |
| **Also known as** | skipped tasks, suspended graph, downstream impact |

Verified questions:
- *how many tasks were skipped*
- *blast radius of last night's failure*

#### `pipe.task_duration_p95` — p95 task duration

95th-percentile wall-clock duration from scheduled time to completion, per task. Measured from *scheduled* rather than started time deliberately: a task that waits ten minutes for a warehouse has been late by ten minutes, whatever its statement timing says.
The percentile is taken from a nearest-rank window computed over the task's retained history rather than through the PERCENTILE shim, because Snowflake estimates percentiles from a t-digest and DuckDB computes them exactly — a difference no task-duration tail has enough observations to absorb.

| | |
|---|---|
| **Entity** | `fact_task_run` |
| **Expression** | `MAX(CASE WHEN DURATION_PERCENT_RANK <= 0.95 THEN DURATION_SEC END)` |
| **Grain** | day |
| **Format** | number (seconds) |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `database`, `task`, `task_schema` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.TASK_HISTORY` |
| **Also known as** | task runtime, slow tasks, task latency, p95 task time |

Verified questions:
- *slowest tasks*
- *p95 task duration by schema*

#### `pipe.task_failures` — Task failures

Count of task runs that ended in FAILED. Sliced by error class this separates the timeouts from the genuinely broken SQL, which want different responses.

| | |
|---|---|
| **Entity** | `fact_task_run` |
| **Expression** | `SUM(CASE WHEN STATE = 'FAILED' THEN 1 ELSE 0 END)` |
| **Grain** | day |
| **Format** | integer |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `database`, `error_class`, `graph_root`, `task`, `task_schema` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.TASK_HISTORY` |
| **Also known as** | failed tasks, task errors, pipeline failures |

Verified questions:
- *task failures yesterday*
- *failures by error code*

#### `pipe.task_success_rate` — Task success rate

Share of task runs that completed successfully. SKIPPED runs are in the denominator on purpose: a graph that is repeatedly suspended is not delivering data, however healthy its individual statements look.

| | |
|---|---|
| **Entity** | `fact_task_run` |
| **Expression** | `SAFE_RATIO( SUM(CASE WHEN STATE = 'SUCCEEDED' THEN 1 ELSE 0 END), COUNT(*) )` |
| **Grain** | day |
| **Format** | percent |
| **Direction** | higher is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `database`, `graph_root`, `task`, `task_schema` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.TASK_HISTORY` |
| **Thresholds** | warn: 0.98, critical: 0.95 |
| **Also known as** | task reliability, task success, pipeline success rate |

Verified questions:
- *task success rate this week*
- *least reliable tasks*

## D6 — Data quality & freshness

| KPI | Name | Freshness floor | Direction |
|---|---|---|---|
| `dq.freshness_sla_attainment` | Freshness SLA attainment | 3 h | higher is better |
| `dq.minutes_since_task_success` | Minutes since last successful task run | 45 min | lower is better |
| `dq.pipeline_freshness_minutes` | Observability pipeline freshness | 45 min | lower is better |
| `dq.quarantine_rate` | Quarantine rate (query error proxy) | 45 min | lower is better |
| `dq.schema_drift_events` | Schema drift events | 45 min | lower is better |
| `dq.sla_breach_count` | Freshness SLA breaches | 3 h | lower is better |
| `dq.sla_breach_seconds` | Freshness SLA breach duration | 3 h | lower is better |

#### `dq.freshness_sla_attainment` — Freshness SLA attainment

Share of dynamic-table refreshes that met the table's own declared TARGET_LAG. Refreshes on tables with no target are excluded from both sides of the ratio rather than counted as successes — this measures attainment against stated SLAs, not the absence of one.

| | |
|---|---|
| **Entity** | `fact_dynamic_table_refresh` |
| **Expression** | `SAFE_RATIO( SUM(CASE WHEN TARGET_LAG_SEC IS NOT NULL AND ACTUAL_LAG_SEC <= TARGET_LAG_SEC THEN 1 ELSE 0 END), SUM(CASE WHEN TARGET_LAG_SEC IS NOT NULL THEN 1 ELSE 0 END) )` |
| **Grain** | day |
| **Format** | percent |
| **Direction** | higher is better |
| **Freshness floor** | 3 h |
| **Owner** | platform |
| **Dimensions** | `database`, `dynamic_table`, `sla_status`, `table_schema` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.DYNAMIC_TABLE_REFRESH_HISTORY` |
| **Thresholds** | warn: 0.99, critical: 0.95 |
| **Also known as** | freshness sla, sla attainment, on-time refreshes, freshness compliance |

Verified questions:
- *freshness SLA attainment this month*
- *which tables miss their SLA*

#### `dq.minutes_since_task_success` — Minutes since last successful task run

Age of a task's most recent SUCCEEDED run, measured against the newest scheduled run observed anywhere in the data rather than wall-clock time — so the number is reproducible and never blames the reader's clock for a stale extract. A task that has never succeeded returns null, not a large number: unknown staleness stays unknown (R3).

| | |
|---|---|
| **Entity** | `fact_task_run` |
| **Expression** | `SAFE_RATIO(MAX(SECONDS_SINCE_LAST_SUCCESS), 60)` |
| **Grain** | day |
| **Format** | number (minutes) |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `database`, `graph_root`, `task`, `task_schema` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.TASK_HISTORY` |
| **Thresholds** | warn: 1440.0, critical: 2880.0 |
| **Also known as** | task freshness, staleness, time since last success, last good run |

Verified questions:
- *which tables are stale*
- *tasks with no successful run today*

#### `dq.pipeline_freshness_minutes` — Observability pipeline freshness

Age of the newest QUERY_HISTORY row at the moment it landed — how far behind the account this platform's own copy of the data was. It is the number the freshness banner is built on, and the one that decides whether a figure is allowed to be shown at all. A platform that reports on everyone else's staleness has no standing to hide its own; the floor is what ACCOUNT_USAGE documents (45 minutes for QUERY_HISTORY), anything above it is this platform's own doing.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `SAFE_RATIO(EPOCH_SECONDS(MAX(STARTED_AT), MAX(LANDED_AT)), 60)` |
| **Grain** | day |
| **Format** | number (minutes) |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `database`, `team`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` |
| **Thresholds** | warn: 90.0, critical: 180.0 |
| **Also known as** | our own freshness, ingestion lag, pipeline lag, data age |

Verified questions:
- *how fresh is this data*
- *observability pipeline lag*

#### `dq.quarantine_rate` — Quarantine rate (query error proxy)

Share of statements that did not complete, sliced by error class. Snowflake publishes no quarantine or reject-row count, so this stands in for one: a load or transform that rejects data usually fails the statement, and the error class says which kind of badness it was. It is a rate of failed *operations*, not of failed rows — a distinction worth keeping in front of anyone reading it as a data-quality figure.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `SAFE_RATIO( SUM(CASE WHEN EXECUTION_STATUS <> 'SUCCESS' THEN 1 ELSE 0 END), COUNT(*) )` |
| **Grain** | day |
| **Format** | percent |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `database`, `error_class`, `team`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` |
| **Thresholds** | warn: 0.02, critical: 0.05 |
| **Also known as** | quarantined rows, rejected data, bad records, error rate by class |

Verified questions:
- *error rate by error class*
- *which error classes dominate failures*

#### `dq.schema_drift_events` — Schema drift events

Distinct sources in which a query failed with SQL compilation error 000904, "invalid identifier" — the error a query gets when a column it was written against has been renamed, retyped, or removed. This is a proxy for schema drift, not a registry diff: it fires when drift has already broken something, and stays silent about additive drift that has broken nothing yet. Counted per source rather than per query so one changed column in a dashboard refreshed hourly is one event.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `COUNT(DISTINCT CASE WHEN COALESCE(ERROR_CODE, '') = '000904' THEN COALESCE(DATABASE_NAME, 'UNKNOWN') END)` |
| **Grain** | day |
| **Format** | integer |
| **Direction** | lower is better |
| **Freshness floor** | 45 min |
| **Owner** | platform |
| **Dimensions** | `database`, `team`, `user`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` |
| **Thresholds** | warn: 1.0, critical: 3.0 |
| **Also known as** | schema drift, column changes, invalid identifier, breaking changes |

Verified questions:
- *schema drift this week*
- *which databases have breaking column changes*

#### `dq.sla_breach_count` — Freshness SLA breaches

Number of refreshes that missed their declared target lag. Counted per refresh rather than per table so a table that breaches every hour is visibly worse than one that breached once.

| | |
|---|---|
| **Entity** | `fact_dynamic_table_refresh` |
| **Expression** | `SUM(CASE WHEN TARGET_LAG_SEC IS NOT NULL AND ACTUAL_LAG_SEC > TARGET_LAG_SEC THEN 1 ELSE 0 END)` |
| **Grain** | day |
| **Format** | integer |
| **Direction** | lower is better |
| **Freshness floor** | 3 h |
| **Owner** | platform |
| **Dimensions** | `database`, `dynamic_table`, `table_schema` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.DYNAMIC_TABLE_REFRESH_HISTORY` |
| **Also known as** | sla breaches, missed freshness, breach count |

#### `dq.sla_breach_seconds` — Freshness SLA breach duration

Total seconds by which refreshes overran their target lag. Severity, not frequency: one table four hours late and one table four seconds late both register as a single breach, and only this metric tells them apart.

| | |
|---|---|
| **Entity** | `fact_dynamic_table_refresh` |
| **Expression** | `SUM(CASE WHEN TARGET_LAG_SEC IS NOT NULL AND ACTUAL_LAG_SEC > TARGET_LAG_SEC THEN ACTUAL_LAG_SEC - TARGET_LAG_SEC ELSE 0 END)` |
| **Grain** | day |
| **Format** | number (seconds) |
| **Direction** | lower is better |
| **Freshness floor** | 3 h |
| **Owner** | platform |
| **Dimensions** | `database`, `dynamic_table`, `table_schema` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.DYNAMIC_TABLE_REFRESH_HISTORY` |
| **Also known as** | breach severity, how late, seconds over target, lag excess |

Verified questions:
- *worst freshness breaches by duration*

## D7 — Security, access & governance

| KPI | Name | Freshness floor | Direction |
|---|---|---|---|
| `sec.client_type_spread` | Distinct client types per user | 2 h | neutral |
| `sec.disabled_but_granted_users` | Disabled users still holding roles | 2 h | lower is better |
| `sec.distinct_client_ips` | Distinct client IPs per user | 2 h | neutral |
| `sec.dormant_users` | Dormant users | 2 h | lower is better |
| `sec.failed_login_rate` | Failed login rate | 2 h | lower is better |
| `sec.failed_logins` | Failed logins | 2 h | lower is better |
| `sec.new_grants` | New grants in window | 2 h | neutral |
| `sec.privileged_grants` | ACCOUNTADMIN-adjacent grants | 2 h | lower is better |
| `sec.single_factor_logins` | Single-factor logins | 2 h | lower is better |
| `sec.users_without_key_pair` | Users without an RSA key | 2 h | lower is better |

#### `sec.client_type_spread` — Distinct client types per user

Number of distinct reported client types a user authenticated from. It is a coarse fingerprint — REPORTED_CLIENT_TYPE is self-declared by the driver and trivially spoofable — but a service account that has used one driver for a year and suddenly reports two is worth a question.

| | |
|---|---|
| **Entity** | `fact_login` |
| **Expression** | `COUNT(DISTINCT CLIENT_TYPE)` |
| **Grain** | day |
| **Format** | integer |
| **Direction** | neutral |
| **Freshness floor** | 2 h |
| **Owner** | security |
| **Dimensions** | `first_factor`, `user` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY` |
| **Also known as** | new client, client fingerprint, unusual driver, tooling spread |

Verified questions:
- *users connecting from new clients*

#### `sec.disabled_but_granted_users` — Disabled users still holding roles

Disabled accounts that still hold live role grants. Disabling a user stops them logging in but leaves the grant graph untouched, so this is the deprovisioning tail: the difference between "cannot sign in today" and "has no access", which is the difference that matters if the account is ever re-enabled.

| | |
|---|---|
| **Entity** | `fact_grant` |
| **Expression** | `COUNT(DISTINCT CASE WHEN GRANTEE_DISABLED AND REVOKED_AT IS NULL THEN GRANTEE_NAME END)` |
| **Grain** | day |
| **Format** | integer |
| **Direction** | lower is better |
| **Freshness floor** | 2 h |
| **Owner** | security |
| **Dimensions** | `granted_by`, `grantee_type`, `privilege_tier`, `role` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.GRANTS_TO_USERS`, `SNOWFLAKE.ACCOUNT_USAGE.USERS` |
| **Thresholds** | warn: 1.0, critical: 5.0 |
| **Also known as** | disabled but granted, orphaned grants, deprovisioning gap |

Verified questions:
- *disabled users who still have roles*
- *deprovisioning gaps*

#### `sec.distinct_client_ips` — Distinct client IPs per user

Number of distinct source addresses a user authenticated from. A service account pinned to a network allowlist should be a small constant; a jump is either an infrastructure change nobody mentioned or a credential in the wrong hands.

| | |
|---|---|
| **Entity** | `fact_login` |
| **Expression** | `COUNT(DISTINCT CLIENT_IP)` |
| **Grain** | day |
| **Format** | integer |
| **Direction** | neutral |
| **Freshness floor** | 2 h |
| **Owner** | security |
| **Dimensions** | `client_type`, `first_factor`, `user` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY` |
| **Thresholds** | warn: 10.0, critical: 25.0 |
| **Also known as** | source addresses, ip spread, impossible travel, connection origins |

Verified questions:
- *users logging in from many IPs*
- *IP spread for SVC accounts*

#### `sec.dormant_users` — Dormant users

Enabled users with no successful login in 90 days. Every one is a live credential with no owner watching it — the cheapest access risk to remove and the one most often left alone because nobody is sure who it belongs to. Age is measured against the newest login in the snapshot, so the cohort is stable between runs.

| | |
|---|---|
| **Entity** | `dim_user` |
| **Expression** | `COUNT(DISTINCT CASE WHEN DAYS_SINCE_LAST_LOGIN >= 90 AND NOT IS_DISABLED THEN USER_NAME END)` |
| **Grain** | day |
| **Format** | integer |
| **Direction** | lower is better |
| **Freshness floor** | 2 h |
| **Owner** | security |
| **Dimensions** | `account_status`, `credential_type`, `default_role`, `user_type` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.USERS` |
| **Also known as** | inactive users, unused accounts, dormant accounts, stale users |

Verified questions:
- *dormant users*
- *accounts with no login in 90 days*

#### `sec.failed_login_rate` — Failed login rate

Share of authentication attempts that failed. A background rate of a few percent is normal — mistyped passwords, expired sessions. A step change on one user or one client type is not, and that is what the slice is for.

| | |
|---|---|
| **Entity** | `fact_login` |
| **Expression** | `SAFE_RATIO( SUM(CASE WHEN IS_SUCCESS = 'NO' THEN 1 ELSE 0 END), COUNT(*) )` |
| **Grain** | day |
| **Format** | percent |
| **Direction** | lower is better |
| **Freshness floor** | 2 h |
| **Owner** | security |
| **Dimensions** | `client_type`, `error_class`, `first_factor`, `user` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY` |
| **Thresholds** | warn: 0.1, critical: 0.25 |
| **Also known as** | login failure rate, authentication failures, failed auth rate |

Verified questions:
- *failed login rate this week*
- *login failures by client*

#### `sec.failed_logins` — Failed logins

Count of failed authentication attempts. Per user and per source IP this is the shape a credential-stuffing attempt makes: many failures against many users from few addresses, or many failures against one user from many.

| | |
|---|---|
| **Entity** | `fact_login` |
| **Expression** | `SUM(CASE WHEN IS_SUCCESS = 'NO' THEN 1 ELSE 0 END)` |
| **Grain** | day |
| **Format** | integer |
| **Direction** | lower is better |
| **Freshness floor** | 2 h |
| **Owner** | security |
| **Dimensions** | `client_ip`, `client_type`, `error_class`, `user` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY` |
| **Also known as** | failed logins per user, bad password attempts, brute force |

Verified questions:
- *users with the most failed logins*
- *failed logins by IP*

#### `sec.new_grants` — New grants in window

Grants created inside the requested window, from GRANTS_TO_USERS.CREATED_ON. This is the privilege-drift feed: a review reads it in date order and asks who approved each row. Sliced by privilege_tier it separates routine team membership from a new administrator.

| | |
|---|---|
| **Entity** | `fact_grant` |
| **Expression** | `COUNT(*)` |
| **Grain** | day |
| **Format** | integer |
| **Direction** | neutral |
| **Freshness floor** | 2 h |
| **Owner** | security |
| **Dimensions** | `granted_by`, `grantee`, `grantee_type`, `privilege_tier`, `role` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.GRANTS_TO_USERS`, `SNOWFLAKE.ACCOUNT_USAGE.USERS` |
| **Also known as** | privilege drift, new access, grants issued, access changes |

Verified questions:
- *new grants this week*
- *privileged grants issued recently*

#### `sec.privileged_grants` — ACCOUNTADMIN-adjacent grants

Live grants of ACCOUNTADMIN, SECURITYADMIN, or ORGADMIN. These roles can change billing, read every object, and rewrite the access model, so the right number is small, known, and unchanging — and any movement in it should be traceable to a ticket. Revoked grants are excluded so the figure is current holdings rather than everything ever granted.

| | |
|---|---|
| **Entity** | `fact_grant` |
| **Expression** | `SUM(CASE WHEN ROLE_NAME IN ('ACCOUNTADMIN', 'SECURITYADMIN', 'ORGADMIN') AND REVOKED_AT IS NULL THEN 1 ELSE 0 END)` |
| **Grain** | day |
| **Format** | integer |
| **Direction** | lower is better |
| **Freshness floor** | 2 h |
| **Owner** | security |
| **Dimensions** | `granted_by`, `grantee`, `grantee_type`, `role` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.GRANTS_TO_USERS`, `SNOWFLAKE.ACCOUNT_USAGE.USERS` |
| **Thresholds** | warn: 5.0, critical: 10.0 |
| **Also known as** | accountadmin, privileged access, admin roles, super users |

Verified questions:
- *who has ACCOUNTADMIN*
- *privileged role grants*

#### `sec.single_factor_logins` — Single-factor logins

Successful logins that presented no second authentication factor. Snowflake blocks single-factor password sign-in from October 2026, so this is a migration burn-down: every login counted here is one that will stop working. Key-pair service authentication legitimately has no second factor and shows up here too — slice by first_factor to separate the two populations.

| | |
|---|---|
| **Entity** | `fact_login` |
| **Expression** | `SUM(CASE WHEN IS_SUCCESS = 'YES' AND SECOND_AUTH_FACTOR IS NULL THEN 1 ELSE 0 END)` |
| **Grain** | day |
| **Format** | integer |
| **Direction** | lower is better |
| **Freshness floor** | 2 h |
| **Owner** | security |
| **Dimensions** | `client_type`, `first_factor`, `user` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY` |
| **Also known as** | no mfa, single factor, mfa gap, password only logins |

Verified questions:
- *who still logs in without MFA*
- *single factor logins by user*

#### `sec.users_without_key_pair` — Users without an RSA key

Enabled users with no RSA public key configured. Sliced by user_type this is the key-pair rollout backlog for service accounts, which cannot use interactive MFA and must move to key-pair authentication before password sign-in is blocked.

| | |
|---|---|
| **Entity** | `dim_user` |
| **Expression** | `COUNT(DISTINCT CASE WHEN NOT HAS_RSA_PUBLIC_KEY AND NOT IS_DISABLED THEN USER_NAME END)` |
| **Grain** | day |
| **Format** | integer |
| **Direction** | lower is better |
| **Freshness floor** | 2 h |
| **Owner** | security |
| **Dimensions** | `credential_type`, `default_role`, `user_type` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.USERS` |
| **Also known as** | no key pair, password auth, key pair rollout, rsa key gap |

Verified questions:
- *service accounts without a key pair*
- *key pair rollout progress*

## D8 — AI / Cortex & advanced features

| KPI | Name | Freshness floor | Direction |
|---|---|---|---|
| `ai.credits_by_function` | Cortex credits by function | 3 h | lower is better |
| `ai.credits_by_model` | Cortex credits by model | 3 h | lower is better |
| `ai.daily_credits` | Daily AI credits (growth series) | 3 h | lower is better |
| `ai.share_of_credits` | AI share of account credits | 3 h | neutral |
| `ai.tokens_per_credit` | Tokens per credit | 3 h | higher is better |
| `ai.total_credits` | Cortex credits | 3 h | lower is better |
| `ai.total_tokens` | Cortex tokens | 3 h | neutral |

#### `ai.credits_by_function` — Cortex credits by function

Token credits split by Cortex function. COMPLETE and the embedding functions have very different cost profiles per call, so a rise in the total is only actionable once it is attributed to one of them.

| | |
|---|---|
| **Entity** | `fact_ai_usage_daily` |
| **Expression** | `SUM(TOKEN_CREDITS)` |
| **Grain** | day |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 3 h |
| **Owner** | finops |
| **Dimensions** | `ai_function`, `ai_model`, `usage_day` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.CORTEX_FUNCTIONS_USAGE_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY` |
| **Also known as** | credits per function, cortex function cost, which function costs most |

Verified questions:
- *cortex spend by function*
- *which cortex function costs the most*

#### `ai.credits_by_model` — Cortex credits by model

Token credits split by model. Models are priced per million tokens at very different rates, so a workload that silently switched to a larger model can double its cost with no change in call volume — which is the failure mode this slice exists to catch.

| | |
|---|---|
| **Entity** | `fact_ai_usage_daily` |
| **Expression** | `SUM(TOKEN_CREDITS)` |
| **Grain** | day |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 3 h |
| **Owner** | finops |
| **Dimensions** | `ai_function`, `ai_model`, `usage_day` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.CORTEX_FUNCTIONS_USAGE_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY` |
| **Also known as** | credits per model, model cost, llm model spend |

Verified questions:
- *cortex spend by model*
- *which model are we paying for*

#### `ai.daily_credits` — Daily AI credits (growth series)

Token credits per day, bucketed at day grain and ordered for trend analysis. It is deliberately the same sum as ai.total_credits rather than a computed growth percentage: the day-over-day comparison, the growth rate, and the forecast are all derived from this series downstream, and each of them wants the raw daily figure rather than a pre-differenced one.

| | |
|---|---|
| **Entity** | `fact_ai_usage_daily` |
| **Expression** | `SUM(TOKEN_CREDITS)` |
| **Grain** | day |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 3 h |
| **Owner** | finops |
| **Dimensions** | `ai_function`, `ai_model`, `usage_day` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.CORTEX_FUNCTIONS_USAGE_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY` |
| **Also known as** | ai growth, day over day ai spend, ai trend, ai run rate |

Verified questions:
- *daily AI credits*
- *is AI spend growing*

#### `ai.share_of_credits` — AI share of account credits

Cortex token credits as a fraction of the account's total metered credits for the same day. The account total is a per-day constant carried on every row of the entity and is therefore read with MAX rather than SUM — summing it would multiply the denominator by the number of function/model pairs active that day. Declared at day grain for the same reason: at a coarser bucket the single-day constant no longer stands for the period.

| | |
|---|---|
| **Entity** | `fact_ai_usage_daily` |
| **Expression** | `SAFE_RATIO(SUM(TOKEN_CREDITS), MAX(ACCOUNT_CREDITS_DAY))` |
| **Grain** | day |
| **Format** | percent |
| **Direction** | neutral |
| **Freshness floor** | 3 h |
| **Owner** | finops |
| **Dimensions** | `ai_function`, `ai_model`, `usage_day` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.CORTEX_FUNCTIONS_USAGE_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY` |
| **Thresholds** | warn: 0.1, critical: 0.25 |
| **Also known as** | ai share, ai percentage of spend, how much of the bill is ai |

Verified questions:
- *what share of our bill is AI*
- *AI share of credits this month*

#### `ai.tokens_per_credit` — Tokens per credit

Tokens processed per credit spent — the AI unit-cost line, inverted so that higher is better. It is flat for a fixed model and moves the moment the model mix changes, which makes it the earliest signal that AI spend growth is a pricing change rather than an adoption one.

| | |
|---|---|
| **Entity** | `fact_ai_usage_daily` |
| **Expression** | `SAFE_RATIO(SUM(TOKENS), SUM(TOKEN_CREDITS))` |
| **Grain** | day |
| **Format** | number (tokens/credit) |
| **Direction** | higher is better |
| **Freshness floor** | 3 h |
| **Owner** | finops |
| **Dimensions** | `ai_function`, `ai_model`, `usage_day` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.CORTEX_FUNCTIONS_USAGE_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY` |
| **Also known as** | ai unit economics, cost per token, token efficiency, price per token |

Verified questions:
- *tokens per credit by model*
- *is our AI unit cost changing*

#### `ai.total_credits` — Cortex credits

Total token credits consumed by Cortex functions. This is the whole AI compute bill for LLM functions in one figure, independent of which warehouse issued the call.

| | |
|---|---|
| **Entity** | `fact_ai_usage_daily` |
| **Expression** | `SUM(TOKEN_CREDITS)` |
| **Grain** | day |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 3 h |
| **Owner** | finops |
| **Dimensions** | `ai_function`, `ai_model`, `usage_day` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.CORTEX_FUNCTIONS_USAGE_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY` |
| **Also known as** | ai credits, cortex spend, llm cost, ai cost |

Verified questions:
- *cortex credits this month*
- *how much are we spending on AI*

#### `ai.total_tokens` — Cortex tokens

Total tokens processed. This is the workload figure: it tracks what the business actually asked for, unaffected by pricing or model choice, and is the correct denominator for any AI unit-cost question.

| | |
|---|---|
| **Entity** | `fact_ai_usage_daily` |
| **Expression** | `SUM(TOKENS)` |
| **Grain** | day |
| **Format** | integer (tokens) |
| **Direction** | neutral |
| **Freshness floor** | 3 h |
| **Owner** | finops |
| **Dimensions** | `ai_function`, `ai_model`, `usage_day` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.CORTEX_FUNCTIONS_USAGE_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY` |
| **Also known as** | tokens, token volume, ai usage volume |

Verified questions:
- *how many tokens did we process*
- *token volume by function*

## D9 — Chargeback, budget & commitment

| KPI | Name | Freshness floor | Direction |
|---|---|---|---|
| `chargeback.allocated_credits` | Allocated credits by team (direct) | 8 h | lower is better |
| `chargeback.allocation_method_mix` | Allocation method mix | 8 h | neutral |
| `chargeback.budget_variance_credits` | Billed credits vs budget (monthly) | 3 h | lower is better |
| `chargeback.forecast_input_credits` | Daily billed credits (forecast input) | 3 h | lower is better |
| `chargeback.metered_credits` | Metered credits (reconciliation control total) | 3 h | lower is better |
| `chargeback.reconciliation_variance` | Reconciliation variance (metered minus allocated) | 8 h | lower is better |
| `chargeback.spend_by_account` | Spend in currency by account | 3 d | lower is better |
| `chargeback.unattributed_credits` | Unattributed credits | 8 h | lower is better |
| `chargeback.unattributed_share` | Unattributed share of allocation | 8 h | lower is better |

#### `chargeback.allocated_credits` — Allocated credits by team (direct)

Credits allocated to a team by its own query tags — the direct component of the three-part model (direct + idle share + cloud-services share, HLD §10.2). Read alone it under-states a team's true cost, because idle time and cloud services are not attributable per query; it is the component that can be defended query by query, which is why it is stated separately.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `SUM(CREDITS_ATTRIBUTED)` |
| **Grain** | day |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 8 h |
| **Owner** | finops |
| **Allocation method** | direct |
| **Dimensions** | `allocation_method`, `database`, `team`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY` |
| **Also known as** | chargeback, team allocation, showback, allocated cost |

Verified questions:
- *allocated credits by team*
- *chargeback for TEAM_DATA_ENG*

#### `chargeback.allocation_method_mix` — Allocation method mix

Query count split by how the query's credits reach a team — DIRECT_TAG for queries that carry a team tag, RESIDUAL_POOL for those that do not. It answers "how was this number arrived at" in a single slice, which is the question a team asks first when it disputes a bill.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `COUNT(*)` |
| **Grain** | day |
| **Format** | integer |
| **Direction** | neutral |
| **Freshness floor** | 8 h |
| **Owner** | finops |
| **Allocation method** | mixed |
| **Dimensions** | `allocation_method`, `database`, `team`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY` |
| **Also known as** | how was this allocated, method mix, tagged vs untagged queries |

Verified questions:
- *allocation method mix*
- *how many queries are tagged*

#### `chargeback.budget_variance_credits` — Billed credits vs budget (monthly)

Billed credits at month grain, carrying the budget thresholds. Snowflake publishes no budget object in ACCOUNT_USAGE, so the budget lives in the thresholds below rather than in a joined table: the metric states the spend and the configured limits state the expectation. Replace the thresholds per deployment — they are a placeholder, not a recommendation.

| | |
|---|---|
| **Entity** | `fact_cost_daily` |
| **Expression** | `SUM(CREDITS_BILLED)` |
| **Grain** | month |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 3 h |
| **Owner** | finops |
| **Allocation method** | metered |
| **Dimensions** | `service_type`, `usage_day` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY` |
| **Thresholds** | warn: 40000.0, critical: 50000.0 |
| **Also known as** | budget, monthly budget, budget variance, are we over budget |

Verified questions:
- *billed credits this month*
- *are we over budget*

#### `chargeback.forecast_input_credits` — Daily billed credits (forecast input)

Billed credits per day — the series every forecast and run-rate projection is fitted to. Kept as a raw daily total on purpose: the forecast belongs in the analytics layer where its method and its error bars can be stated, not inside a metric definition that would present an extrapolation with the same authority as a measurement (R3).

| | |
|---|---|
| **Entity** | `fact_cost_daily` |
| **Expression** | `SUM(CREDITS_BILLED)` |
| **Grain** | day |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 3 h |
| **Owner** | finops |
| **Allocation method** | metered |
| **Dimensions** | `service_type`, `usage_day` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY` |
| **Also known as** | run rate, daily spend series, forecast, burn rate |

Verified questions:
- *daily billed credits*
- *what is our run rate*

#### `chargeback.metered_credits` — Metered credits (reconciliation control total)

The account's metered credits by service type — the control total every allocation must sum back to. It is stated in this domain, rather than borrowed from D1, because the reconciliation gate needs both sides of the comparison to carry the same provenance and the same latency floor.

| | |
|---|---|
| **Entity** | `fact_cost_daily` |
| **Expression** | `SUM(CREDITS_USED)` |
| **Grain** | day |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 3 h |
| **Owner** | finops |
| **Allocation method** | metered |
| **Dimensions** | `service_type`, `usage_day` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY` |
| **Also known as** | control total, metered total, what the account actually used |

Verified questions:
- *metered credits by service type*
- *control total for reconciliation*

#### `chargeback.reconciliation_variance` — Reconciliation variance (metered minus allocated)

Metered warehouse compute minus the compute attributable to individual queries. The gap is expected, not an error: QUERY_ATTRIBUTION_HISTORY excludes idle time, queries under about 100 ms, and Adaptive Warehouse jobs (ASSUMPTIONS §5). What the reconciliation gate checks is that the gap stays explainable — a variance that grows without idle time growing means the attribution view has stopped seeing part of the workload.

| | |
|---|---|
| **Entity** | `fact_warehouse_metering_hourly` |
| **Expression** | `SUM(CREDITS_COMPUTE) - SUM(CREDITS_ATTRIBUTED)` |
| **Grain** | hour |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 8 h |
| **Owner** | finops |
| **Allocation method** | idle_share |
| **Dimensions** | `metering_hour`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY` |
| **Also known as** | reconciliation gap, unallocated compute, allocation residual |

Verified questions:
- *reconciliation variance by warehouse*
- *unallocated compute credits*

#### `chargeback.spend_by_account` — Spend in currency by account

Spend in contract currency per account, from ORGANIZATION_USAGE. This is the figure a finance system reconciles against, and the only one in the platform denominated in money rather than credits. Values restate until month close, so anything inside the 35-day window is flagged provisional and must not be posted as final (R7, §9.3).

| | |
|---|---|
| **Entity** | `fact_spend_currency_daily` |
| **Expression** | `SUM(SPEND_IN_CURRENCY)` |
| **Grain** | day |
| **Format** | currency (USD) |
| **Direction** | lower is better |
| **Freshness floor** | 3 d |
| **Owner** | finops |
| **Allocation method** | metered |
| **Provisional window** | 35 days (restatement) |
| **Dimensions** | `account`, `currency`, `usage_day`, `usage_type` |
| **Required sources** | `SNOWFLAKE.ORGANIZATION_USAGE.USAGE_IN_CURRENCY_DAILY` |
| **Also known as** | spend by account, invoice, money by account, currency spend |

Verified questions:
- *spend by account this month*
- *which account costs the most*

#### `chargeback.unattributed_credits` — Unattributed credits

Attributed credits carrying no team tag. This is the residual pool: it has to be spread over the tagged teams pro-rata, which means every credit here is a credit somebody is being charged for on an estimate rather than on evidence. Sliced by warehouse and user it becomes a tagging backlog.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `SUM(CASE WHEN QUERY_TAG_TEAM IS NULL THEN CREDITS_ATTRIBUTED ELSE 0 END)` |
| **Grain** | day |
| **Format** | number (credits) |
| **Direction** | lower is better |
| **Freshness floor** | 8 h |
| **Owner** | finops |
| **Allocation method** | residual_pool |
| **Dimensions** | `database`, `query_type`, `user`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY` |
| **Also known as** | untagged credits, unallocated spend, residual pool |

Verified questions:
- *unattributed credits by warehouse*
- *who is not tagging their queries*

#### `chargeback.unattributed_share` — Unattributed share of allocation

Fraction of allocated credits that reached a team by estimate rather than by tag. It is the confidence figure that belongs next to every chargeback statement: at 5% the allocation is defensible, at 30% it is a negotiation.

| | |
|---|---|
| **Entity** | `fact_query_execution` |
| **Expression** | `SAFE_RATIO( SUM(CASE WHEN QUERY_TAG_TEAM IS NULL THEN CREDITS_ATTRIBUTED ELSE 0 END), SUM(CREDITS_ATTRIBUTED) )` |
| **Grain** | day |
| **Format** | percent |
| **Direction** | lower is better |
| **Freshness floor** | 8 h |
| **Owner** | finops |
| **Allocation method** | residual_pool |
| **Dimensions** | `database`, `query_type`, `warehouse` |
| **Required sources** | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`, `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY` |
| **Thresholds** | warn: 0.05, critical: 0.15 |
| **Also known as** | allocation confidence, untagged percentage, estimate share |

Verified questions:
- *how much of the allocation is estimated*
- *unattributed share by warehouse*

## Portability shims

Constructs that do not express identically in both engines are translated by a
shim, one construct each, every one covered by a parity test. Business logic is
never forked per engine (R1).

| Shim | Purpose |
|---|---|
| `DATE_DIFF_DAYS` | Whole days between two dates/timestamps. |
| `EPOCH_SECONDS` | Whole seconds between two timestamps. |
| `JSON_GET` | Read a top-level string field from a JSON/VARIANT column. |
| `MONEY` | Cast to the fixed-point credit/currency type — never float. |
| `PERCENTILE` | Percentile of a numeric column (approximate on Snowflake, exact on DuckDB). |
| `REGEX_CONTAINS` | Boolean regular-expression match. |
| `SAFE_RATIO` | Division yielding NULL on a zero or null denominator, fixed-point result. |
| `TS_PARSE` | Parse landed ISO-8601 timestamp text, NULL when unparseable. |
| `TS_TRUNC` | Truncate a timestamp to a unit. |

Documented divergences and their tolerances are in [`PARITY_EXCEPTIONS.md`](PARITY_EXCEPTIONS.md).
