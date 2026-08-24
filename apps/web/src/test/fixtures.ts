// Payloads captured from a running API against the synthetic fixture account,
// trimmed to what each test needs. Keeping them shaped exactly like the real
// responses is what makes the zod tests meaningful.

export const META = {
  version: "0.1.0",
  mode: "offline",
  tenancy: "single",
  branding: {
    display_name: "Observability & FinOps Platform for Snowflake",
    short_name: "snowobs",
    palette: { navy: "#12446E", primary: "#0070AD", sky: "#12ABDB", coral: "#E94B89" },
  },
};

export const SOURCES = [
  {
    id: "metering_daily_history",
    snowflake_object: "SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY",
    domain: "cost",
    criticality: "core",
    documented_latency_minutes: 180,
    latency_verified: true,
    required_db_role: "SNOWFLAKE.USAGE_VIEWER",
  },
  {
    id: "query_attribution_history",
    snowflake_object: "SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY",
    domain: "cost",
    criticality: "core",
    documented_latency_minutes: 480,
    latency_verified: true,
    required_db_role: "SNOWFLAKE.USAGE_VIEWER",
  },
  {
    id: "query_history",
    snowflake_object: "SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY",
    domain: "query",
    criticality: "core",
    documented_latency_minutes: 45,
    latency_verified: true,
    required_db_role: "SNOWFLAKE.USAGE_VIEWER",
  },
  {
    id: "warehouse_metering_history",
    snowflake_object: "SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY",
    domain: "warehouse",
    criticality: "core",
    documented_latency_minutes: 180,
    latency_verified: true,
    required_db_role: "SNOWFLAKE.USAGE_VIEWER",
  },
  {
    id: "usage_in_currency_daily",
    snowflake_object: "SNOWFLAKE.ORGANIZATION_USAGE.USAGE_IN_CURRENCY_DAILY",
    domain: "cost",
    criticality: "important",
    documented_latency_minutes: 4320,
    latency_verified: false,
    required_db_role: null,
  },
];

export const METRIC_QUERY_RESPONSE = {
  metrics: ["cost.total_credits"],
  columns: ["TIME_BUCKET", "SERVICE_TYPE", "COST_TOTAL_CREDITS"],
  rows: [
    ["2026-08-01T00:00:00", "WAREHOUSE_METERING", "15895.569200000"],
    ["2026-07-01T00:00:00", "WAREHOUSE_METERING", "757.052000000"],
    ["2026-08-01T00:00:00", "AI_SERVICES", "21.308539000"],
  ],
  row_count: 3,
  truncated: false,
  scope: "organization",
  scope_account: null,
  scope_partial: false,
  contributing_accounts: [],
  as_of: "2026-08-24T03:02:27.800588Z",
  latency_floor_minutes: 180,
  provisional: false,
  sources: ["metering_daily_history"],
  sql: 'SELECT SUM(CREDITS_USED) AS "COST_TOTAL_CREDITS" FROM metering_daily_history',
};

export const EMPTY_METRIC_QUERY_RESPONSE = {
  ...METRIC_QUERY_RESPONSE,
  rows: [],
  row_count: 0,
};

export const TOTAL_CREDITS_TILE = {
  metric_id: "cost.total_credits",
  name: "Total credits consumed",
  value: "15934.514768931",
  format_type: "number",
  format_decimals: 1,
  unit: "credits",
  direction: "lower_is_better",
  scope: "organization",
  scope_account: null,
  scope_partial: false,
  contributing_accounts: [],
  as_of: "2026-08-24T14:32:00Z",
  latency_floor_minutes: 180,
  provisional: false,
  sources: ["metering_daily_history"],
  sql: 'SELECT SUM(CREDITS_USED) AS "COST_TOTAL_CREDITS" FROM metering_daily_history',
  allocation_method: null,
  unavailable_reason: null,
};

export const SPEND_TILE = {
  metric_id: "cost.spend_usd",
  name: "Spend in currency",
  value: "45104.310000000",
  format_type: "currency",
  format_decimals: 2,
  unit: "USD",
  direction: "lower_is_better",
  scope: "organization",
  scope_account: null,
  scope_partial: false,
  contributing_accounts: [],
  as_of: "2026-08-24T14:32:00Z",
  latency_floor_minutes: 4320,
  provisional: true,
  sources: ["usage_in_currency_daily"],
  sql: 'SELECT SUM(SPEND_IN_CURRENCY) AS "COST_SPEND_USD" FROM usage_in_currency_daily',
  allocation_method: null,
  unavailable_reason: null,
};

