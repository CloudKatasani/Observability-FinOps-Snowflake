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

// -------------------------------------------------------------------- scope
// A figure is only meaningful once you know *whose* it is. Every metric
// response says where it was computed, and — for an organization roll-up —
// which accounts actually contributed, so a roll-up over the accounts landed so
// far is never presented as the whole organization (§9, R3/R5).

export const scopeKindSchema = z.enum(["organization", "account"]);
export type ScopeKind = z.infer<typeof scopeKindSchema>;

export const scopeContextSchema = z.object({
  scope: scopeKindSchema,
  scope_account: z.string().nullish(),
  //: True when an organization figure covers only the accounts landed so far.
  scope_partial: z.boolean(),
  contributing_accounts: z.array(z.string()),
});
export type ScopeContext = z.infer<typeof scopeContextSchema>;

/** One entry in the scope selector, with how much of the catalogue it answers. */
export const scopeOptionSchema = z.object({
  value: z.string(),
  label: z.string(),
  scope: scopeKindSchema,
  answerable_metrics: z.number(),
  total_metrics: z.number(),
});
export type ScopeOption = z.infer<typeof scopeOptionSchema>;

export const scopeOptionsSchema = z.object({
  mode: z.string(),
  organization: z.string().nullish(),
  options: z.array(scopeOptionSchema),
});
export type ScopeOptions = z.infer<typeof scopeOptionsSchema>;

/**
 * What a page collects to drive its freshness and scope banners: provenance
 * always, and the scope fields from those endpoints that report them. The
 * allocation and coverage endpoints do not yet, so the scope half is optional
 * rather than faked.
 */
export type ProvenanceContribution = Provenance & Partial<ScopeContext>;

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

export const metricQueryResponseSchema = provenanceSchema.merge(scopeContextSchema).extend({
  metrics: z.array(z.string()),
  columns: z.array(z.string()),
  rows: z.array(z.array(cellSchema)),
  row_count: z.number(),
  truncated: z.boolean(),
  sql: z.string(),
});
export type MetricQueryResponse = z.infer<typeof metricQueryResponseSchema>;

export const metricTileSchema = provenanceSchema.merge(scopeContextSchema).extend({
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
  //: The global scope filter. `scope: "account"` requires `account`; the API
  //: rejects the pair rather than widening it to the organization.
  scope?: ScopeKind;
  account?: string | null;
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

/** One statement behind the allocation, and what it contributes (R5). */
export const sqlDisclosureSchema = z.object({
  purpose: z.string(),
  metrics: z.array(z.string()),
  dimensions: z.array(z.string()),
  sql: z.string(),
});
export type SqlDisclosure = z.infer<typeof sqlDisclosureSchema>;

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
  //: R5 — an allocation is several metric queries, and all of them are shown.
  sql: z.array(sqlDisclosureSchema),
});
export type Allocation = z.infer<typeof allocationSchema>;

// ------------------------------------------------------------------ coverage

export const sourceStatusSchema = z.enum(["available", "stale", "empty", "missing"]);
export type SourceStatus = z.infer<typeof sourceStatusSchema>;

export const metricAvailabilitySchema = z.enum(["enabled", "degraded", "unavailable"]);

/** How a source is exported: once per account, or once for the whole fleet. */
export const sourceScopeSchema = z.enum(["account", "organization"]);
export type SourceScope = z.infer<typeof sourceScopeSchema>;

/**
 * One account's slice of one source. Present only where ingest recorded which
 * account a batch came from — the account is never inferred from the rows.
 */
export const accountCoverageSchema = z.object({
  account: z.string(),
  status: sourceStatusSchema,
  rows: z.number(),
  batches: z.number(),
  window_start: z.string().nullish(),
  window_end: z.string().nullish(),
  freshness_minutes: z.number().nullish(),
});
export type AccountCoverage = z.infer<typeof accountCoverageSchema>;

