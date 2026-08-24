import { z } from "zod";

// Every API payload is validated at the boundary with zod (BUILD_PROMPT §6);
// downstream code only ever sees parsed, typed values.

export const livenessSchema = z.object({
  status: z.literal("ok"),
  version: z.string(),
});
export type Liveness = z.infer<typeof livenessSchema>;

export const componentStatusSchema = z.object({
  name: z.string(),
  status: z.enum(["ok", "unavailable"]),
  detail: z.string().nullish(),
});

export const readinessSchema = z.object({
  status: z.enum(["ready", "not_ready"]),
  version: z.string(),
  components: z.array(componentStatusSchema),
});
export type Readiness = z.infer<typeof readinessSchema>;

export const brandingSchema = z.object({
  display_name: z.string(),
  short_name: z.string(),
  palette: z.object({
    navy: z.string(),
    primary: z.string(),
    sky: z.string(),
    coral: z.string(),
  }),
});

export const metaSchema = z.object({
  version: z.string(),
  mode: z.string(),
  tenancy: z.string(),
  branding: brandingSchema,
});
export type Meta = z.infer<typeof metaSchema>;

// ---------------------------------------------------------------- provenance
// Every figure-bearing response carries the four fields R5/R7 require: when it
// was computed, how stale its slowest source can be, whether it may restate,
// and which source views produced it.

export const provenanceSchema = z.object({
  as_of: z.string(),
  latency_floor_minutes: z.number(),
  provisional: z.boolean(),
  sources: z.array(z.string()),
});
export type Provenance = z.infer<typeof provenanceSchema>;

/** A single cell of a metric result set: Decimals arrive as strings. */
export const cellSchema = z.union([z.string(), z.number(), z.boolean(), z.null()]);
export type Cell = z.infer<typeof cellSchema>;

export const figureFormatSchema = z.enum([
  "number",
  "currency",
  "percent",
  "duration_ms",
  "bytes",
  "integer",
]);

export const directionSchema = z.enum(["lower_is_better", "higher_is_better", "neutral"]);

// -------------------------------------------------------------------- metrics

export const metricQueryResponseSchema = provenanceSchema.extend({
  metrics: z.array(z.string()),
  columns: z.array(z.string()),
  rows: z.array(z.array(cellSchema)),
  row_count: z.number(),
  truncated: z.boolean(),
  sql: z.string(),
});
export type MetricQueryResponse = z.infer<typeof metricQueryResponseSchema>;

export const metricTileSchema = provenanceSchema.extend({
  metric_id: z.string(),
  name: z.string(),
  value: cellSchema,
  format_type: figureFormatSchema,
  format_decimals: z.number(),
  unit: z.string().nullish(),
  direction: directionSchema,
  sql: z.string(),
  allocation_method: z.string().nullish(),
  //: Set when the metric cannot be computed — R3: say why, never show zero.
  unavailable_reason: z.string().nullish(),
});
export type MetricTile = z.infer<typeof metricTileSchema>;

export const filterOperatorSchema = z.enum([
  "eq",
  "neq",
  "in",
  "not_in",
  "gt",
  "gte",
  "lt",
  "lte",
  "contains",
  "is_null",
  "is_not_null",
]);

export const timeGrainSchema = z.enum(["hour", "day", "week", "month"]);
export type TimeGrain = z.infer<typeof timeGrainSchema>;

export interface MetricFilter {
  dimension: string;
  operator?: z.infer<typeof filterOperatorSchema>;
  value?: string | number | boolean | string[] | null;
}

export interface MetricQueryRequest {
  metrics: string[];
  dimensions?: string[];
  filters?: MetricFilter[];
  start?: string;
  end?: string;
  grain?: TimeGrain;
  limit?: number;
  order?: { field: string; descending?: boolean }[];
}

// ---------------------------------------------------------------- chargeback

export const teamCostSchema = z.object({
  team: z.string(),
  direct_credits: z.string(),
  idle_credits: z.string(),
  cloud_services_credits: z.string(),
  total_credits: z.string(),
  cost_usd: z.string().nullable(),
  share_of_total: z.string(),
});
export type TeamCost = z.infer<typeof teamCostSchema>;

/** `dict[str, str | None]` on the API side, so every value may be null. */
export const reconciliationDaySchema = z.object({
  usage_day: z.string().nullable(),
  allocated_credits: z.string().nullable(),
  metered_credits: z.string().nullable(),
  variance_credits: z.string().nullable(),
  variance_pct: z.string().nullable(),
});

export const reconciliationSchema = z.object({
  outcome: z.string(),
  allocated_credits: z.string(),
  metered_credits: z.string(),
  variance_credits: z.string(),
  variance_pct: z.string().nullable(),
  tolerance_pct: z.string(),
  publication_allowed: z.boolean(),
  banner: z.string(),
  ran_at: z.string(),
  worst_days: z.array(reconciliationDaySchema),
});
export type Reconciliation = z.infer<typeof reconciliationSchema>;

