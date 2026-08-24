import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import AskPage from "@/pages/AskPage";
import { renderWithClient } from "@/test/render";

// The agent console is judged on one thing above all: an answer must arrive
// with its provenance, and an answer the runtime could not ground must not be
// presented as a finding.

const AGENTS = [
  {
    name: "finops",
    description: "Spend and allocation.",
    tools: ["query_metric"],
  },
  { name: "sre", description: "Pipeline health.", tools: ["query_metric"] },
];

const SOURCES = [
  {
    id: "metering_daily_history",
    snowflake_object: "SNOWFLAKE.ORGANIZATION_USAGE.METERING_DAILY_HISTORY",
    domain: "cost",
    criticality: "core",
    documented_latency_minutes: 180,
    latency_verified: true,
  },
];

const SQL = 'SELECT SUM("CREDITS") AS COST_BILLED_CREDITS FROM metering_daily_history';

/** Frames the streaming endpoint emits for one successful turn. */
function groundedTurn(): string[] {
  return [
    { event: "agent_selected", agent: "finops" },
    {
      event: "user_message",
      summary: "What were our billed credits?",
      detail: {},
    },
    {
      event: "tool_call",
      summary: "query_metric(metrics)",
      detail: { tool: "query_metric" },
    },
    {
      event: "tool_result",
      summary: "query_metric: 1 row(s)",
      elapsed_ms: 12.5,
      detail: {
        tool: "query_metric",
        sql: SQL,
        sources: ["metering_daily_history"],
      },
    },
    {
      event: "answer",
      answer: "Billed credits for the period were 412.5.",
      agent: "finops",
      trace_id: "t-1",
      grounded: true,
      refused: false,
      refusal_reason: null,
      metrics: ["cost.billed_credits"],
      sources: ["metering_daily_history"],
      sql: [SQL],
    },
  ].map((frame) => `data: ${JSON.stringify(frame)}\n\n`);
}

function refusedTurn(): string[] {
  return [
    { event: "agent_selected", agent: "finops" },
    { event: "refusal", summary: "no metric matched", detail: {} },
    {
      event: "answer",
      answer: "Nothing in the governed metric catalogue matched this question.",
      agent: "finops",
      trace_id: "t-2",
      grounded: false,
      refused: true,
      refusal_reason: "no metric matched the question",
      metrics: [],
      sources: [],
      sql: [],
    },
  ].map((frame) => `data: ${JSON.stringify(frame)}\n\n`);
}

/**
 * Stub fetch for this page, streaming the SSE frames a chunk at a time.
 *
 * Splitting frames across chunk boundaries is deliberate: it is exactly the
 * case a naive line reader gets wrong, and the reason the client buffers.
 */
function stubAgentFetch(frames: string[]): void {
  const encoder = new TextEncoder();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : String(input);

      if (url.startsWith("/api/v1/agents/catalog")) {
        return { ok: true, status: 200, json: async () => AGENTS };
      }
      if (url.startsWith("/api/v1/sources")) {
        return { ok: true, status: 200, json: async () => SOURCES };
      }
      if (url.startsWith("/api/v1/agents/stream")) {
        const payload = frames.join("");
        const midpoint = Math.floor(payload.length / 2);
        const chunks = [payload.slice(0, midpoint), payload.slice(midpoint)];
        return {
          ok: true,
          status: 200,
          body: {
            getReader() {
              let index = 0;
              return {
                async read() {
                  if (index >= chunks.length) return { done: true, value: undefined };
                  return {
                    done: false,
                    value: encoder.encode(chunks[index++]),
                  };
                },
                releaseLock() {},
              };
            },
          },
        };
      }
      throw new Error(`Unexpected fetch: ${url}`);
    }),
  );
}

function renderPage() {
  return renderWithClient(
    <MemoryRouter>
      <AskPage />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AskPage", () => {
  it("shows an answer with the metrics, sources, and SQL behind it", async () => {
    stubAgentFetch(groundedTurn());
    renderPage();

    await userEvent.click(screen.getByRole("button", { name: "What were our billed credits?" }));

    await waitFor(() =>
      expect(screen.getByText(/Billed credits for the period were 412\.5/)).toBeInTheDocument(),
    );

    // R5: the provenance is on the page, not behind a developer flag.
    expect(screen.getByText("cost.billed_credits")).toBeInTheDocument();
    expect(screen.getByText("METERING_DAILY_HISTORY")).toBeInTheDocument();

    const disclosure = screen.getByText(/Show the SQL/);
    await userEvent.click(disclosure);
    expect(screen.getByText(new RegExp("SELECT SUM"))).toBeInTheDocument();
  });

  it("surfaces each tool step as it arrives", async () => {
    stubAgentFetch(groundedTurn());
    renderPage();

    await userEvent.click(screen.getByRole("button", { name: "What were our billed credits?" }));

    await waitFor(() => expect(screen.getByText("Calling query_metric")).toBeInTheDocument());
    expect(screen.getByText("query_metric: 1 row(s)")).toBeInTheDocument();
  });

  it("presents a refusal as a refusal rather than as a finding", async () => {
    stubAgentFetch(refusedTurn());
    renderPage();

    const input = screen.getByLabelText("Your question");
    await userEvent.type(input, "what is the weather in Oslo");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    await waitFor(() =>
      expect(
        screen.getByText(/Nothing in the governed metric catalogue matched/),
      ).toBeInTheDocument(),
    );
    // No metrics or sources are claimed for an answer that has none.
    expect(screen.queryByText("Metrics")).not.toBeInTheDocument();
    expect(screen.queryByText(/Show the SQL/)).not.toBeInTheDocument();
    expect(screen.getByText(/Reason: no metric matched the question/)).toBeInTheDocument();
  });

  it("offers the specialists the API reports, plus automatic routing", async () => {
    stubAgentFetch(groundedTurn());
    renderPage();

    const select = await screen.findByLabelText("Specialist");
    await waitFor(() => expect(screen.getByRole("option", { name: "finops" })).toBeInTheDocument());
    expect(select).toHaveValue("");
    expect(screen.getByRole("option", { name: "Route automatically" })).toBeInTheDocument();
  });
});
