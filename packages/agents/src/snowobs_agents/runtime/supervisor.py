"""The agent runtime (BUILD_PROMPT §12.1) — a thin, auditable tool-use loop.

Deliberately not a framework. Enterprise review requires every step to be
inspectable and replayable, and a heavyweight agent library hides exactly the
step you need to see when a customer asks why a figure was wrong (§27.12).

The loop is: system prompt → model → tool calls → tool results → model → answer,
with a budget check before every iteration, injection defence on every tool
result, and a grounding check before the answer is released (R12).

With no LLM configured the same loop runs deterministically: the question is
routed to a governed metric by keyword and synonym matching, the tool runs, and
the result is reported with a note that narration is disabled (§19).
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from snowobs_agents.runtime.guardrails import (
    BudgetTracker,
    RedactionPolicy,
    ungrounded_figures,
    wrap_untrusted,
)
from snowobs_agents.runtime.tools import Tool, ToolContext, ToolOutcome, build_registry, specs_for
from snowobs_agents.runtime.trace import StepKind, Trace
from snowobs_common.logging import get_logger
from snowobs_llm.base import (
    Completion,
    LLMProvider,
    Message,
    Role,
    ToolCall,
    ToolResult,
)

logger = get_logger(__name__)

MAX_ITERATIONS = 8

#: Said once, at the end of every deterministic answer. The platform is not
#: apologising for the absence of an LLM — it answered the question — it is
#: being clear about which part of the answer is missing (§19).
_NO_NARRATIVE_NOTE = (
    "Narrative generation is disabled because no LLM provider is configured; "
    "everything above comes straight from the governed metric layer."
)

#: Questions a no-LLM deployment can still answer exactly, by reaching for a
#: tool other than a metric query.
_COVERAGE_PHRASES = (
    "coverage",
    "which sources",
    "what sources",
    "source view",
    "am i missing",
    "missing source",
    "what data do you have",
    "are loaded",
    "is loaded",
    "not loaded",
    "what can you answer",
    "data do i have",
)
_CATALOGUE_PHRASES = (
    "what metrics",
    "which metrics",
    "list the metrics",
    "list metrics",
    "metric catalogue",
    "metric catalog",
    "what kpis",
    "which kpis",
    "what can you measure",
)
_DESCRIBE_PHRASES = (
    "describe",
    "what is",
    "what does",
    "tell me about",
    "explain the metric",
    "how is",
    "definition of",
)


@dataclass
class TurnResult:
    """What one agent turn produced."""

    answer: str
    trace: Trace
    #: True when the answer rests on at least one tool result (R12).
    grounded: bool = False
    refused: bool = False
    #: The raw tool payloads this answer was checked against. Carried on the
    #: result rather than rebuilt from the trace, because the trace records
    #: summaries: a grounding check run against "query_metric: 14 row(s)"
    #: would call every real figure a fabrication.
    tool_outputs: list[str] = field(default_factory=list)

    @property
    def sql_shown(self) -> list[str]:
        return [
            str(step.detail.get("sql", ""))
            for step in self.trace.steps
            if step.kind is StepKind.TOOL_RESULT and step.detail.get("sql")
        ]


@dataclass
class AgentDefinition:
    """A specialist: its prompt, and the tools it is allowed to reach for."""

    name: str
    system_prompt: str
    tool_names: tuple[str, ...]
    description: str = ""


class AgentRuntime:
    """Runs one turn for one agent."""

    def __init__(
        self,
        provider: LLMProvider,
        context: ToolContext,
        *,
        registry: dict[str, Tool] | None = None,
        budget: BudgetTracker | None = None,
        redaction: RedactionPolicy | None = None,
        max_iterations: int = MAX_ITERATIONS,
    ) -> None:
        self.provider = provider
        self.context = context
        self.registry = registry or build_registry()
        self.budget = budget or BudgetTracker()
        self.redaction = redaction or RedactionPolicy()
        self.max_iterations = max_iterations

    # ------------------------------------------------------------------ turn
    def run(
        self,
        agent: AgentDefinition,
        question: str,
        *,
        history: list[Message] | None = None,
    ) -> TurnResult:
        trace = Trace(
            tenant=self.context.tenant,
            actor=self.context.actor,
            agent=agent.name,
            question=question,
        )
        trace.add(StepKind.USER_MESSAGE, question[:400], detail={"question": question})

        daily_stop = self.budget.check_daily(actor=self.context.actor, tenant=self.context.tenant)
        if daily_stop:
            trace.add(StepKind.BUDGET_STOP, daily_stop)
            trace.refusal_reason = daily_stop
            trace.finish(daily_stop)
            return TurnResult(answer=daily_stop, trace=trace, refused=True)

        if not self.provider.generates_narrative:
            return self._run_deterministic(agent, question, trace)

        messages: list[Message] = list(history or [])
        messages.append(Message(role=Role.USER, content=question))
        available = {
            name: tool
            for name, tool in self.registry.items()
            if name in agent.tool_names
            and (not tool.required_roles or (self.context.roles & tool.required_roles))
        }
        tool_specs = specs_for(available, self.context.roles)
        tool_outputs: list[str] = []
        spend = Decimal(0)

        for _ in range(self.max_iterations):
            stop = self.budget.check_turn(
                tokens=trace.usage.total_tokens,
                tool_calls=len(trace.tool_calls),
                spend=spend,
            )
            if stop:
                trace.add(StepKind.BUDGET_STOP, stop)
                trace.refusal_reason = stop
                trace.finish(stop)
                return TurnResult(answer=stop, trace=trace, refused=True)

            started = time.perf_counter()
            completion = self.provider.complete(agent.system_prompt, messages, tools=tool_specs)
            elapsed = (time.perf_counter() - started) * 1000
            trace.add(
                StepKind.MODEL_THINKING,
                completion.text[:400] or "(tool use)",
                elapsed_ms=round(elapsed, 1),
                usage=completion.usage,
                detail={"model": completion.model},
            )

            if not completion.wants_tools:
                return self._finalise(completion, trace, tool_outputs)

            messages.append(
                Message(
                    role=Role.ASSISTANT,
                    content=completion.text,
                    tool_calls=completion.tool_calls,
                )
            )
            results: list[ToolResult] = []
            for call in completion.tool_calls:
                outcome = self._invoke(call, available, trace)
                tool_outputs.append(outcome.content)
                results.append(
                    ToolResult(
                        call_id=call.id,
                        # R9/§12.5: tool output is data, and is fenced as such
                        # before it re-enters the model's context.
                        content=wrap_untrusted(outcome.content, label=call.name),
                        is_error=outcome.is_error,
                    )
                )
            messages.append(Message(role=Role.USER, tool_results=results))

        exhausted = (
            "I could not reach a grounded answer within the tool-call budget for "
            "this turn. Narrowing the question — a single metric, or a shorter "
            "time range — usually resolves it."
        )
        trace.add(StepKind.BUDGET_STOP, exhausted)
        trace.finish(exhausted)
        return TurnResult(answer=exhausted, trace=trace, refused=True)

    # ------------------------------------------------------------- tool call
    def _invoke(self, call: ToolCall, available: dict[str, Tool], trace: Trace) -> ToolOutcome:
        tool = available.get(call.name)
        if tool is None:
            message = (
                f"Tool '{call.name}' is not available to this agent. "
                f"Available: {', '.join(sorted(available))}."
            )
            trace.add(StepKind.GUARDRAIL_BLOCK, message, detail={"tool": call.name})
            return ToolOutcome(content=message, is_error=True)

        trace.add(
            StepKind.TOOL_CALL,
            f"{call.name}({', '.join(sorted(call.arguments))})",
            detail={"tool": call.name, "arguments": call.arguments},
        )
        started = time.perf_counter()
        try:
            outcome = tool.run(self.context, call.arguments)
        except Exception as exc:
            message = f"{call.name} failed: {type(exc).__name__}"
            logger.warning("tool_failed", tool=call.name, error=str(exc))
            trace.add(StepKind.TOOL_ERROR, message, detail={"tool": call.name})
            return ToolOutcome(content=message, is_error=True)

        elapsed = (time.perf_counter() - started) * 1000
        trace.add(
            StepKind.TOOL_RESULT if not outcome.is_error else StepKind.TOOL_ERROR,
            f"{call.name}: {outcome.row_count} row(s)"
            if not outcome.is_error
            else outcome.content[:200],
            elapsed_ms=round(elapsed, 1),
            detail={
                "tool": call.name,
                "sql": outcome.sql,
                "sources": outcome.sources,
                "metrics": outcome.metrics,
                "latency_floor_minutes": outcome.latency_floor_minutes,
                "provisional": outcome.provisional,
            },
        )
        for metric in outcome.metrics:
            if metric not in trace.metrics_used:
                trace.metrics_used.append(metric)
        for source in outcome.sources:
            if source not in trace.sources_used:
                trace.sources_used.append(source)
        return outcome

    # -------------------------------------------------------------- finalise
    def _finalise(
        self, completion: Completion, trace: Trace, tool_outputs: list[str]
    ) -> TurnResult:
        """Release the answer only if its figures are grounded (R12)."""
        answer = completion.text.strip()
        missing = ungrounded_figures(answer, tool_outputs)
        if missing and tool_outputs:
            # The offending figures stay on the trace and out of the reply. A
            # message that quotes them back — "I cannot support 999,999.42" —
            # still puts an invented number in front of the reader, and a
            # skimming reader takes the only number on screen for the answer.
            plural = "a figure" if len(missing) == 1 else f"{len(missing)} figures"
            warning = (
                f"I drafted an answer containing {plural} I cannot trace to any tool "
                "result, so I am not presenting it. Every number this platform reports "
                "has to come from a governed metric query, and that draft did not. "
                "Ask again and I will run the metric, or open the trace to see what "
                "was discarded."
            )
            trace.add(
                StepKind.GUARDRAIL_BLOCK,
                "ungrounded figures blocked",
                detail={"figures": missing, "draft": answer[:500]},
            )
            trace.refusal_reason = warning
            trace.finish(warning)
            return TurnResult(
                answer=warning,
                trace=trace,
                grounded=False,
                refused=True,
                tool_outputs=list(tool_outputs),
            )

        trace.add(StepKind.ASSISTANT_MESSAGE, answer[:400], usage=completion.usage)
        trace.finish(answer)
        return TurnResult(
            answer=answer,
            trace=trace,
            grounded=trace.grounded,
            tool_outputs=list(tool_outputs),
        )

    # --------------------------------------------------- deterministic path
    def _run_deterministic(self, agent: AgentDefinition, question: str, trace: Trace) -> TurnResult:
        """No LLM: route to a tool, run it, report the result plainly (§19)."""
        from snowobs_agents.runtime.routing import comparison_windows, route

        available = {name: tool for name, tool in self.registry.items() if name in agent.tool_names}

        # Not every question is a metric lookup. "Which sources am I missing"
        # and "what can you tell me about cost.idle_credits" have exact,
        # non-narrative answers that a no-LLM deployment should still give,
        # rather than forcing them through metric matching and answering a
        # different question from the one asked.
        direct = self._deterministic_intent(question)
        if direct is not None:
            outcome = self._invoke(direct, available, trace)
            if not outcome.is_error:
                answer = f"{outcome.content}\n\n{_NO_NARRATIVE_NOTE}"
                trace.add(StepKind.ASSISTANT_MESSAGE, answer[:400])
                trace.finish(answer)
                return TurnResult(
                    answer=answer, trace=trace, grounded=True, tool_outputs=[outcome.content]
                )

        routed = route(question, self.context.model)
        if routed is None:
            answer = (
                "No LLM provider is configured, so I answer by matching your question "
                "to the governed metric catalogue — and nothing matched this one. "
                "Try naming a metric directly (for example 'billed credits' or "
                "'idle credits by warehouse'), or open the metric catalogue."
            )
            trace.add(StepKind.REFUSAL, "no metric matched")
            trace.refusal_reason = "no metric matched the question"
            trace.finish(answer)
            return TurnResult(answer=answer, trace=trace, refused=True)

        # "Why did spend go up" is a different question from "what was spend",
        # and answering it with a total is answering the wrong one. When the
        # question asks why, decompose the change across a dimension — the
        # analytics engine computes the contributions, deterministically, with
        # no model involved (R12).
        windows = comparison_windows(question)
        if windows is not None and "explain_delta" in available:
            delta = self._invoke(
                ToolCall(
                    id="deterministic-delta",
                    name="explain_delta",
                    arguments={
                        "metric": routed.metric_id,
                        "dimension": routed.dimensions[0] if routed.dimensions else "warehouse",
                        "period_a_start": windows.period_a_start.isoformat(),
                        "period_a_end": windows.period_a_end.isoformat(),
                        "period_b_start": windows.period_b_start.isoformat(),
                        "period_b_end": windows.period_b_end.isoformat(),
                    },
                ),
                available,
                trace,
            )
            if not delta.is_error:
                answer = (
                    f"Change in {routed.metric_name}, comparing {windows.basis}:\n\n"
                    f"{delta.content}\n\n"
                    f"Sources: {', '.join(delta.sources)}. No figure here is fresher than "
                    f"{delta.latency_floor_minutes} minutes.\n\n{_NO_NARRATIVE_NOTE}"
                )
                trace.add(StepKind.ASSISTANT_MESSAGE, answer[:400])
                trace.finish(answer)
                return TurnResult(
                    answer=answer, trace=trace, grounded=True, tool_outputs=[delta.content]
                )

        call = ToolCall(
            id="deterministic-1",
            name="query_metric",
            arguments={
                "metrics": [routed.metric_id],
                "dimensions": routed.dimensions,
                "last_days": routed.last_days,
                "by_time": False,
            },
        )
        outcome = self._invoke(call, available, trace)

        if outcome.is_error:
            trace.finish(outcome.content)
            return TurnResult(
                answer=outcome.content, trace=trace, refused=True, tool_outputs=[outcome.content]
            )

        answer = (
            f"{routed.metric_name}, over the window reported below:\n\n"
            f"{outcome.content}\n\n"
            f"Sources: {', '.join(outcome.sources)}. No figure here is fresher than "
            f"{outcome.latency_floor_minutes} minutes."
            + (" These figures are provisional and may restate." if outcome.provisional else "")
            + f"\n\n{_NO_NARRATIVE_NOTE}"
        )
        trace.add(StepKind.ASSISTANT_MESSAGE, answer[:400])
        trace.finish(answer)
        return TurnResult(answer=answer, trace=trace, grounded=True, tool_outputs=[outcome.content])

    @staticmethod
    def _deterministic_intent(question: str) -> ToolCall | None:
        """Match a question to a non-metric tool, or None to fall through.

        Only the tools whose arguments can be derived from the question with
        certainty are routed here. ``explain_delta`` is deliberately absent:
        it needs two explicit periods, and inventing a comparison window the
        user did not ask for would answer a question they did not pose.
        """
        lowered = question.lower()

        if any(phrase in lowered for phrase in _COVERAGE_PHRASES):
            return ToolCall(id="deterministic-coverage", name="get_coverage", arguments={})

        if any(phrase in lowered for phrase in _CATALOGUE_PHRASES):
            return ToolCall(id="deterministic-catalogue", name="list_metrics", arguments={})

        # "tell me about cost.idle_credits" — an exact metric id is unambiguous.
        for token in re.findall(r"[a-z][a-z_]*\.[a-z][a-z_]*", lowered):
            if any(phrase in lowered for phrase in _DESCRIBE_PHRASES):
                return ToolCall(
                    id="deterministic-describe",
                    name="describe_metric",
                    arguments={"metric_id": token},
                )
        return None

    # ---------------------------------------------------------------- stream
    def stream(self, agent: AgentDefinition, question: str) -> Iterator[dict[str, Any]]:
        """Stream a turn as SSE-ready events, tool steps surfaced as they happen."""
        result = self.run(agent, question)
        for step in result.trace.steps:
            yield {
                "event": step.kind.value,
                "summary": step.summary,
                "detail": step.detail,
            }
        yield {
            "event": "answer",
            "answer": result.answer,
            "agent": agent.name,
            "trace_id": result.trace.id,
            "grounded": result.grounded,
            # A consumer must not have to infer a refusal from `grounded`: a
            # refusal and an answer that merely used no tools are different
            # things, and only one of them should be shown as a finding.
            "refused": result.refused,
            "refusal_reason": result.trace.refusal_reason,
            "metrics": result.trace.metrics_used,
            "sources": result.trace.sources_used,
            # R5 has to survive streaming. Without the SQL on the final frame a
            # streaming client would have to re-ask the non-streaming endpoint
            # to show its work — running every query a second time.
            "sql": result.sql_shown,
        }


@dataclass
class Supervisor:
    """Routes a question to a specialist, then runs it (§12.2)."""

    runtime: AgentRuntime
    agents: dict[str, AgentDefinition] = field(default_factory=dict)
    default_agent: str = "finops"

    def route(self, question: str) -> AgentDefinition:
        """Pick a specialist by intent. Deterministic, so routing is testable."""
        lowered = question.lower()
        scores: dict[str, int] = {}
        for name in self.agents:
            scores[name] = sum(
                _WEAK_KEYWORD_WEIGHT if keyword in _OBJECT_NOUNS else _INTENT_KEYWORD_WEIGHT
                for keyword in _ROUTING_KEYWORDS.get(name, ())
                if keyword in lowered
            )
        # Sorted by name on a tie so routing never depends on dict ordering.
        best = max(sorted(scores), key=lambda name: scores[name]) if scores else self.default_agent
        if scores.get(best, 0) == 0:
            best = self.default_agent
        return self.agents[best]

    def ask(self, question: str) -> TurnResult:
        return self.runtime.run(self.route(question), question)


#: Words that name a *thing* rather than an intent. Every domain talks about
#: warehouses, tasks, and tags, so a bare mention of one says almost nothing
#: about who should answer: "which team should be charged for the ETL warehouse"
#: is a chargeback question that happens to name a warehouse. Counting these
#: equally with intent words routed it to the SRE agent.
_OBJECT_NOUNS = frozenset(
    {"warehouse", "task", "tag", "role", "column", "source", "version", "queue"}
)
_INTENT_KEYWORD_WEIGHT = 3
_WEAK_KEYWORD_WEIGHT = 1

#: Intent keywords per specialist. Kept explicit and testable rather than
#: delegated to the model, so routing behaviour is reviewable.
_ROUTING_KEYWORDS: dict[str, tuple[str, ...]] = {
    "finops": (
        "cost",
        "spend",
        "credit",
        "budget",
        "chargeback",
        "allocat",
        "forecast",
        "bill",
        "dollar",
        "expensive",
        "cheap",
        "invoice",
        "commitment",
        # "who should be charged for X" is the canonical chargeback question and
        # matched none of the above.
        "charged",
        "charge back",
        "showback",
        "cross-charge",
    ),
    "sre": (
        "fail",
        "error",
        "pipeline",
        "task",
        "freshness",
        "latency",
        "slow",
        "queue",
        "warehouse",
        "spill",
        "performance",
        "incident",
        "lag",
        "refresh",
    ),
    "governance": (
        "access",
        "grant",
        "role",
        "permission",
        "privilege",
        "login",
        "dormant",
        "security",
        "policy",
        "masking",
        "tag",
        "who read",
        "audit",
        # People ask about privilege by naming the role, not the concept:
        # "who still has ACCOUNTADMIN" contains none of the words above.
        "accountadmin",
        "securityadmin",
        "sysadmin",
        "orgadmin",
        "admin",
    ),
    "curator": (
        "data product",
        "publish",
        "contract",
        "semantic",
        "listing",
        "catalogue",
        "catalog",
        "version",
        "sla",
    ),
    "onboarding": (
        "upload",
        "extract",
        "source",
        "coverage",
        "missing",
        "mapping",
        "column",
        "ingest",
        "connect",
    ),
    "optimisation": (
        "optimis",
        "optimiz",
        "recommend",
        "save",
        "saving",
        "right-size",
        "rightsize",
        "resize",
        "auto-suspend",
        "autosuspend",
        "waste",
        "idle",
    ),
}
