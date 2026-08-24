import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Provenance, ProvenanceContribution } from "@/api/client";
import FreshnessBanner from "@/components/FreshnessBanner";
import { summariseFreshness } from "@/lib/freshness";
import { summariseScope } from "@/lib/scope";
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

// ───────────────────────────────────────────────────────────────────── scope
// An organization figure computed over the accounts landed so far is not the
// whole organization. It is a correct number with an incomplete denominator,
// and presenting it as the fleet total is the same class of error as showing a
// zero for an unknown (R3).

const wholeOrganization: ProvenanceContribution = {
  ...meteringBacked,
  scope: "organization",
  scope_account: null,
  scope_partial: false,
  contributing_accounts: ["ACME_PROD", "ACME_SANDBOX"],
};

const partialOrganization: ProvenanceContribution = {
  ...meteringBacked,
  scope: "organization",
  scope_account: null,
  scope_partial: true,
  contributing_accounts: ["ACME_PROD", "ACME_ANALYTICS"],
};

const oneAccount: ProvenanceContribution = {
  ...meteringBacked,
  scope: "account",
  scope_account: "ACME_PROD",
  scope_partial: false,
  contributing_accounts: ["ACME_PROD"],
};

describe("summariseScope", () => {
  it("names the scope every figure on the page was computed at", () => {
    expect(summariseScope([wholeOrganization, wholeOrganization]).label).toBe("Organization");
    expect(summariseScope([oneAccount]).label).toBe("ACME_PROD");
  });

  it("names no scope when the page's figures disagree about theirs", () => {
    expect(summariseScope([wholeOrganization, oneAccount]).label).toBeNull();
  });

  it("warns when an organization roll-up covers only part of the fleet", () => {
    const summary = summariseScope([partialOrganization]);

    expect(summary.partial).toBe(true);
    expect(summary.accounts).toEqual(["ACME_ANALYTICS", "ACME_PROD"]);
    expect(summary.warning).toContain("Partial organization roll-up");
    expect(summary.warning).toContain("ACME_ANALYTICS, ACME_PROD");
    // The absent accounts are absent, not zero.
    expect(summary.warning).toContain("not counted as zero");
  });

  it("unions the accounts behind several partial figures rather than intersecting", () => {
    const other: ProvenanceContribution = {
      ...partialOrganization,
      contributing_accounts: ["ACME_APAC"],
    };
    expect(summariseScope([partialOrganization, other]).accounts).toEqual([
      "ACME_ANALYTICS",
      "ACME_APAC",
      "ACME_PROD",
    ]);
  });

  it("stays silent about scope for an endpoint that reports none", () => {
    // The allocation and coverage endpoints carry provenance but no scope.
    const summary = summariseScope([meteringBacked, undefined, null]);
    expect(summary.label).toBeNull();
    expect(summary.partial).toBe(false);
  });
});

describe("FreshnessBanner — scope", () => {
  it("states the scope beside the freshness floor", () => {
    render(<FreshnessBanner contributions={[oneAccount]} sources={SOURCES} />);
    expect(screen.getByText("ACME_PROD")).toBeInTheDocument();
  });

  it("says so when an organization figure covers only the accounts landed so far", () => {
    render(<FreshnessBanner contributions={[partialOrganization]} sources={SOURCES} />);

    expect(screen.getByText(/Partial organization roll-up/)).toBeInTheDocument();
    expect(screen.getByText(/ACME_ANALYTICS, ACME_PROD/)).toBeInTheDocument();
  });

  it("does not warn when the roll-up covers the whole fleet", () => {
    render(<FreshnessBanner contributions={[wholeOrganization]} sources={SOURCES} />);
    expect(screen.queryByText(/Partial organization roll-up/)).not.toBeInTheDocument();
  });
});
