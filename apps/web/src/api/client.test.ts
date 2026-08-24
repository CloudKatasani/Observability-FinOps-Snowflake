import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  allocationSchema,
  coverageMatrixSchema,
  fetchAllocation,
  fetchCoverage,
  fetchLiveness,
  fetchMetricTile,
  fetchReadiness,
  fetchSources,
  metricQueryResponseSchema,
  metricTileSchema,
  queryMetrics,
  readinessSchema,
} from "@/api/client";
import {
  BLOCKED_ALLOCATION,
  COVERAGE,
  METRIC_QUERY_RESPONSE,
  PUBLISHED_ALLOCATION,
  SOURCES,
  TOTAL_CREDITS_TILE,
  UNAVAILABLE_TILE,
} from "@/test/fixtures";
import { stubFetch } from "@/test/http";

/** A payload with one field removed, to prove the schema insists on it. */
function without<T extends object>(payload: T, field: keyof T): Partial<T> {
  const copy = { ...payload };
  delete copy[field];
  return copy;
}

function mockFetch(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchLiveness", () => {
  it("parses a healthy response", async () => {
    mockFetch(200, { status: "ok", version: "0.1.0" });
    await expect(fetchLiveness()).resolves.toEqual({ status: "ok", version: "0.1.0" });
  });

  it("rejects a malformed payload rather than passing it through", async () => {
    mockFetch(200, { status: "fine" });
    await expect(fetchLiveness()).rejects.toThrow();
  });

  it("raises ApiError on transport-level failure statuses", async () => {
    mockFetch(500, {});
    await expect(fetchLiveness()).rejects.toBeInstanceOf(ApiError);
  });
});

describe("fetchReadiness", () => {
  it("treats 503 as a valid not_ready answer, not an error", async () => {
    const body = {
      status: "not_ready",
      version: "0.1.0",
      components: [
        { name: "postgres", status: "unavailable", detail: "ConnectionRefusedError" },
        { name: "redis", status: "ok" },
      ],
    };
    mockFetch(503, body);
    const result = await fetchReadiness();
    expect(result.status).toBe("not_ready");
    expect(result.components).toHaveLength(2);
  });
});

describe("readinessSchema", () => {
  it("rejects unknown component statuses", () => {
    const parsed = readinessSchema.safeParse({
      status: "ready",
      version: "0.1.0",
      components: [{ name: "postgres", status: "degraded" }],
    });
    expect(parsed.success).toBe(false);
  });
});

describe("metric query parsing", () => {
  it("keeps Decimal cells as strings so no precision is lost", async () => {
    stubFetch({ "/api/v1/metrics/query": { body: METRIC_QUERY_RESPONSE } });

    const result = await queryMetrics({
      metrics: ["cost.total_credits"],
      dimensions: ["service_type"],
      start: "2026-07-01",
      end: "2026-08-24",
      grain: "month",
    });

    expect(result.columns).toEqual(["TIME_BUCKET", "SERVICE_TYPE", "COST_TOTAL_CREDITS"]);
    expect(result.rows[0][2]).toBe("15895.569200000");
    expect(typeof result.rows[0][2]).toBe("string");
    expect(result.sources).toEqual(["metering_daily_history"]);
    expect(result.sql).toContain("COST_TOTAL_CREDITS");
  });

  it("rejects a response that drops its provenance", () => {
    expect(metricQueryResponseSchema.safeParse(without(METRIC_QUERY_RESPONSE, "sources")).success).toBe(
      false,
    );
  });

  it("rejects a response whose rows are not arrays of cells", () => {
    const parsed = metricQueryResponseSchema.safeParse({
      ...METRIC_QUERY_RESPONSE,
      rows: [{ TIME_BUCKET: "2026-08-01" }],
    });
    expect(parsed.success).toBe(false);
  });
});

describe("metric tile parsing", () => {
  it("parses a populated tile with its unit and format", async () => {
    stubFetch({ "/api/v1/metrics/cost.total_credits/tile": { body: TOTAL_CREDITS_TILE } });

    const tile = await fetchMetricTile("cost.total_credits", {
      start: "2026-08-01",
      end: "2026-08-24",
    });

    expect(tile.value).toBe("15934.514768931");
    expect(tile.format_type).toBe("number");
    expect(tile.unit).toBe("credits");
    expect(tile.unavailable_reason).toBeNull();
  });

  it("parses an unavailable tile, carrying the reason rather than a value", async () => {
    stubFetch({ "/api/v1/metrics/cost.unattributed_share/tile": { body: UNAVAILABLE_TILE } });

    const tile = await fetchMetricTile("cost.unattributed_share", {
      start: "2026-08-01",
      end: "2026-08-24",
    });

    expect(tile.value).toBeNull();
    expect(tile.unavailable_reason).toBe("Unavailable — requires query_attribution_history");
  });

  it("rejects an unknown format type rather than guessing how to render it", () => {
    const parsed = metricTileSchema.safeParse({ ...TOTAL_CREDITS_TILE, format_type: "money" });
    expect(parsed.success).toBe(false);
  });
});

