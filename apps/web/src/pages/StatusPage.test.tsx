import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import StatusPage from "@/pages/StatusPage";

const NOT_REQUIRED_DETAIL =
  "Not used by this deployment — Redis is the background worker's queue, and this process " +
  "runs no worker. Set READINESS__REQUIRE_REDIS=true to gate on it.";

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

function stub(readyz: { status: number; body: unknown }) {
  const table = { ...responses, "/readyz": readyz };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      const match = table[path];
      if (!match) throw new Error(`Unexpected fetch: ${path}`);
      return {
        ok: match.status >= 200 && match.status < 300,
        status: match.status,
        json: async () => match.body,
      };
    }),
  );
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <StatusPage />
    </QueryClientProvider>,
  );
}

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

  it("does not report a component this deployment does not use as a failure", async () => {
    // The demo run from a checkout starts no Postgres and no Redis, and needs
    // neither. Two red crosses told the reader something was broken when
    // nothing they could use was — and sent them looking for a fault that did
    // not exist.
    stub({
      status: 200,
      body: {
        status: "ready",
        version: "0.1.0",
        components: [
          {
            name: "postgres",
            status: "not_required",
            required: false,
            detail: "Not used by this deployment — app metadata has no durable store yet.",
          },
          { name: "redis", status: "not_required", required: false, detail: NOT_REQUIRED_DETAIL },
        ],
      },
    });
    renderPage();

    expect(await screen.findByText("postgres not required")).toBeInTheDocument();
    expect(await screen.findByText("redis not required")).toBeInTheDocument();

    // Neither of the two wrong answers: no failure claimed, and no tick for a
    // check that never ran.
    expect(screen.queryByText(/unavailable/)).not.toBeInTheDocument();
    expect(screen.queryByText(/reachable/)).not.toBeInTheDocument();

    // And the reason travels with it, so "not required" is not a shrug.
    expect(screen.getByText(NOT_REQUIRED_DETAIL)).toBeInTheDocument();
  });

  it("still reports a required component that is down", async () => {
    stub({
      status: 503,
      body: {
        status: "not_ready",
        version: "0.1.0",
        components: [
          { name: "postgres", status: "not_required", required: false, detail: "Not used here." },
          { name: "redis", status: "unavailable", required: true, detail: "ConnectionError" },
        ],
      },
    });
    renderPage();

    expect(await screen.findByText("redis unavailable (ConnectionError)")).toBeInTheDocument();
    expect(screen.getByText("postgres not required")).toBeInTheDocument();
  });
});
