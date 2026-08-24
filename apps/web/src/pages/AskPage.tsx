import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  type AgentAnswer,
  type AgentInfo,
  type AgentStreamEvent,
  type TraceStep,
  fetchAgents,
  fetchSources,
  streamAgent,
} from "@/api/client";
import PageFrame from "@/components/PageFrame";
import Panel from "@/components/Panel";
import { ErrorState } from "@/components/states";
import { shortObjectName } from "@/lib/format";

// The agent console (BUILD_PROMPT §12, §16). Two things make this page part of
// the product rather than a chat toy:
//
//   1. Every answer is shown with its trace — the tools called, the metrics
//      used, and the SQL behind each figure. An answer you cannot audit is not
//      an answer this platform is willing to give (R5).
//   2. An answer the runtime could not ground is displayed as a refusal, in
//      the refusal's own words, not styled as a finding. The visual weight of
//      an answer must match how much it can be relied on.

const SUGGESTIONS = [
  "What were our billed credits?",
  "Which warehouse costs the most?",
  "Why did spend change week over week?",
  "Which warehouses are queueing?",
  "How much spend is untagged?",
  "Which source views are loaded?",
];

/** Trace steps a reader cares about. Model-internal chatter is not one. */
const VISIBLE_STEPS = new Set([
  "tool_call",
  "tool_result",
  "tool_error",
  "guardrail_block",
  "budget_stop",
  "refusal",
]);

interface TurnState {
  question: string;
  agent?: string;
  steps: TraceStep[];
  answer?: AgentAnswer;
  error?: string;
  running: boolean;
}

function stepLabel(step: TraceStep): string {
  const tool = typeof step.detail.tool === "string" ? step.detail.tool : undefined;
  switch (step.kind) {
    case "tool_call":
      return tool ? `Calling ${tool}` : "Calling a tool";
    case "tool_result":
      return step.summary;
    case "tool_error":
      return `Tool failed: ${step.summary}`;
    case "guardrail_block":
      return `Blocked: ${step.summary}`;
    case "budget_stop":
      return `Stopped: ${step.summary}`;
    case "refusal":
      return `Declined: ${step.summary}`;
    default:
      return step.summary;
  }
}

function StepList({ steps, running }: { steps: TraceStep[]; running: boolean }) {
  const visible = steps.filter((step) => VISIBLE_STEPS.has(step.kind));
  if (visible.length === 0 && !running) return null;
  return (
    <ol className="space-y-1 text-xs text-slate-600" aria-live="polite">
      {visible.map((step, index) => (
        <li key={`${step.kind}-${index}`} className="flex items-baseline gap-2">
          <span aria-hidden className="text-slate-400">
            {step.kind === "tool_error" || step.kind === "guardrail_block" ? "✕" : "•"}
          </span>
          <span className={step.kind === "guardrail_block" ? "text-amber-800" : undefined}>
            {stepLabel(step)}
          </span>
          {typeof step.elapsed_ms === "number" && step.elapsed_ms > 0 ? (
            <span className="tabular-nums text-slate-400">{Math.round(step.elapsed_ms)} ms</span>
          ) : null}
        </li>
      ))}
      {running ? (
        <li className="flex items-baseline gap-2 text-slate-400">
          <span aria-hidden>•</span>
          <span className="animate-pulse">working…</span>
        </li>
      ) : null}
    </ol>
  );
}

function SqlDisclosure({ statements }: { statements: string[] }) {
  if (statements.length === 0) return null;
  return (
    <details className="mt-3 border-t border-slate-200 pt-2">
      <summary className="cursor-pointer list-none text-[11px] font-medium text-slate-700 underline decoration-dotted underline-offset-2 focus-visible:outline-2 focus-visible:outline-offset-2">
        Show the SQL ({statements.length})
      </summary>
      <div className="mt-2 space-y-2">
        {statements.map((sql, index) => (
          <pre
            key={index}
            className="overflow-x-auto rounded bg-slate-900 p-2 font-mono text-[11px] leading-relaxed text-slate-100"
          >
            {sql}
          </pre>
        ))}
      </div>
    </details>
  );
}

