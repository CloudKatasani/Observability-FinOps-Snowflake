import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AppShell from "@/components/AppShell";
import { META, SCOPE_OPTIONS, SOURCES } from "@/test/fixtures";
import { stubFetch } from "@/test/http";
import { ORGANIZATION_SCOPE, useScopeStore } from "@/store/scope";
import { DEFAULT_PRESET, presetRange, useTimeRangeStore } from "@/store/timeRange";

/** Exposes the query string the shell has written, so a link can be asserted. */
function LocationProbe() {
  return <output data-testid="search">{useLocation().search}</output>;
}

function renderShell(entry: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route
              path="/"
              element={
                <>
                  <p>Executive content</p>
                  <LocationProbe />
                </>
              }
            />
            <Route path="/coverage" element={<p>Coverage content</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  const initial = presetRange(DEFAULT_PRESET);
  useTimeRangeStore.setState({ preset: DEFAULT_PRESET, ...initial });
  useScopeStore.setState(ORGANIZATION_SCOPE);
  stubFetch({
    "/api/v1/meta": { body: META },
    "/api/v1/sources": { body: SOURCES },
    "/api/v1/metrics/scopes": { body: SCOPE_OPTIONS },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AppShell", () => {
  it("takes its product name from the branding API rather than hardcoding one", async () => {
    renderShell("/");
    expect(
      await screen.findByText("Observability & FinOps Platform for Snowflake"),
    ).toBeInTheDocument();
    expect(screen.getByText(/snowobs · offline mode · v0.1.0/)).toBeInTheDocument();
  });

  it("links to every dashboard and marks the current one", async () => {
    renderShell("/coverage");

    for (const label of ["Executive", "Platform health", "Chargeback", "Coverage & sources"]) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole("link", { name: "Coverage & sources" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("hydrates the global range from the URL", async () => {
    renderShell("/?range=custom&start=2026-01-01&end=2026-01-31");

    await waitFor(() => {
      expect(useTimeRangeStore.getState().start).toBe("2026-01-01");
    });
    expect(useTimeRangeStore.getState().end).toBe("2026-01-31");
    expect(useTimeRangeStore.getState().preset).toBe("custom");
  });

  it("ignores a malformed range in the URL instead of querying nonsense dates", async () => {
    const fallback = presetRange(DEFAULT_PRESET);
    renderShell("/?start=yesterday&end=tomorrow");

    await screen.findByText("Executive content");
    expect(useTimeRangeStore.getState().start).toBe(fallback.start);
  });

  it("applies a preset chosen in the picker to the shared store", async () => {
    renderShell("/");
    await screen.findByText("Executive content");

    await userEvent.selectOptions(screen.getByLabelText("Period"), "7d");

    const expected = presetRange("7d");
    expect(useTimeRangeStore.getState().preset).toBe("7d");
    expect(useTimeRangeStore.getState().start).toBe(expected.start);
    expect(useTimeRangeStore.getState().end).toBe(expected.end);
  });

  it("hydrates the global scope from the URL", async () => {
    renderShell("/?scope=account&account=ACME_PROD");

    await waitFor(() => {
      expect(useScopeStore.getState().account).toBe("ACME_PROD");
    });
    expect(useScopeStore.getState().scope).toBe("account");
  });

  it("ignores an account scope in the URL that names no account", async () => {
    // The API rejects the same pair, and widening it to the organization
    // silently would answer a question the link did not ask.
    renderShell("/?scope=account");

    await screen.findByText("Executive content");
    expect(useScopeStore.getState()).toMatchObject(ORGANIZATION_SCOPE);
  });

  it("mirrors the chosen scope into the URL beside the range, not instead of it", async () => {
    renderShell("/");
    await screen.findByText("Executive content");

    await userEvent.selectOptions(await screen.findByLabelText("Scope"), "ACME_SANDBOX");

    await waitFor(() => {
      expect(screen.getByTestId("search")).toHaveTextContent("scope=account");
    });
    const search = screen.getByTestId("search").textContent ?? "";
    expect(search).toContain("account=ACME_SANDBOX");
    expect(search).toContain(`range=${DEFAULT_PRESET}`);
    expect(search).toContain(`start=${presetRange(DEFAULT_PRESET).start}`);
  });

  it("drops the account from the URL when the organization is selected again", async () => {
    renderShell("/?scope=account&account=ACME_PROD");
    await screen.findByText("Executive content");

    await userEvent.selectOptions(await screen.findByLabelText("Scope"), "organization");

    await waitFor(() => {
      expect(screen.getByTestId("search")).toHaveTextContent("scope=organization");
    });
    expect(screen.getByTestId("search").textContent).not.toContain("account=");
  });

  it("offers a skip link so the keyboard reaches the content first", async () => {
    renderShell("/");
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main");
  });
});
