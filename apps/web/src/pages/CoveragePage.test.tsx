import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import CoveragePage from "@/pages/CoveragePage";
import { COVERAGE, META, ORGANIZATION_COVERAGE, SOURCES } from "@/test/fixtures";
import { stubFetch } from "@/test/http";
import { renderWithClient } from "@/test/render";
import { ORGANIZATION_SCOPE, useScopeStore } from "@/store/scope";

function stub(coverage: unknown = COVERAGE) {
  stubFetch({
    "/api/v1/meta": { body: META },
    "/api/v1/sources": { body: SOURCES },
    "/api/v1/datasets/coverage": { body: coverage },
  });
}

beforeEach(() => {
  useScopeStore.setState(ORGANIZATION_SCOPE);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("CoveragePage — the R3 page", () => {
  it("groups sources by domain and states each one's status in words", async () => {
    stub();
    renderWithClient(<CoveragePage />);

    expect(await screen.findByRole("region", { name: "cost sources" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "warehouse sources" })).toBeInTheDocument();
    expect(screen.getByText("METERING_DAILY_HISTORY")).toBeInTheDocument();
    expect(screen.getAllByText("WAREHOUSE_LOAD_HISTORY").length).toBeGreaterThan(0);
    expect(screen.getByText("Available")).toBeInTheDocument();
    expect(screen.getByText("Missing")).toBeInTheDocument();
  });

  it("shows freshness against the documented latency, never a bare age", async () => {
    stub();
    renderWithClient(<CoveragePage />);

    expect(await screen.findByText("5.7h old")).toBeInTheDocument();
    expect(screen.getAllByText(/documented 3h/).length).toBeGreaterThan(0);
    expect(screen.getByText("Never landed")).toBeInTheDocument();
  });

  it("shows the window that landed, and says so plainly when none did", async () => {
    stub();
    renderWithClient(<CoveragePage />);

    expect(await screen.findByText(/2026-07-01/)).toBeInTheDocument();
    expect(screen.getByText("none")).toBeInTheDocument();
  });

  it("offers the copy-pastable remediation for anything missing", async () => {
    stub();
    renderWithClient(<CoveragePage />);

    expect(
      await screen.findByText(/Upload an extract of SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_LOAD_HISTORY/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
  });

  it("names the KPIs a missing source blocks", async () => {
    stub();
    renderWithClient(<CoveragePage />);

    expect(await screen.findByText("wh.queue_overload_pct")).toBeInTheDocument();
    expect(screen.getByText("Unavailable — requires warehouse_load_history")).toBeInTheDocument();
  });

  it("filters to the sources that need action", async () => {
    stub();
    renderWithClient(<CoveragePage />);

    const toggle = await screen.findByLabelText("Show only sources needing action");
    await userEvent.click(toggle);

    expect(screen.getAllByText("WAREHOUSE_LOAD_HISTORY").length).toBeGreaterThan(0);
    expect(screen.queryByText("METERING_DAILY_HISTORY")).not.toBeInTheDocument();
  });

  it("says so when everything is present rather than showing an empty page", async () => {
    stub({ ...COVERAGE, sources: [COVERAGE.sources[0]], metrics: [] });
    renderWithClient(<CoveragePage />);

    const toggle = await screen.findByLabelText("Show only sources needing action");
    await userEvent.click(toggle);

    expect(
      screen.getByText(/Every registered source is present and inside its documented latency/),
    ).toBeInTheDocument();
  });
});

// ────────────────────────────────────────────────────── the enterprise question
// "Which of my accounts can you see, and how deeply?" ACME_PROD has landed
// query history; ACME_SANDBOX has only billing. USAGE_IN_CURRENCY_DAILY is
// exported once for the fleet and belongs to neither.

describe("CoveragePage — per account", () => {
  it("shows how much of the source set each account has landed", async () => {
    stub(ORGANIZATION_COVERAGE);
    renderWithClient(<CoveragePage />);

    const panel = await screen.findByRole("region", { name: "Coverage by account" });
    const summary = within(panel).getByRole("table", {
      name: "How much of the account-scoped source set each account has landed",
    });

    // ACME_PROD has both account-scoped sources; ACME_SANDBOX has one of them.
    const prod = within(summary).getByText("ACME_PROD").closest("tr") as HTMLElement;
    expect(within(prod).getAllByText("2 of 2")).toHaveLength(2);
    expect(within(prod).getByText("complete")).toBeInTheDocument();

    const sandbox = within(summary).getByText("ACME_SANDBOX").closest("tr") as HTMLElement;
    expect(within(sandbox).getAllByText("1 of 2")).toHaveLength(2);
    expect(within(sandbox).getByText("1 missing")).toBeInTheDocument();
  });

  it("counts only account-scoped sources per account, never the fleet's own", async () => {
    // USAGE_IN_CURRENCY_DAILY is exported once for the organization. Counting
    // it against each account would report a gap no account can close.
    stub(ORGANIZATION_COVERAGE);
    renderWithClient(<CoveragePage />);

    const panel = await screen.findByRole("region", { name: "Coverage by account" });
    expect(panel).toHaveTextContent("2 account-scoped sources");
    expect(within(panel).queryByText("USAGE_IN_CURRENCY_DAILY")).not.toBeInTheDocument();
  });

  it("names a source one account has and another does not", async () => {
    stub(ORGANIZATION_COVERAGE);
    renderWithClient(<CoveragePage />);

    const panel = await screen.findByRole("region", { name: "Coverage by account" });
    await userEvent.click(within(panel).getByText("Source by account"));

    const row = within(panel).getByText("QUERY_HISTORY").closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("Available")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("Missing")).toBeInTheDocument();
  });

  it("answers for one account when that account is the selected scope", async () => {
    useScopeStore.setState({ scope: "account", account: "ACME_SANDBOX" });
    stub(ORGANIZATION_COVERAGE);
    renderWithClient(<CoveragePage />);

    const queryDomain = await screen.findByRole("region", { name: "query sources" });
    expect(within(queryDomain).getByText("Missing")).toBeInTheDocument();

    const costDomain = screen.getByRole("region", { name: "cost sources" });
    // 80 rows for ACME_SANDBOX, not the 480 the whole tenant landed.
    expect(within(costDomain).getByText("80")).toBeInTheDocument();
    expect(within(costDomain).queryByText("480")).not.toBeInTheDocument();
  });

  it("does not ask an account for a source exported once for the fleet", async () => {
    useScopeStore.setState({ scope: "account", account: "ACME_SANDBOX" });
    stub(ORGANIZATION_COVERAGE);
    renderWithClient(<CoveragePage />);

    const organizationPanel = await screen.findByRole("region", {
      name: "Organization-scoped sources",
    });
    expect(within(organizationPanel).getByText("USAGE_IN_CURRENCY_DAILY")).toBeInTheDocument();
    expect(organizationPanel).toHaveTextContent(/not ACME_SANDBOX's to upload/);

    // …and it is not counted as one of the account's own sources.
    const costDomain = screen.getByRole("region", { name: "cost sources" });
    expect(within(costDomain).queryByText("USAGE_IN_CURRENCY_DAILY")).not.toBeInTheDocument();
  });

  it("says what to upload for an account whose siblings already have a source", async () => {
    useScopeStore.setState({ scope: "account", account: "ACME_SANDBOX" });
    stub(ORGANIZATION_COVERAGE);
    renderWithClient(<CoveragePage />);

    expect(
      await screen.findByText(
        /has landed for other accounts but not for ACME_SANDBOX/,
      ),
    ).toBeInTheDocument();
  });
});