function AnswerBody({
  answer,
  sourceNames,
}: {
  answer: AgentAnswer;
  sourceNames: (id: string) => string;
}) {
  // A refusal is presented as a refusal: plain, unadorned, and not dressed up
  // as a result. Nothing about it should read as a figure.
  if (answer.refused || !answer.grounded) {
    return (
      <div className="rounded border border-dashed border-amber-300 bg-amber-50 p-3">
        <p className="text-sm whitespace-pre-wrap text-amber-900">{answer.answer}</p>
        {answer.refusal_reason && answer.refusal_reason !== answer.answer ? (
          <p className="mt-2 text-xs text-amber-800">Reason: {answer.refusal_reason}</p>
        ) : null}
      </div>
    );
  }

  return (
    <div>
      <p className="text-sm leading-relaxed whitespace-pre-wrap text-slate-800">{answer.answer}</p>
      <dl className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">
        <div className="flex gap-1">
          <dt>Answered by</dt>
          <dd className="font-medium text-slate-700">{answer.agent}</dd>
        </div>
        {answer.metrics_used.length > 0 ? (
          <div className="flex gap-1">
            <dt>Metrics</dt>
            <dd className="font-mono text-slate-700">{answer.metrics_used.join(", ")}</dd>
          </div>
        ) : null}
        {answer.sources_used.length > 0 ? (
          <div className="flex gap-1">
            <dt>Sources</dt>
            <dd className="text-slate-700">
              {answer.sources_used.map((id) => sourceNames(id)).join(", ")}
            </dd>
          </div>
        ) : null}
      </dl>
      <SqlDisclosure statements={answer.sql} />
    </div>
  );
}