/** A metric whose source never landed — R3's central case. */
export const UNAVAILABLE_TILE = {
  metric_id: "cost.unattributed_share",
  name: "Unattributed spend share",
  value: null,
  format_type: "percent",
  format_decimals: 1,
  unit: null,
  direction: "lower_is_better",
  scope: "organization",
  scope_account: null,
  scope_partial: false,
  contributing_accounts: [],
  as_of: "2026-08-24T14:32:00Z",
  latency_floor_minutes: 480,
  provisional: false,
  sources: ["query_attribution_history", "query_history"],
  sql: "",
  allocation_method: null,
  unavailable_reason: "Unavailable — requires query_attribution_history",
};

export const PASSING_RECONCILIATION = {
  outcome: "passed",
  allocated_credits: "14997.081714307",
  metered_credits: "14995.820000000",
  variance_credits: "1.261714307",
  variance_pct: "0.008413773351507286697226293727",
  tolerance_pct: "0.5",
  publication_allowed: true,
  banner: "Reconciled: allocated 14997.08 credits vs metered 14995.82 (+0.008%), within ±0.5%.",
  ran_at: "2026-08-24T03:02:46.150979Z",
  worst_days: [
    {
      usage_day: "2026-08-02",
      allocated_credits: "235.861714307",
      metered_credits: "234.600000000",
      variance_credits: "1.261714307",
      variance_pct: "0.5378151351236146632566069906",
    },
  ],
};

export const FAILING_RECONCILIATION = {
  ...PASSING_RECONCILIATION,
  outcome: "failed",
  allocated_credits: "13120.400000000",
  variance_credits: "-1875.420000000",
  variance_pct: "-12.506103459",
  publication_allowed: false,
  banner:
    "Reconciliation FAILED: allocated 13120.40 credits vs metered 14995.82 (-12.506%), outside ±0.5%.",
  worst_days: [
    {
      usage_day: "2026-08-18",
      allocated_credits: "684.580000000",
      metered_credits: "2560.000000000",
      variance_credits: "-1875.420000000",
      variance_pct: "-73.258593750",
    },
  ],
};

export const TEAM_ROWS = [
  {
    team: "TEAM_ML",
    direct_credits: "3212.726276630",
    idle_credits: "1270.513723370",
    cloud_services_credits: "0",
    total_credits: "4483.240000000",
    cost_usd: "13449.72",
    share_of_total: "0.2989",
  },
  {
    team: "TEAM_ANALYTICS",
    direct_credits: "1812.759306981",
    idle_credits: "486.560693019",
    cloud_services_credits: "0",
    total_credits: "2299.320000000",
    cost_usd: "6897.96",
    share_of_total: "0.1533",
  },
];

export const PUBLISHED_ALLOCATION = {
  period_start: "2026-08-01",
  period_end: "2026-08-24",
  mode: "showback",
  teams: TEAM_ROWS,
  unattributed_share: "0.2037",
  credit_price_usd: "3.00",
  reconciliation: PASSING_RECONCILIATION,
  figures_published: true,
  // The allocation reports its scope like every other figure: this one covers
  // two accounts, and names them, so a reader can tell an organization-wide
  // chargeback from one account's.
  scope: "organization",
  scope_account: null,
  scope_partial: false,
  contributing_accounts: ["ACME_ANALYTICS", "ACME_PROD"],
  missing_accounts: [],
  as_of: "2026-08-24T14:32:00Z",
  // §15: an allocation is a composite of three metric queries, and it reports
  // the least favourable provenance of its parts rather than the best.
  provisional: false,
  latency_floor_minutes: 480,
  sources: ["warehouse_metering_history", "query_attribution_history", "metering_daily_history"],
  sql: [
    {
      purpose: "Metered credits per warehouse-day — the pool each warehouse's cost is allocated from.",
      metrics: ["cost.by_warehouse_credits"],
      dimensions: ["warehouse"],
      sql: 'SELECT SUM("CREDITS_USED") AS "COST_BY_WAREHOUSE_CREDITS" FROM warehouse_metering_history',
    },
    {
      purpose: "Attributed credits per team — the direct component of the waterfall.",
      metrics: ["cost.by_team_credits"],
      dimensions: ["team", "warehouse"],
      sql: 'SELECT SUM("CREDITS_ATTRIBUTED") AS "COST_BY_TEAM_CREDITS" FROM query_attribution_history',
    },
  ],
};

