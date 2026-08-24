import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Provenance } from "@/api/client";
import FreshnessBanner from "@/components/FreshnessBanner";
import { summariseFreshness } from "@/lib/freshness";
import { SOURCES } from "@/test/fixtures";

const attributionBacked: Provenance = {
  as_of: "2026-08-24T14:32:00Z",
  latency_floor_minutes: 480,
  provisional: false,
  sources: ["query_attribution_history", "query_history"],
};

const meteringBacked: Provenance = {
  as_of: "2026-08-24T15:10:00Z",
  latency_floor_minutes: 180,
  provisional: false,
  sources: ["metering_daily_history"],
};

const currencyBacked: Provenance = {
  as_of: "2026-08-24T15:11:00Z",
  latency_floor_minutes: 4320,
  provisional: true,
  sources: ["usage_in_currency_daily"],
};

describe("summariseFreshness", () => {
  it("names the slowest contributing source and its floor", () => {
    const summary = summariseFreshness([attributionBacked, meteringBacked], SOURCES);

    expect(summary.latencyFloorMinutes).toBe(480);
    expect(summary.slowestSource).toBe("QUERY_ATTRIBUTION_HISTORY");
    expect(summary.text).toContain("data no fresher than 8h (QUERY_ATTRIBUTION_HISTORY)");
  });

  it("reports the oldest as-of across contributions, not the newest", () => {
    const summary = summariseFreshness([meteringBacked, attributionBacked], SOURCES);
    expect(summary.asOf).toBe("2026-08-24T14:32:00Z");
  });

  it("flags the page as provisional when any figure may restate", () => {
    expect(summariseFreshness([meteringBacked, currencyBacked], SOURCES).provisional).toBe(true);
    expect(summariseFreshness([meteringBacked], SOURCES).provisional).toBe(false);
  });

  it("raises the floor to the registry's figure when a metric declares a tighter one", () => {
    // q.failure_rate declares a 45-minute floor but reads QUERY_ATTRIBUTION_HISTORY,
    // documented at 8h. Understating staleness is the one thing R7 forbids.
    const tightlyDeclared: Provenance = {
      as_of: "2026-08-24T14:32:00Z",
      latency_floor_minutes: 45,
      provisional: false,
      sources: ["query_history", "query_attribution_history"],
    };
    const summary = summariseFreshness([tightlyDeclared], SOURCES);

    expect(summary.latencyFloorMinutes).toBe(480);
    expect(summary.text).toContain("data no fresher than 8h (QUERY_ATTRIBUTION_HISTORY)");
  });

  it("names a single source even without the registry, and stays silent about several", () => {
    const single: Provenance = { ...attributionBacked, sources: ["query_attribution_history"] };
    expect(summariseFreshness([single]).text).toContain("8h (QUERY_ATTRIBUTION_HISTORY)");

    // Two unregistered inputs: naming either one would be a guess.
    const summary = summariseFreshness([attributionBacked]);
    expect(summary.slowestSource).toBeNull();
    expect(summary.text).toBe("As of 14:32 · data no fresher than 8h");
  });

  it("says it is waiting rather than claiming freshness it cannot prove", () => {
    const summary = summariseFreshness([undefined, null]);
    expect(summary.latencyFloorMinutes).toBeNull();
    expect(summary.text).toBe("Waiting for data — no source has answered yet.");
  });
});

describe("FreshnessBanner", () => {
  it("renders the as-of time, the floor, and the source that sets it", () => {
    render(<FreshnessBanner contributions={[attributionBacked]} sources={SOURCES} />);

    expect(
      screen.getByText(/As of \d{2}:\d{2} · data no fresher than 8h \(QUERY_ATTRIBUTION_HISTORY\)/),
    ).toBeInTheDocument();
  });

  it("adds a provisional badge when a contributing figure may restate", () => {
    render(<FreshnessBanner contributions={[currencyBacked]} sources={SOURCES} />);

    expect(screen.getByText("Provisional")).toBeInTheDocument();
    expect(screen.getByText("figures on this page may restate")).toBeInTheDocument();
    expect(screen.getByText(/data no fresher than 3d \(USAGE_IN_CURRENCY_DAILY\)/)).toBeInTheDocument();
  });

  it("omits the provisional badge when nothing on the page restates", () => {
    render(<FreshnessBanner contributions={[meteringBacked]} sources={SOURCES} />);
    expect(screen.queryByText("Provisional")).not.toBeInTheDocument();
  });
});