export const sourceCoverageSchema = z.object({
  source_id: z.string(),
  snowflake_object: z.string(),
  domain: z.string(),
  criticality: z.string(),
  scope: sourceScopeSchema.default("account"),
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
  //: Per-account status, empty when the lake records no account stamps.
  accounts: z.array(accountCoverageSchema).default([]),
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
  //: Accounts whose extracts are present in this tenant's lake. Empty for a
  //: deployment that never told ingest which account it was uploading.
  accounts: z.array(z.string()).default([]),
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

/** The RFC 7807 document the API returns for every expected failure (§15). */
export const problemDetailSchema = z.object({
  type: z.string().default("about:blank"),
  title: z.string(),
  status: z.number(),
  detail: z.string().nullish(),
  instance: z.string().nullish(),
});
export type ProblemDetail = z.infer<typeof problemDetailSchema>;

/** A metric that cannot be answered at the requested scope, per `services/scope.py`. */
export const SCOPE_UNAVAILABLE_PROBLEM = "https://snowobs.dev/problems/scope-unavailable";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    /** The RFC 7807 `type`, when the API sent a problem document. */
    readonly problemType?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * "This metric cannot answer at the scope you selected" — a well-formed request
 * against healthy data, not a failure. R3 says surface the reason where the
 * figure would have been, so callers render it as an explanation rather than an
 * alarm.
 */
export function isScopeUnavailable(error: unknown): error is ApiError {
  return error instanceof ApiError && error.problemType === SCOPE_UNAVAILABLE_PROBLEM;
}

/**
 * Turn a failed response into an error that still carries the API's reason.
 *
 * Discarding the problem body and reporting only the status would strip exactly
 * the sentence R3 exists to show — "this metric describes the whole
 * organization" reads very differently from "422".
 */
async function failure(method: string, path: string, response: Response): Promise<ApiError> {
  const fallback = `${method} ${path} failed with ${response.status}`;
  try {
    const problem = problemDetailSchema.parse(await response.json());
    return new ApiError(response.status, problem.detail ?? problem.title, problem.type);
  } catch {
    return new ApiError(response.status, fallback);
  }
}

async function getJson(path: string, allowStatuses: number[] = []): Promise<unknown> {
  const response = await fetch(path, { headers: { accept: "application/json" } });
  if (!response.ok && !allowStatuses.includes(response.status)) {
    throw await failure("GET", path, response);
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
    throw await failure("POST", path, response);
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

/** The scopes this deployment can answer at, and how much each can answer. */
export async function fetchScopeOptions(): Promise<ScopeOptions> {
  return scopeOptionsSchema.parse(await getJson("/api/v1/metrics/scopes"));
}

/** Run a governed metric query. Rows come back with the SQL that produced them. */
export async function queryMetrics(request: MetricQueryRequest): Promise<MetricQueryResponse> {
  return metricQueryResponseSchema.parse(await postJson("/api/v1/metrics/query", request));
}

/** One KPI tile. An unavailable metric explains itself instead of returning 0. */
export async function fetchMetricTile(
  metricId: string,
  range: { start: string; end: string },
  scope?: { scope: ScopeKind; account: string | null },
): Promise<MetricTile> {
  const query = new URLSearchParams({ start: range.start, end: range.end });
  if (scope) {
    query.set("scope", scope.scope);
    // Only an account scope names an account; sending an empty one would make
    // the API reject a request the user never made.
    if (scope.scope === "account" && scope.account) query.set("account", scope.account);
  }
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

// ------------------------------------------------------------------- agents
// An agent answer is held to the same standard as a dashboard figure: it
// arrives with the SQL, the metrics, and the sources behind it, and the UI
// shows them (R5). `grounded` says whether any tool result backs the answer at
// all — an ungrounded answer is never presented as a finding.

export const agentInfoSchema = z.object({
  name: z.string(),
  description: z.string(),
  tools: z.array(z.string()),
});
export type AgentInfo = z.infer<typeof agentInfoSchema>;

export const traceStepSchema = z.object({
  kind: z.string(),
  summary: z.string(),
  elapsed_ms: z.number().nullish(),
  detail: z.record(z.string(), z.unknown()).default({}),
});
export type TraceStep = z.infer<typeof traceStepSchema>;

export const agentAnswerSchema = z.object({
  answer: z.string(),
  agent: z.string(),
  grounded: z.boolean(),
  refused: z.boolean(),
  refusal_reason: z.string().nullish(),
  metrics_used: z.array(z.string()),
  sources_used: z.array(z.string()),
  sql: z.array(z.string()),
  trace_id: z.string(),
  steps: z.array(traceStepSchema),
  input_tokens: z.number(),
  output_tokens: z.number(),
});
export type AgentAnswer = z.infer<typeof agentAnswerSchema>;

/** The specialists available and the tools each may reach for (§12.2). */
export async function fetchAgents(): Promise<AgentInfo[]> {
  return z.array(agentInfoSchema).parse(await getJson("/api/v1/agents/catalog"));
}

/** Ask one question. The supervisor routes it unless an agent is named. */
export async function askAgent(question: string, agent?: string): Promise<AgentAnswer> {
  return agentAnswerSchema.parse(
    await postJson("/api/v1/agents/ask", { question, agent: agent ?? null }),
  );
}

/** One event from the streaming endpoint: a trace step, or the final answer. */
export const agentStreamEventSchema = z.object({ event: z.string() }).passthrough();
export type AgentStreamEvent = z.infer<typeof agentStreamEventSchema>;

/**
 * Stream a turn, yielding each event as it arrives.
 *
 * `fetch` is used rather than `EventSource` because the question is a POST
 * body; EventSource is GET-only, and putting a free-text question in a query
 * string would put it in every proxy access log along the way.
 */
export async function* streamAgent(
  question: string,
  agent?: string,
  signal?: AbortSignal,
): AsyncGenerator<AgentStreamEvent> {
  const response = await fetch("/api/v1/agents/stream", {
    method: "POST",
    headers: {
      accept: "text/event-stream",
      "content-type": "application/json",
    },
    body: JSON.stringify({ question, agent: agent ?? null }),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new ApiError(
      response.status,
      `POST /api/v1/agents/stream failed with ${response.status}`,
    );
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffered = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffered += decoder.decode(value, { stream: true });
      // SSE frames are separated by a blank line; a frame split across two
      // chunks must not be parsed until the rest of it arrives.
      const frames = buffered.split("\n\n");
      buffered = frames.pop() ?? "";
      for (const frame of frames) {
        const payload = frame
          .split("\n")
          .filter((line) => line.startsWith("data: "))
          .map((line) => line.slice("data: ".length))
          .join("");
        if (!payload) continue;
        yield agentStreamEventSchema.parse(JSON.parse(payload));
      }
    }
  } finally {
    reader.releaseLock();
  }
}