/**
 * The blocked case. Note that `teams` is deliberately populated: R6 says the
 * gate's verdict decides what is rendered, not the emptiness of the payload.
 */
export const BLOCKED_ALLOCATION = {
  ...PUBLISHED_ALLOCATION,
  teams: TEAM_ROWS,
  reconciliation: FAILING_RECONCILIATION,
  figures_published: false,
};

export const COVERAGE = {
  as_of: "2026-08-24T03:01:46.455203Z",
  mode: "offline",
  sources: [
    {
      source_id: "metering_daily_history",
      snowflake_object: "SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY",
      domain: "cost",
      criticality: "core",
      status: "available",
      rows: 480,
      batches: 1,
      window_start: "2026-07-01",
      window_end: "2026-08-20",
      freshness_minutes: 340.5,
      documented_latency_minutes: 180,
      latency_verified: true,
      remediation: null,
      enables_metric_count: 7,
    },
    {
      source_id: "warehouse_load_history",
      snowflake_object: "SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_LOAD_HISTORY",
      domain: "warehouse",
      criticality: "core",
      status: "missing",
      rows: 0,
      batches: 0,
      window_start: null,
      window_end: null,
      freshness_minutes: null,
      documented_latency_minutes: 180,
      latency_verified: true,
      remediation:
        "Upload an extract of SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_LOAD_HISTORY (expected file name 'warehouse_load_history.csv' or 'warehouse_load_history.parquet').",
      enables_metric_count: 4,
    },
  ],
  metrics: [
    {
      metric_id: "wh.queue_overload_pct",
      availability: "unavailable",
      required_sources: ["warehouse_load_history"],
      missing_sources: ["warehouse_load_history"],
      explanation: "Unavailable — requires warehouse_load_history",
    },
  ],
};

// ─────────────────────────────────────────────────────────── scope selection
// A four-account organization, as `/api/v1/metrics/scopes` reports it. The
// per-scope counts are the point: ACME_SANDBOX has had only billing uploaded,
// so most of the catalogue has nothing to show there.

export const SCOPE_OPTIONS = {
  mode: "offline",
  organization: "ACME_GROUP",
  options: [
    {
      value: "organization",
      label: "Organization",
      scope: "organization",
      answerable_metrics: 92,
      total_metrics: 92,
    },
    {
      value: "ACME_PROD",
      label: "ACME_PROD",
      scope: "account",
      answerable_metrics: 84,
      total_metrics: 92,
    },
    {
      value: "ACME_ANALYTICS",
      label: "ACME_ANALYTICS",
      scope: "account",
      answerable_metrics: 84,
      total_metrics: 92,
    },
    {
      value: "ACME_SANDBOX",
      label: "ACME_SANDBOX",
      scope: "account",
      answerable_metrics: 11,
      total_metrics: 92,
    },
  ],
};

/** A deployment with one account: the picker has nothing to choose between. */
export const SINGLE_SCOPE_OPTIONS = {
  mode: "offline",
  organization: null,
  options: [SCOPE_OPTIONS.options[0]],
};

/**
 * An organization roll-up computed over the accounts landed so far. `q.volume`
 * is additive, so the organization figure is the sum of the four accounts —
 * and `scope_partial` says the denominator is only what has been uploaded.
 */
export const PARTIAL_ORGANIZATION_TILE = {
  ...TOTAL_CREDITS_TILE,
  metric_id: "q.volume",
  name: "Query volume",
  value: 13362,
  format_type: "integer",
  format_decimals: 0,
  unit: null,
  scope_partial: true,
  contributing_accounts: ["ACME_ANALYTICS", "ACME_APAC", "ACME_PROD"],
};

