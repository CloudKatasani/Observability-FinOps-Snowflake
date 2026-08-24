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

from snowobs_api.deps import SettingsDep
from snowobs_api.services.agents import AgentService
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
