"""Per-turn traces (BUILD_PROMPT §12.1).

Enterprise review requires every step to be inspectable and replayable, so a
trace is a first-class persisted artifact rather than a debug log: messages,
tool calls, tool results, latency, tokens, and cost. A trace can be replayed
deterministically, which is what makes an agent bug reproducible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import uuid4

from snowobs_llm.base import Usage


class StepKind(StrEnum):
    USER_MESSAGE = "user_message"
    MODEL_THINKING = "model_thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_ERROR = "tool_error"
    ASSISTANT_MESSAGE = "assistant_message"
    REFUSAL = "refusal"
    BUDGET_STOP = "budget_stop"
    GUARDRAIL_BLOCK = "guardrail_block"


@dataclass
class TraceStep:
    """One step. ``detail`` is JSON-serialisable so the trace can be replayed."""

    kind: StepKind
    at: datetime
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: float = 0.0
    usage: Usage | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "at": self.at.isoformat(),
            "summary": self.summary,
            "detail": self.detail,
            "elapsed_ms": self.elapsed_ms,
            "tokens": (
                {
                    "input": self.usage.input_tokens,
                    "output": self.usage.output_tokens,
                }
                if self.usage
                else None
            ),
        }


@dataclass
class Trace:
    """The full record of one agent turn."""

    id: str = field(default_factory=lambda: uuid4().hex)
    tenant: str = "default"
    actor: str = "anonymous"
    agent: str = "supervisor"
    question: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    finished_at: datetime | None = None
    steps: list[TraceStep] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    cost_usd: Decimal | None = None
    #: Metrics the answer is grounded in — the audit trail for every figure (R5).
    metrics_used: list[str] = field(default_factory=list)
    sources_used: list[str] = field(default_factory=list)
    answer: str = ""
    #: Set when the agent declined; the reason is user-visible.
    refusal_reason: str | None = None

    def add(
        self,
        kind: StepKind,
        summary: str,
        *,
        detail: dict[str, Any] | None = None,
        elapsed_ms: float = 0.0,
        usage: Usage | None = None,
    ) -> TraceStep:
        step = TraceStep(
            kind=kind,
            at=datetime.now(tz=UTC),
            summary=summary,
            detail=detail or {},
            elapsed_ms=elapsed_ms,
            usage=usage,
        )
        self.steps.append(step)
        if usage is not None:
            self.usage = self.usage + usage
        return step

    def finish(self, answer: str) -> None:
        self.answer = answer
        self.finished_at = datetime.now(tz=UTC)

    @property
    def duration_ms(self) -> float:
        end = self.finished_at or datetime.now(tz=UTC)
        return (end - self.started_at).total_seconds() * 1000

    @property
    def tool_calls(self) -> list[TraceStep]:
        return [step for step in self.steps if step.kind is StepKind.TOOL_CALL]

    @property
    def tools_used(self) -> list[str]:
        return [str(step.detail.get("tool", "")) for step in self.tool_calls]

    @property
    def grounded(self) -> bool:
        """An answer is grounded when at least one tool produced a result.

        The runtime uses this to enforce R12: an ungrounded numeric claim is
        never presented as an answer.
        """
        return any(step.kind is StepKind.TOOL_RESULT for step in self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant": self.tenant,
            "actor": self.actor,
            "agent": self.agent,
            "question": self.question,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_ms": round(self.duration_ms, 1),
            "steps": [step.to_dict() for step in self.steps],
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
            },
            "cost_usd": str(self.cost_usd) if self.cost_usd is not None else None,
            "metrics_used": self.metrics_used,
            "sources_used": self.sources_used,
            "answer": self.answer,
            "refusal_reason": self.refusal_reason,
            "grounded": self.grounded,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)


@dataclass
class TraceStore:
    """In-process trace storage. Persisted to Postgres by the API layer."""

    traces: dict[str, Trace] = field(default_factory=dict)
    max_traces: int = 1000

    def save(self, trace: Trace) -> None:
        self.traces[trace.id] = trace
        while len(self.traces) > self.max_traces:
            oldest = min(self.traces.values(), key=lambda t: t.started_at)
            del self.traces[oldest.id]

    def get(self, trace_id: str) -> Trace | None:
        return self.traces.get(trace_id)

    def recent(self, limit: int = 20) -> list[Trace]:
        return sorted(self.traces.values(), key=lambda t: t.started_at, reverse=True)[:limit]