describe("chargeback allocation parsing", () => {
  it("parses a published allocation with its reconciliation verdict", async () => {
    stubFetch({ "/api/v1/chargeback/allocation": { body: PUBLISHED_ALLOCATION } });

    const allocation = await fetchAllocation({ start: "2026-08-01", end: "2026-08-24" });

    expect(allocation.figures_published).toBe(true);
    expect(allocation.reconciliation.outcome).toBe("passed");
    expect(allocation.teams).toHaveLength(2);
    expect(allocation.teams[0].total_credits).toBe("4483.240000000");
  });

  it("parses a blocked allocation without discarding the gate's evidence", async () => {
    stubFetch({ "/api/v1/chargeback/allocation": { body: BLOCKED_ALLOCATION } });

    const allocation = await fetchAllocation({ start: "2026-08-01", end: "2026-08-24" });

    expect(allocation.figures_published).toBe(false);
    expect(allocation.reconciliation.publication_allowed).toBe(false);
    expect(allocation.reconciliation.worst_days[0].usage_day).toBe("2026-08-18");
  });

  it("keeps the provenance the allocation endpoint emits", () => {
    // §15 and R5: the endpoint once omitted both of these, leaving the page
    // unable to say whether a figure was settled or where it came from.
    const parsed = allocationSchema.parse(PUBLISHED_ALLOCATION);
    expect(parsed.provisional).toBe(false);
    expect(parsed.sql.length).toBeGreaterThan(0);
    for (const disclosure of parsed.sql) {
      expect(disclosure.purpose).not.toBe("");
      expect(disclosure.metrics.length).toBeGreaterThan(0);
      expect(disclosure.sql.toUpperCase()).toContain("SELECT");
    }
  });

  it("rejects an allocation with no SQL behind its figures — R5 has no default", () => {
    expect(allocationSchema.safeParse(without(PUBLISHED_ALLOCATION, "sql")).success).toBe(false);
  });

  it("rejects an allocation missing the gate verdict — R6 has no default", () => {
    expect(allocationSchema.safeParse(without(PUBLISHED_ALLOCATION, "figures_published")).success).toBe(
      false,
    );
  });
});

describe("coverage parsing", () => {
  it("parses per-source status, window, and remediation", async () => {
    stubFetch({ "/api/v1/datasets/coverage": { body: COVERAGE } });

    const coverage = await fetchCoverage();

    expect(coverage.sources).toHaveLength(2);
    expect(coverage.sources[0].status).toBe("available");
    expect(coverage.sources[1].status).toBe("missing");
    expect(coverage.sources[1].remediation).toContain("Upload an extract");
    expect(coverage.metrics[0].explanation).toBe("Unavailable — requires warehouse_load_history");
  });

  it("tolerates a matrix that carries no metric assessments", () => {
    const parsed = coverageMatrixSchema.parse(without(COVERAGE, "metrics"));
    expect(parsed.metrics).toEqual([]);
  });

  it("rejects an unknown source status", () => {
    const parsed = coverageMatrixSchema.safeParse({
      ...COVERAGE,
      sources: [{ ...COVERAGE.sources[0], status: "partial" }],
    });
    expect(parsed.success).toBe(false);
  });
});

describe("source registry parsing", () => {
  it("parses documented latencies, which the UI must never hardcode", async () => {
    stubFetch({ "/api/v1/sources": { body: SOURCES } });

    const sources = await fetchSources();
    const attribution = sources.find((source) => source.id === "query_attribution_history");

    expect(attribution?.documented_latency_minutes).toBe(480);
    expect(attribution?.snowflake_object).toBe(
      "SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY",
    );
  });

  it("raises ApiError when the registry endpoint fails", async () => {
    stubFetch({ "/api/v1/sources": { status: 500, body: {} } });
    await expect(fetchSources()).rejects.toBeInstanceOf(ApiError);
  });
});
