# KPI catalogue

**Generated from `packages/semantics/metrics/*.yaml` — do not edit by hand.**
Regenerate with `make catalog`.

Every KPI below is defined once, in YAML, and compiled to both Snowflake and
DuckDB SQL by the same compiler (R1). Each declares the source views it needs,
which is what drives the coverage matrix: a KPI whose sources are missing renders
as *"Unavailable — requires …"* with a remediation, never as a zero (R3).

The **freshness floor** is the documented latency of the slowest source a KPI
reads. No surface may imply a figure is fresher than this (R7).

**41 KPIs across 3 domains.**

## Contents

- [D1 — Cost & spend](#d1-cost-spend) (15)
- [D2 — Warehouse & compute efficiency](#d2-warehouse-compute-efficiency) (12)
- [D3 — Query & workload performance](#d3-query-workload-performance) (14)

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