export default function AskPage() {
  const [question, setQuestion] = useState("");
  const [agent, setAgent] = useState<string>("");
  const [turns, setTurns] = useState<TurnState[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  const agents = useQuery({ queryKey: ["agents"], queryFn: fetchAgents });
  const sources = useQuery({ queryKey: ["sources"], queryFn: fetchSources });

  const sourceNames = (id: string): string => {
    const definition = sources.data?.find((source) => source.id === id);
    return definition ? shortObjectName(definition.snowflake_object) : id.toUpperCase();
  };

  // Abandon an in-flight turn if the page goes away, so the request does not
  // outlive the thing that asked for it.
  useEffect(() => () => abortRef.current?.abort(), []);

  const running = turns.some((turn) => turn.running);

  const ask = async (asked: string) => {
    const trimmed = asked.trim();
    if (!trimmed || running) return;

    setQuestion("");
    const index = turns.length;
    setTurns((current) => [
      ...current,
      {
        question: trimmed,
        agent: agent || undefined,
        steps: [],
        running: true,
      },
    ]);

    const controller = new AbortController();
    abortRef.current = controller;

    const update = (change: Partial<TurnState>) =>
      setTurns((current) =>
        current.map((turn, position) => (position === index ? { ...turn, ...change } : turn)),
      );

    try {
      const steps: TraceStep[] = [];
      let answered = false;
      for await (const event of streamAgent(trimmed, agent || undefined, controller.signal)) {
        if (event.event === "answer") {
          answered = true;
          update({ answer: asAnswer(event, trimmed), running: false });
          continue;
        }
        if (event.event === "agent_selected") {
          update({ agent: String(event.agent ?? "") });
          continue;
        }
        steps.push(asStep(event));
        update({ steps: [...steps] });
      }
      if (!answered) {
        // The stream ended without an answer frame. Saying so beats leaving a
        // spinner running forever.
        update({
          running: false,
          error: "The connection closed before an answer arrived. Try asking again.",
        });
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      update({
        running: false,
        error: error instanceof Error ? error.message : "The request failed.",
      });
    } finally {
      abortRef.current = null;
    }
  };

  return (
    <PageFrame
      title="Ask"
      description="Ask about cost, performance, or governance. Every answer shows the metrics, sources, and SQL behind it."
      contributions={[]}
      sources={sources.data}
    >
      <Panel
        title="Question"
        subtitle="The supervisor picks a specialist unless you name one."
        actions={
          <label className="flex items-center gap-2 text-xs text-slate-600">
            <span>Specialist</span>
            <select
              className="rounded border border-slate-300 bg-white px-2 py-1 text-xs"
              value={agent}
              onChange={(event) => setAgent(event.target.value)}
            >
              <option value="">Route automatically</option>
              {(agents.data ?? []).map((info: AgentInfo) => (
                <option key={info.name} value={info.name}>
                  {info.name}
                </option>
              ))}
            </select>
          </label>
        }
      >
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void ask(question);
          }}
          className="flex gap-2"
        >
          <label className="sr-only" htmlFor="agent-question">
            Your question
          </label>
          <input
            id="agent-question"
            className="flex-1 rounded border border-slate-300 px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-offset-2"
            placeholder="What were our billed credits?"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            disabled={running}
          />
          <button
            type="submit"
            className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-400"
            disabled={running || question.trim().length === 0}
          >
            {running ? "Asking…" : "Ask"}
          </button>
        </form>

        <div className="mt-3 flex flex-wrap gap-2">
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              className="rounded-full border border-slate-300 px-3 py-1 text-xs text-slate-600 hover:border-slate-400 hover:text-slate-900 disabled:opacity-50"
              onClick={() => void ask(suggestion)}
              disabled={running}
            >
              {suggestion}
            </button>
          ))}
        </div>
      </Panel>

      {turns.length === 0 ? (
        <Panel title="Nothing asked yet" subtitle="Pick a suggestion above, or type a question.">
          <p className="text-sm text-slate-600">
            Answers are computed from the same governed metrics the dashboards use, so a figure here
            and a figure on the executive page are the same figure. The assistant selects metrics —
            it never writes SQL of its own, and it never states a number a query did not return.
          </p>
        </Panel>
      ) : null}

      {turns
        .slice()
        .reverse()
        .map((turn, position) => (
          <Panel
            key={turns.length - position}
            title={turn.question}
            subtitle={turn.agent ? `${turn.agent} specialist` : undefined}
          >
            <StepList steps={turn.steps} running={turn.running} />
            {turn.error ? (
              <div className="mt-3">
                <ErrorState
                  title="The question could not be answered"
                  error={turn.error}
                  remediation="Check that the API is reachable, then ask again."
                  onRetry={() => void ask(turn.question)}
                />
              </div>
            ) : null}
            {turn.answer ? (
              <div className="mt-3">
                <AnswerBody answer={turn.answer} sourceNames={sourceNames} />
              </div>
            ) : null}
          </Panel>
        ))}
    </PageFrame>
  );
}

function asStep(event: AgentStreamEvent): TraceStep {
  const raw = event as Record<string, unknown>;
  return {
    kind: String(raw.event ?? ""),
    summary: String(raw.summary ?? ""),
    elapsed_ms: typeof raw.elapsed_ms === "number" ? raw.elapsed_ms : null,
    detail: (raw.detail as Record<string, unknown>) ?? {},
  };
}

/** Build the answer from the stream's final frame, which carries its own SQL. */
function asAnswer(event: AgentStreamEvent, question: string): AgentAnswer {
  const raw = event as Record<string, unknown>;
  return {
    answer: String(raw.answer ?? ""),
    agent: String(raw.agent ?? ""),
    grounded: raw.grounded === true,
    refused: raw.refused === true,
    refusal_reason: typeof raw.refusal_reason === "string" ? raw.refusal_reason : null,
    metrics_used: Array.isArray(raw.metrics) ? raw.metrics.map(String) : [],
    sources_used: Array.isArray(raw.sources) ? raw.sources.map(String) : [],
    sql: Array.isArray(raw.sql) ? raw.sql.map(String) : [],
    trace_id: String(raw.trace_id ?? question),
    steps: [],
    input_tokens: 0,
    output_tokens: 0,
  };
}
