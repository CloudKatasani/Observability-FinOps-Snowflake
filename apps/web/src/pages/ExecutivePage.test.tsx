import { act, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ExecutivePage from "@/pages/ExecutivePage";
import {
  ACCOUNT_SCOPED_TILE,
  EMPTY_METRIC_QUERY_RESPONSE,
  META,
  METRIC_QUERY_RESPONSE,
  PARTIAL_ORGANIZATION_TILE,
  SCOPE_UNAVAILABLE_PROBLEM,
  SCOPE_UNAVAILABLE_TILE,
  SOURCES,
  SPEND_TILE,
  TOTAL_CREDITS_TILE,
  UNAVAILABLE_TILE,
} from "@/test/fixtures";
import { stubFetch } from "@/test/http";
import { renderWithClient } from "@/test/render";
import { ORGANIZATION_SCOPE, useScopeStore } from "@/store/scope";

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

beforeEach(() => {
  useScopeStore.setState(ORGANIZATION_SCOPE);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Every URL and POST body the page asked for, in order. */
function requests(): { url: string; body: unknown }[] {
  return vi.mocked(fetch).mock.calls.map(([input, init]) => ({
    url: String(input),
    body: init?.body ? JSON.parse(String(init.body)) : undefined,
  }));
}

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

describe("ExecutivePage — the scope filter", () => {
  it("asks the API at the organization by default", async () => {
    stub();
    renderWithClient(<ExecutivePage />);

    await screen.findByText("15,934.5");
    const tile = requests().find((request) => request.url.includes("cost.total_credits/tile"));
    expect(tile?.url).toContain("scope=organization");
    expect(tile?.url).not.toContain("account=");
    expect(
      requests().find((request) => request.url.endsWith("/api/v1/metrics/query"))?.body,
    ).toMatchObject({ scope: "organization", account: null });
  });

  it("carries the selected account into every tile and every query", async () => {
    useScopeStore.setState({ scope: "account", account: "ACME_PROD" });
    stub();
    renderWithClient(<ExecutivePage />);

    await screen.findByText("15,934.5");
    for (const request of requests().filter((entry) => entry.url.includes("/tile"))) {
      expect(request.url).toContain("scope=account");
      expect(request.url).toContain("account=ACME_PROD");
    }
    expect(
      requests().find((request) => request.url.endsWith("/api/v1/metrics/query"))?.body,
    ).toMatchObject({ scope: "account", account: "ACME_PROD" });
  });

  it("refetches on a scope change rather than relabelling the figure it has", async () => {
    // The scope is part of the query key precisely so the previous scope's
    // number cannot sit on screen under the new scope's name.
    stub();
    renderWithClient(<ExecutivePage />);
    expect(await screen.findByText("15,934.5")).toBeInTheDocument();

    stubFetch({
      "/api/v1/meta": { body: META },
      "/api/v1/sources": { body: SOURCES },
      "/api/v1/metrics/cost.total_credits/tile": { body: ACCOUNT_SCOPED_TILE },
      "/api/v1/metrics/cost.billed_credits/tile": { body: BILLED_TILE },
      "/api/v1/metrics/cost.spend_usd/tile": { body: SPEND_TILE },
      "/api/v1/metrics/cost.unattributed_share/tile": { body: UNAVAILABLE_TILE },
      "/api/v1/metrics/wh.idle_pct/tile": { body: IDLE_TILE },
      "/api/v1/metrics/cost.per_query/tile": { body: PER_QUERY_TILE },
      "/api/v1/metrics/query": { body: METRIC_QUERY_RESPONSE },
    });
    act(() => useScopeStore.getState().select({ scope: "account", account: "ACME_PROD" }));

    expect(await screen.findByText("4,021.1")).toBeInTheDocument();
    expect(screen.queryByText("15,934.5")).not.toBeInTheDocument();
  });

  it("names the scope on every figure-bearing surface", async () => {
    useScopeStore.setState({ scope: "account", account: "ACME_PROD" });
    stub({ "/api/v1/metrics/cost.total_credits/tile": { body: ACCOUNT_SCOPED_TILE } });
    renderWithClient(<ExecutivePage />);

    await screen.findByText("4,021.1");
    expect(screen.getAllByText("ACME_PROD").length).toBeGreaterThan(0);
  });

  it("warns that an organization roll-up covers only the accounts landed so far", async () => {
    stub({ "/api/v1/metrics/cost.total_credits/tile": { body: PARTIAL_ORGANIZATION_TILE } });
    renderWithClient(<ExecutivePage />);

    // Once in the page banner, and again in the figure's own provenance strip.
    expect(await screen.findAllByText(/Partial organization roll-up/)).not.toHaveLength(0);
    // The contributing accounts are named, so the gap is a known one.
    expect(screen.getAllByText(/ACME_ANALYTICS, ACME_APAC, ACME_PROD/)).not.toHaveLength(0);
    expect(screen.getAllByText("partial").length).toBeGreaterThan(0);
  });

  it("renders the reason where the number would be when a scope cannot answer", async () => {
    useScopeStore.setState({ scope: "account", account: "ACME_SANDBOX" });
    stub({ "/api/v1/metrics/cost.spend_usd/tile": { body: SCOPE_UNAVAILABLE_TILE } });
    renderWithClient(<ExecutivePage />);

    expect(
      await screen.findByText(/describes the whole organization/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Switch to organization scope to see it/)).toBeInTheDocument();
    // R3: never a zero, and never an empty tile, where the answer is unknown.
    expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
  });

  it("explains an unanswerable chart instead of raising an alarm about it", async () => {
    useScopeStore.setState({ scope: "account", account: "ACME_SANDBOX" });
    stub({
      "/api/v1/metrics/query": { status: 422, body: SCOPE_UNAVAILABLE_PROBLEM },
    });
    renderWithClient(<ExecutivePage />);

    expect(
      await screen.findAllByText(/reads ACCOUNT_USAGE, which returns one account per connection/),
    ).not.toHaveLength(0);
    expect(screen.queryByText("Chart unavailable")).not.toBeInTheDocument();
  });
});
