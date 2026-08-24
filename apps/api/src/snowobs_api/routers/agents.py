"""The agent console API (BUILD_PROMPT §12, §14).

Every answer is returned with its trace: which agent ran, which tools it called,
which metrics and sources it used, and the SQL behind each figure. That is R5
applied to the agent surface — "show the SQL" has to work for an answer a model
narrated exactly as it does for a dashboard tile.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from snowobs_agents.runtime.trace import Trace
from snowobs_api.deps import SettingsDep
from snowobs_api.services.agents import AgentService, get_trace, recent_traces
from snowobs_common.errors import AppError

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


class AgentInfo(BaseModel):
    name: str
    description: str
    tools: list[str]


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)
    #: Omit to let the supervisor route by intent.
    agent: str | None = None


class TraceStepResponse(BaseModel):
    kind: str
    summary: str
    elapsed_ms: float | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class AskResponse(BaseModel):
    answer: str
    agent: str
    #: True when the answer rests on at least one tool result (R12).
    grounded: bool
    refused: bool
    refusal_reason: str | None = None
    metrics_used: list[str]
    sources_used: list[str]
    #: R5: the statements behind the figures, in the order they ran.
    sql: list[str]
    trace_id: str
    steps: list[TraceStepResponse]
    input_tokens: int
    output_tokens: int


@router.get("/catalog", response_model=list[AgentInfo])
async def agent_catalog(settings: SettingsDep) -> list[AgentInfo]:
    """The specialists available, and the tools each may reach for (§12.2)."""
    return [
        AgentInfo(
            name=definition.name,
            description=definition.description,
            tools=list(definition.tool_names),
        )
        for definition in AgentService(settings).catalogue()
    ]


@router.post("/ask", response_model=AskResponse)
async def ask(payload: AskRequest, settings: SettingsDep) -> AskResponse:
    """Ask one question and get the answer with its full trace."""
    try:
        result = AgentService(settings).ask(payload.question, payload.agent)
    except AppError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    trace = result.trace
    return AskResponse(
        answer=result.answer,
        agent=trace.agent,
        grounded=result.grounded,
        refused=result.refused,
        refusal_reason=trace.refusal_reason,
        metrics_used=trace.metrics_used,
        sources_used=trace.sources_used,
        sql=result.sql_shown,
        trace_id=trace.id,
        steps=[
            TraceStepResponse(
                kind=step.kind.value,
                summary=step.summary,
                elapsed_ms=step.elapsed_ms,
                detail=step.detail,
            )
            for step in trace.steps
        ],
        input_tokens=trace.usage.input_tokens,
        output_tokens=trace.usage.output_tokens,
    )


class TraceSummary(BaseModel):
    trace_id: str
    agent: str
    actor: str
    question: str
    started_at: str
    answer: str
    refusal_reason: str | None = None
    metrics_used: list[str]
    sources_used: list[str]


class TraceListResponse(BaseModel):
    traces: list[TraceSummary]
    #: False while traces live only in this process's memory. A caller must not
    #: read an absent trace as evidence that the question was never asked.
    durable: bool
    retention_note: str


@router.get("/traces", response_model=TraceListResponse)
async def list_traces(settings: SettingsDep, limit: int = 20) -> TraceListResponse:
    """Recent agent turns, for reviewing what was asked and what was answered."""
    del settings
    return TraceListResponse(
        traces=[_summarise(trace) for trace in recent_traces(min(max(limit, 1), 100))],
        durable=False,
        retention_note=(
            "Traces are held in memory by the API process that served the turn and are "
            "lost on restart. They are a debugging aid, not the audit log; durable "
            "retention is not yet implemented (see docs/ASSUMPTIONS.md)."
        ),
    )


@router.get("/traces/{trace_id}", response_model=AskResponse)
async def read_trace(trace_id: str, settings: SettingsDep) -> AskResponse:
    """One recorded turn in full — every step, and the SQL behind each figure."""
    del settings
    trace = get_trace(trace_id)
    if trace is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No trace {trace_id} in this process's buffer. Traces are not persisted, "
                "so this does not mean the turn never happened."
            ),
        )
    return AskResponse(
        answer=trace.answer,
        agent=trace.agent,
        grounded=bool(trace.metrics_used),
        refused=trace.refusal_reason is not None,
        refusal_reason=trace.refusal_reason,
        metrics_used=trace.metrics_used,
        sources_used=trace.sources_used,
        sql=[str(step.detail["sql"]) for step in trace.steps if step.detail.get("sql")],
        trace_id=trace.id,
        steps=[
            TraceStepResponse(
                kind=step.kind.value,
                summary=step.summary,
                elapsed_ms=step.elapsed_ms,
                detail=step.detail,
            )
            for step in trace.steps
        ],
        input_tokens=trace.usage.input_tokens,
        output_tokens=trace.usage.output_tokens,
    )


def _summarise(trace: Trace) -> TraceSummary:
    return TraceSummary(
        trace_id=trace.id,
        agent=trace.agent,
        actor=trace.actor,
        question=trace.question,
        started_at=trace.started_at.isoformat(),
        answer=trace.answer,
        refusal_reason=trace.refusal_reason,
        metrics_used=trace.metrics_used,
        sources_used=trace.sources_used,
    )


@router.post("/stream")
async def stream(payload: AskRequest, settings: SettingsDep) -> StreamingResponse:
    """The same turn as Server-Sent Events, so tool steps appear as they happen.

    A turn can run several queries; watching the tool calls arrive is the
    difference between a console that feels stalled and one that shows its work.
    """

    def events() -> Any:
        for event in AgentService(settings).stream(payload.question, payload.agent):
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Proxies that buffer would defeat the point of streaming at all.
            "X-Accel-Buffering": "no",
        },
    )
