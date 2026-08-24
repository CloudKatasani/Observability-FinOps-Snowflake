import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import CoveragePage from "@/pages/CoveragePage";
import { COVERAGE, META, SOURCES } from "@/test/fixtures";
import { stubFetch } from "@/test/http";
import { renderWithClient } from "@/test/render";

function stub(coverage: unknown = COVERAGE) {
  stubFetch({
    "/api/v1/meta": { body: META },
    "/api/v1/sources": { body: SOURCES },
    "/api/v1/datasets/coverage": { body: coverage },
  });
}

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