export const allocationSchema = provenanceSchema.extend({
  period_start: z.string(),
  period_end: z.string(),
  mode: z.string(),
  teams: z.array(teamCostSchema),
  unattributed_share: z.string(),
  credit_price_usd: z.string().nullable(),
  reconciliation: reconciliationSchema,
  //: R6 — the gate's verdict. False means the figures are withheld.
  figures_published: z.boolean(),
});
export type Allocation = z.infer<typeof allocationSchema>;

// ------------------------------------------------------------------ coverage

export const sourceStatusSchema = z.enum(["available", "stale", "empty", "missing"]);
export type SourceStatus = z.infer<typeof sourceStatusSchema>;

export const metricAvailabilitySchema = z.enum(["enabled", "degraded", "unavailable"]);

export const sourceCoverageSchema = z.object({
  source_id: z.string(),
  snowflake_object: z.string(),
  domain: z.string(),
  criticality: z.string(),
  status: sourceStatusSchema,
  rows: z.number(),
  batches: z.number(),
  window_start: z.string().nullish(),
  window_end: z.string().nullish(),
  freshness_minutes: z.number().nullish(),
  documented_latency_minutes: z.number(),
  latency_verified: z.boolean(),
  remediation: z.string().nullish(),
  enables_metric_count: z.number(),
});
export type SourceCoverage = z.infer<typeof sourceCoverageSchema>;

export const metricCoverageSchema = z.object({
  metric_id: z.string(),
  availability: metricAvailabilitySchema,
  required_sources: z.array(z.string()),
  missing_sources: z.array(z.string()),
  explanation: z.string(),
});
export type MetricCoverage = z.infer<typeof metricCoverageSchema>;

export const coverageMatrixSchema = z.object({
  as_of: z.string(),
  mode: z.string(),
  sources: z.array(sourceCoverageSchema),
  metrics: z.array(metricCoverageSchema).default([]),
});
export type CoverageMatrix = z.infer<typeof coverageMatrixSchema>;

export const sourceSummarySchema = z.object({
  id: z.string(),
  snowflake_object: z.string(),
  domain: z.string(),
  criticality: z.string(),
  documented_latency_minutes: z.number(),
  latency_verified: z.boolean(),
  required_db_role: z.string().nullish(),
});
export type SourceSummary = z.infer<typeof sourceSummarySchema>;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function getJson(path: string, allowStatuses: number[] = []): Promise<unknown> {
  const response = await fetch(path, { headers: { accept: "application/json" } });
  if (!response.ok && !allowStatuses.includes(response.status)) {
    throw new ApiError(response.status, `GET ${path} failed with ${response.status}`);
  }
  return response.json();
}

async function postJson(path: string, body: unknown): Promise<unknown> {
  const response = await fetch(path, {
    method: "POST",
    headers: { accept: "application/json", "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new ApiError(response.status, `POST ${path} failed with ${response.status}`);
  }
  return response.json();
}

export async function fetchLiveness(): Promise<Liveness> {
  return livenessSchema.parse(await getJson("/healthz"));
}

// 503 is a *valid* readiness answer (not_ready with component detail), not an error.
export async function fetchReadiness(): Promise<Readiness> {
  return readinessSchema.parse(await getJson("/readyz", [503]));
}

export async function fetchMeta(): Promise<Meta> {
  return metaSchema.parse(await getJson("/api/v1/meta"));
}

/** Run a governed metric query. Rows come back with the SQL that produced them. */
export async function queryMetrics(request: MetricQueryRequest): Promise<MetricQueryResponse> {
  return metricQueryResponseSchema.parse(await postJson("/api/v1/metrics/query", request));
}

/** One KPI tile. An unavailable metric explains itself instead of returning 0. */
export async function fetchMetricTile(
  metricId: string,
  range: { start: string; end: string },
): Promise<MetricTile> {
  const query = new URLSearchParams({ start: range.start, end: range.end });
  return metricTileSchema.parse(
    await getJson(`/api/v1/metrics/${encodeURIComponent(metricId)}/tile?${query}`),
  );
}

/** Allocated cost by team, behind the reconciliation gate (R6). */
export async function fetchAllocation(range: {
  start: string;
  end: string;
}): Promise<Allocation> {
  const query = new URLSearchParams({ start: range.start, end: range.end });
  return allocationSchema.parse(await getJson(`/api/v1/chargeback/allocation?${query}`));
}

/** The R3 coverage matrix: what landed, how fresh, and what to do about gaps. */
export async function fetchCoverage(): Promise<CoverageMatrix> {
  return coverageMatrixSchema.parse(await getJson("/api/v1/datasets/coverage"));
}

/** The source registry — documented latencies live here, never in the UI (R7). */
export async function fetchSources(): Promise<SourceSummary[]> {
  return z.array(sourceSummarySchema).parse(await getJson("/api/v1/sources"));
}