/** The same metric asked of one account: no roll-up, so nothing is partial. */
export const ACCOUNT_SCOPED_TILE = {
  ...TOTAL_CREDITS_TILE,
  value: "4021.100000000",
  scope: "account",
  scope_account: "ACME_PROD",
  scope_partial: false,
  contributing_accounts: ["ACME_PROD"],
};

/** A metric with no per-account meaning, asked of an account (§9). */
export const SCOPE_UNAVAILABLE_TILE = {
  ...TOTAL_CREDITS_TILE,
  metric_id: "cost.spend_usd",
  name: "Spend in currency",
  value: null,
  format_type: "currency",
  format_decimals: 2,
  unit: "USD",
  scope: "account",
  scope_account: "ACME_SANDBOX",
  scope_partial: false,
  contributing_accounts: ["ACME_SANDBOX"],
  sql: "",
  unavailable_reason:
    "Spend in currency describes the whole organization — it comes from " +
    "usage_in_currency_daily, which has no per-account breakdown. Switch to organization " +
    "scope to see it.",
};

/** The RFC 7807 body the API returns when a query cannot answer at a scope. */
export const SCOPE_UNAVAILABLE_PROBLEM = {
  type: "https://snowobs.dev/problems/scope-unavailable",
  title: "Metric unavailable at this scope",
  status: 422,
  detail:
    "Query volume reads ACCOUNT_USAGE, which returns one account per connection. " +
    "Select an account, or use an ORGANIZATION_USAGE metric.",
  instance: "/api/v1/metrics/query",
};

// ────────────────────────────────────────────────────── multi-account coverage
// Two accounts, unequally landed: ACME_PROD has query history, ACME_SANDBOX has
// only billing. `usage_in_currency_daily` is organization-scoped — exported
// once for the fleet, so it carries no per-account breakdown at all.

export const ORGANIZATION_COVERAGE = {
  as_of: "2026-08-24T03:01:46.455203Z",
  mode: "offline",
  accounts: ["ACME_PROD", "ACME_SANDBOX"],
  sources: [
    {
      source_id: "metering_daily_history",
      snowflake_object: "SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY",
      domain: "cost",
      criticality: "core",
      scope: "account",
      status: "available",
      rows: 480,
      batches: 2,
      window_start: "2026-07-01",
      window_end: "2026-08-20",
      freshness_minutes: 340.5,
      documented_latency_minutes: 180,
      latency_verified: true,
      remediation: null,
      enables_metric_count: 7,
      accounts: [
        {
          account: "ACME_PROD",
          status: "available",
          rows: 400,
          batches: 1,
          window_start: "2026-07-01",
          window_end: "2026-08-20",
          freshness_minutes: 340.5,
        },
        {
          account: "ACME_SANDBOX",
          status: "available",
          rows: 80,
          batches: 1,
          window_start: "2026-07-01",
          window_end: "2026-08-20",
          freshness_minutes: 340.5,
        },
      ],
    },
    {
      source_id: "query_history",
      snowflake_object: "SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY",
      domain: "query",
      criticality: "core",
      scope: "account",
      status: "available",
      rows: 13362,
      batches: 1,
      window_start: "2026-07-01",
      window_end: "2026-08-20",
      freshness_minutes: 60,
      documented_latency_minutes: 45,
      latency_verified: true,
      remediation: null,
      enables_metric_count: 9,
      accounts: [
        {
          account: "ACME_PROD",
          status: "available",
          rows: 13362,
          batches: 1,
          window_start: "2026-07-01",
          window_end: "2026-08-20",
          freshness_minutes: 60,
        },
        {
          account: "ACME_SANDBOX",
          status: "missing",
          rows: 0,
          batches: 0,
          window_start: null,
          window_end: null,
          freshness_minutes: null,
        },
      ],
    },
    {
      source_id: "usage_in_currency_daily",
      snowflake_object: "SNOWFLAKE.ORGANIZATION_USAGE.USAGE_IN_CURRENCY_DAILY",
      domain: "cost",
      criticality: "important",
      scope: "organization",
      status: "available",
      rows: 51,
      batches: 1,
      window_start: "2026-07-01",
      window_end: "2026-08-20",
      freshness_minutes: 4000,
      documented_latency_minutes: 4320,
      latency_verified: false,
      remediation: null,
      enables_metric_count: 3,
      accounts: [],
    },
  ],
  metrics: [],
};
