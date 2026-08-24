import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import StatusPage from "@/pages/StatusPage";

const responses: Record<string, { status: number; body: unknown }> = {
  "/healthz": { status: 200, body: { status: "ok", version: "0.1.0" } },
  "/readyz": {
    status: 503,
    body: {
      status: "not_ready",
      version: "0.1.0",
      components: [
        { name: "postgres", status: "unavailable", detail: "ConnectionRefusedError" },
        { name: "redis", status: "ok" },
      ],
    },
  },
  "/api/v1/meta": {
    status: 200,
    body: {
      version: "0.1.0",
      mode: "auto",
      tenancy: "single",
      branding: {
        display_name: "Observability & FinOps Platform for Snowflake",
        short_name: "snowobs",
        palette: { navy: "#12446E", primary: "#0070AD", sky: "#12ABDB", coral: "#E94B89" },
      },
    },
  },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("StatusPage", () => {
  it("shows API health and per-component readiness including failures", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => {
        const match = responses[path];
        if (!match) throw new Error(`Unexpected fetch: ${path}`);
        return {
          ok: match.status >= 200 && match.status < 300,
          status: match.status,
          json: async () => match.body,
        };
      }),
    );

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <StatusPage />
      </QueryClientProvider>,
    );

    expect(
      await screen.findByText("Observability & FinOps Platform for Snowflake"),
    ).toBeInTheDocument();
    expect(await screen.findByText("API up (v0.1.0)")).toBeInTheDocument();
    expect(
      await screen.findByText("postgres unavailable (ConnectionRefusedError)"),
    ).toBeInTheDocument();
    expect(await screen.findByText("redis reachable")).toBeInTheDocument();
  });
});
