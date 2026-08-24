import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ExecutivePage from "@/pages/ExecutivePage";
import {
  EMPTY_METRIC_QUERY_RESPONSE,
  META,
  METRIC_QUERY_RESPONSE,
  SOURCES,
  SPEND_TILE,
  TOTAL_CREDITS_TILE,
  UNAVAILABLE_TILE,
} from "@/test/fixtures";
import { stubFetch } from "@/test/http";
import { renderWithClient } from "@/test/render";

vi.mock("@/charts/Chart", () => ({ default: () => null }));

const BILLED_TILE = {
  ...TOTAL_CREDITS_TILE,
  metric_id: "cost.billed_credits",
  name: "Billed credits",
  value: "15034.765568931",
};

const IDLE_TILE = {
  ...TOTAL_CREDITS_TILE,
  metric_id: "wh.idle_pct",
  name: "Idle credit share",
  value: "0.425582229903333",
  format_type: "percent",
  format_decimals: 1,
  unit: null,
  latency_floor_minutes: 480,
  sources: ["query_attribution_history", "warehouse_metering_history"],
};

const PER_QUERY_TILE = {
  ...TOTAL_CREDITS_TILE,
  metric_id: "cost.per_query",
  name: "Cost per query",
  value: "0.764455599289989",
  format_decimals: 6,
  latency_floor_minutes: 480,
  sources: ["query_attribution_history", "query_history"],
};

function stub(overrides: Record<string, unknown> = {}) {
  stubFetch({
    "/api/v1/meta": { body: META },
    "/api/v1/sources": { body: SOURCES },
    "/api/v1/metrics/cost.total_credits/tile": { body: TOTAL_CREDITS_TILE },
    "/api/v1/metrics/cost.billed_credits/tile": { body: BILLED_TILE },
    "/api/v1/metrics/cost.spend_usd/tile": { body: SPEND_TILE },
    "/api/v1/metrics/cost.unattributed_share/tile": { body: UNAVAILABLE_TILE },
    "/api/v1/metrics/wh.idle_pct/tile": { body: IDLE_TILE },
    "/api/v1/metrics/cost.per_query/tile": { body: PER_QUERY_TILE },
    "/api/v1/metrics/query": { body: METRIC_QUERY_RESPONSE },
    ...overrides,
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ExecutivePage", () => {
  it("formats every figure from its decimal string, in tabular figures", async () => {
    stub();
    renderWithClient(<ExecutivePage />);

    expect(await screen.findByText("15,934.5")).toBeInTheDocument();
    expect(screen.getByText("15,034.8")).toBeInTheDocument();
    expect(screen.getByText("$45,104.31")).toBeInTheDocument();
    expect(screen.getByText("42.6%")).toBeInTheDocument();
    expect(screen.getByText("0.764456")).toBeInTheDocument();
    expect(screen.getByText("15,934.5")).toHaveClass("tabular-nums");
  });

  it("shows an unavailable tile's reason instead of a zero", async () => {
    stub();
    renderWithClient(<ExecutivePage />);

    expect(
      await screen.findByText("Unavailable — requires query_attribution_history"),
    ).toBeInTheDocument();
    // The unattributed-share tile must not fall back to 0.0% or 0%.
    expect(screen.queryByText("0.0%")).not.toBeInTheDocument();
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
  });

  it("badges a figure that is still inside its restatement window", async () => {
    stub();
    renderWithClient(<ExecutivePage />);

    // The spend tile is provisional, and so is the page banner it feeds.
    expect(await screen.findAllByText("Provisional")).not.toHaveLength(0);
    expect(screen.getByText("figures on this page may restate")).toBeInTheDocument();
  });

  it("names the slowest contributing source in the freshness banner", async () => {
    stub();
    renderWithClient(<ExecutivePage />);

    expect(
      await screen.findByText(/data no fresher than 3d \(USAGE_IN_CURRENCY_DAILY\)/),
    ).toBeInTheDocument();
  });

  it("offers the compiled SQL and its sources on every tile", async () => {
    stub();
    renderWithClient(<ExecutivePage />);

    const disclosures = await screen.findAllByText("Show the SQL");
    expect(disclosures.length).toBeGreaterThanOrEqual(5);
    expect(screen.getAllByText("METERING_DAILY_HISTORY").length).toBeGreaterThan(0);
  });

  it("explains an empty chart rather than drawing an empty axis", async () => {
    stub({ "/api/v1/metrics/query": { body: EMPTY_METRIC_QUERY_RESPONSE } });
    renderWithClient(<ExecutivePage />);

    expect(
      await screen.findByText(/Load METERING_DAILY_HISTORY to populate the trend/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Load WAREHOUSE_METERING_HISTORY to rank warehouses by cost/),
    ).toBeInTheDocument();
  });
});
