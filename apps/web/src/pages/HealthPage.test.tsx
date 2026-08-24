import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import HealthPage from "@/pages/HealthPage";
import { COVERAGE, META, SOURCES, TOTAL_CREDITS_TILE } from "@/test/fixtures";
import { stubFetch } from "@/test/http";
import { renderWithClient } from "@/test/render";

vi.mock("@/charts/Chart", () => ({ default: () => null }));

const FAILURE_RATE_TILE = {
  ...TOTAL_CREDITS_TILE,
  metric_id: "q.failure_rate",
  name: "Query failure rate",
  value: "0.023869346733668",
  format_type: "percent",
  format_decimals: 2,
  unit: null,
  latency_floor_minutes: 45,
  sources: ["query_history"],
};

const VOLUME_TILE = {
  ...TOTAL_CREDITS_TILE,
  metric_id: "q.volume",
  name: "Query volume",
  value: 13362,
  format_type: "integer",
  format_decimals: 0,
  unit: null,
  latency_floor_minutes: 45,
  sources: ["query_history"],
};

/** A metric whose source never landed. It must explain itself, not show 0%. */
const QUEUE_TILE = {
  ...TOTAL_CREDITS_TILE,
  metric_id: "wh.queue_overload_pct",
  name: "Queue overload time share",
  value: null,
  format_type: "percent",
  format_decimals: 1,
  unit: null,
  latency_floor_minutes: 45,
  sources: ["warehouse_load_history"],
  sql: "",
  unavailable_reason: "Unavailable — requires warehouse_load_history",
};

const WAREHOUSE_CREDITS = {
  metrics: ["cost.attributed_credits", "cost.idle_credits", "cost.by_warehouse_credits"],
  columns: [
    "TIME_BUCKET",
    "WAREHOUSE",
    "COST_ATTRIBUTED_CREDITS",
    "COST_IDLE_CREDITS",
    "COST_BY_WAREHOUSE_CREDITS",
  ],
  rows: [
    [
      "2026-08-01T00:00:00",
      "WH_DS_TRAINING",
      "2685.254292594",
      "1797.985707406",
      "4483.240000000",
    ],
  ],
  row_count: 1,
  truncated: false,
  as_of: "2026-08-24T14:32:00Z",
  latency_floor_minutes: 480,
  provisional: false,
  sources: ["query_attribution_history", "warehouse_metering_history"],
  sql: 'SELECT SUM(CREDITS_ATTRIBUTED) AS "COST_ATTRIBUTED_CREDITS" FROM base',
};

const EMPTY_QUERY = { ...WAREHOUSE_CREDITS, rows: [], row_count: 0 };

function stub() {
  stubFetch({
    "/api/v1/meta": { body: META },
    "/api/v1/sources": { body: SOURCES },
    "/api/v1/datasets/coverage": { body: COVERAGE },
    "/api/v1/metrics/q.failure_rate/tile": { body: FAILURE_RATE_TILE },
    "/api/v1/metrics/q.volume/tile": { body: VOLUME_TILE },
    "/api/v1/metrics/wh.queue_overload_pct/tile": { body: QUEUE_TILE },
    "/api/v1/metrics/query": (body) => {
      const request = body as { metrics: string[] };
      return request.metrics.includes("cost.attributed_credits")
        ? { body: WAREHOUSE_CREDITS }
        : { body: EMPTY_QUERY };
    },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("HealthPage", () => {
  it("shows the failure rate and query volume as formatted figures", async () => {
    stub();
    renderWithClient(<HealthPage />);

    expect(await screen.findByText("2.39%")).toBeInTheDocument();
    expect(screen.getByText("13,362")).toBeInTheDocument();
  });

  it("explains an unavailable health metric instead of reporting zero", async () => {
    stub();
    renderWithClient(<HealthPage />);

    expect(
      await screen.findByText("Unavailable — requires warehouse_load_history"),
    ).toBeInTheDocument();
    expect(screen.queryByText("0.0%")).not.toBeInTheDocument();
  });

  it("reports freshness per source against its documented latency", async () => {
    stub();
    renderWithClient(<HealthPage />);

    expect(await screen.findByText("METERING_DAILY_HISTORY")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Source freshness" })).toBeInTheDocument();
    expect(screen.getByText("5.7h old")).toBeInTheDocument();
    expect(screen.getByText("Never landed")).toBeInTheDocument();
  });

  it("derives utilisation by dividing exact credit sums, never by averaging ratios", async () => {
    stub();
    renderWithClient(<HealthPage />);

    expect(await screen.findByText("WH_DS_TRAINING")).toBeInTheDocument();
    // 2685.254292594 / 4483.24 = 59.9%, and idle is the complement.
    expect(screen.getByText("59.9%")).toBeInTheDocument();
    expect(screen.getByText("40.1%")).toBeInTheDocument();
    expect(screen.getByText("4,483.2")).toBeInTheDocument();
  });

  it("states the page's freshness floor", async () => {
    stub();
    renderWithClient(<HealthPage />);

    expect(
      await screen.findByText(/data no fresher than 8h \(QUERY_ATTRIBUTION_HISTORY\)/),
    ).toBeInTheDocument();
  });
});
