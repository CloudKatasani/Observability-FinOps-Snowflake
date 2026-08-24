import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import AppShell from "@/components/AppShell";
import CoveragePage from "@/pages/CoveragePage";
import ExecutivePage from "@/pages/ExecutivePage";
import { ORGANIZATION_SCOPE, useScopeStore } from "@/store/scope";

const API = "http://localhost:8123";
const real = globalThis.fetch;

beforeAll(() => {
  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) =>
    real(`${API}${String(input)}`, init),
  );
});

function renderApp(page: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={page} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("live API", () => {
  it("lists the four accounts with their catalogue coverage", async () => {
    useScopeStore.setState(ORGANIZATION_SCOPE);
    renderApp(<p>page</p>);
    const picker = await screen.findByLabelText("Scope", {}, { timeout: 240_000 });
    const labels = [...picker.querySelectorAll("option")].map((o) => o.textContent);
    console.log("PICKER:", labels);
    expect(labels).toHaveLength(5);
  }, 300_000);

  it("changes the figures when the scope changes", async () => {
    useScopeStore.setState(ORGANIZATION_SCOPE);
    renderApp(<ExecutivePage />);
    const org = await screen.findByText(/^[\d,]+\.\d$/, {}, { timeout: 240_000 });
    const orgText = org.textContent;
    const banner = document.querySelector('[aria-live="polite"]')?.textContent;
    console.log("ORG total credits:", orgText);
    console.log("BANNER:", banner);

    await userEvent.selectOptions(await screen.findByLabelText("Scope"), "ACME_SANDBOX");
    await waitFor(
      () => {
        expect(screen.queryByText(orgText as string)).not.toBeInTheDocument();
      },
      { timeout: 240_000 },
    );
    const after = document.querySelector('[aria-live="polite"]')?.textContent;
    console.log("AFTER SWITCH banner:", after);
    console.log("PROVENANCE:", document.querySelector("summary")?.textContent);
  }, 300_000);

  it("shows the per-account coverage matrix", async () => {
    useScopeStore.setState(ORGANIZATION_SCOPE);
    renderApp(<CoveragePage />);
    const panel = await screen.findByRole(
      "region",
      { name: "Coverage by account" },
      { timeout: 240_000 },
    );
    console.log("MATRIX SUBTITLE:", within(panel).getByText(/account-scoped sources/).textContent);
    const table = within(panel).getByRole("table", {
      name: "How much of the account-scoped source set each account has landed",
    });
    console.log("MATRIX ROWS:", [...table.querySelectorAll("tbody tr")].map((r) => r.textContent));
  }, 300_000);
});
