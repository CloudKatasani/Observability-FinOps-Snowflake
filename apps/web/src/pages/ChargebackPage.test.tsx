import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import ChargebackPage from "@/pages/ChargebackPage";
import {
  BLOCKED_ALLOCATION,
  META,
  PUBLISHED_ALLOCATION,
  SOURCES,
  TEAM_ROWS,
} from "@/test/fixtures";
import { stubFetch } from "@/test/http";
import { renderWithClient } from "@/test/render";
import { ORGANIZATION_SCOPE, useScopeStore } from "@/store/scope";

// ECharts needs a canvas; the figures it draws are asserted through the table
// beside it, which is the accessible copy of the same data.
vi.mock("@/charts/Chart", () => ({ default: () => null }));

function stub(allocation: unknown) {
  stubFetch({
    "/api/v1/meta": { body: META },
    "/api/v1/sources": { body: SOURCES },
    "/api/v1/chargeback/allocation": { body: allocation },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  useScopeStore.setState(ORGANIZATION_SCOPE);
});

describe("ChargebackPage — the R6 gate", () => {
  it("shows the blocking banner and no team figures when the gate fails", async () => {
    stub(BLOCKED_ALLOCATION);
    renderWithClient(<ChargebackPage />);

    const banner = await screen.findByRole("alert", {
      name: "Reconciliation gate",
    });
    expect(banner).toBeInTheDocument();
    expect(
      screen.getByText("Blocked — chargeback figures are withheld"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(BLOCKED_ALLOCATION.reconciliation.banner),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/R6 forbids publishing allocated cost/),
    ).toBeInTheDocument();

    // The payload carried team rows anyway. None of them may be rendered:
    // the gate's verdict decides, not the shape of the response.
    for (const team of TEAM_ROWS) {
      expect(screen.queryByText(team.team)).not.toBeInTheDocument();
    }
    expect(screen.queryByText("4,483.2")).not.toBeInTheDocument();
    expect(screen.queryByText("$13,449.72")).not.toBeInTheDocument();
    expect(screen.queryByText("29.9%")).not.toBeInTheDocument();

    // And nothing is quietly zeroed in place of the withheld figures.
    expect(screen.queryByText("0.0")).not.toBeInTheDocument();
  });

  it("shows the failing variance evidence so the gate can be cleared", async () => {
    stub(BLOCKED_ALLOCATION);
    renderWithClient(<ChargebackPage />);

    expect(
      await screen.findByText("Worst-variance days (1)"),
    ).toBeInTheDocument();
    expect(screen.getByText("2026-08-18")).toBeInTheDocument();
    // The day's own variance, formatted from its decimal string.
    expect(screen.getByText("-73.259%")).toBeInTheDocument();
    expect(screen.getByText("-1,875.42")).toBeInTheDocument();
  });

  it("publishes team figures only when the gate passes", async () => {
    stub(PUBLISHED_ALLOCATION);
    renderWithClient(<ChargebackPage />);

    expect(
      await screen.findByText("Reconciled — chargeback figures are published"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("alert", { name: "Reconciliation gate" }),
    ).not.toBeInTheDocument();

    expect(screen.getByText("TEAM_ML")).toBeInTheDocument();
    expect(screen.getByText("TEAM_ANALYTICS")).toBeInTheDocument();
    // Credit figures are formatted from the API's decimal strings, not parsed.
    expect(screen.getByText("4,483.2")).toBeInTheDocument();
    expect(screen.getByText("$13,449.72")).toBeInTheDocument();
    expect(screen.getByText("29.9%")).toBeInTheDocument();
  });

  it("states the freshness floor of the chargeback sources", async () => {
    stub(PUBLISHED_ALLOCATION);
    renderWithClient(<ChargebackPage />);

    expect(
      await screen.findByText(
        /data no fresher than 8h \(QUERY_ATTRIBUTION_HISTORY\)/,
      ),
    ).toBeInTheDocument();
  });

  it("shows every statement behind the allocation, each labelled with its part", async () => {
    // R5 on a composite figure. The endpoint used to omit its SQL entirely and
    // the page could only name the metrics to go and run by hand.
    stub(PUBLISHED_ALLOCATION);
    const user = userEvent.setup();
    renderWithClient(<ChargebackPage />);

    await user.click(await screen.findByText(/Show the SQL/));
    const sql = screen.getByText(/SELECT/, { selector: "pre, code" });
    for (const disclosure of PUBLISHED_ALLOCATION.sql) {
      expect(sql.textContent).toContain(disclosure.purpose);
      expect(sql.textContent).toContain(disclosure.sql);
    }
  });

  it("shows a remediation action when the endpoint fails", async () => {
    stubFetch({
      "/api/v1/meta": { body: META },
      "/api/v1/sources": { body: SOURCES },
      "/api/v1/chargeback/allocation": { status: 500, body: {} },
    });
    renderWithClient(<ChargebackPage />);

    expect(
      await screen.findByText("Chargeback unavailable"),
    ).toBeInTheDocument();
    expect(screen.getByText(/WAREHOUSE_METERING_HISTORY/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Try again" }),
    ).toBeInTheDocument();
  });
});

describe("ChargebackPage — scope", () => {
  /** Every URL the page asked for, so the request itself can be asserted. */
  function requestedUrls(): string[] {
    return vi.mocked(fetch).mock.calls.map(([input]) => String(input));
  }

  it("asks for one account's allocation when an account is selected", async () => {
    useScopeStore.getState().select({ scope: "account", account: "ACME_PROD" });
    stub({
      ...PUBLISHED_ALLOCATION,
      scope: "account",
      scope_account: "ACME_PROD",
      contributing_accounts: ["ACME_PROD"],
    });
    renderWithClient(<ChargebackPage />);

    // The figures must be the account's, not the organization's relabelled —
    // which starts with the request carrying the scope at all.
    await screen.findByText(TEAM_ROWS[0].team);
    const allocationCall = requestedUrls().find((url) =>
      url.includes("/api/v1/chargeback/allocation"),
    );
    expect(allocationCall).toContain("scope=account");
    expect(allocationCall).toContain("account=ACME_PROD");
    expect(
      screen.getByText(
        /allocated within ACME_PROD and reconciled against that account/,
      ),
    ).toBeInTheDocument();
  });

  it("allocates the whole organization when no account is selected", async () => {
    stub(PUBLISHED_ALLOCATION);
    renderWithClient(<ChargebackPage />);

    await screen.findByText(TEAM_ROWS[0].team);
    const allocationCall = requestedUrls().find((url) =>
      url.includes("/api/v1/chargeback/allocation"),
    );
    expect(allocationCall).toContain("scope=organization");
    expect(allocationCall).not.toContain("account=");
    expect(
      screen.getByText(/allocated across every landed account/),
    ).toBeInTheDocument();
  });
});
