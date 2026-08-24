"""LLM provider adapter (BUILD_PROMPT §6, R11).

Clients insist on different providers — Anthropic direct, Bedrock, or Cortex
inside their own account — and the application code must not care which. The
adapter is deliberately narrow: messages in, a message or tool-use request out,
plus token accounting. Anything richer would leak provider semantics upward.

There is also a **deterministic mode** (``provider = none``): with no API key
configured the platform still answers metric questions by keyword→metric
routing, and says plainly that narrative generation is disabled. The demo can
never hard-depend on an API key (§19).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from snowobs_common.errors import AppError


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class StopReason(StrEnum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    BUDGET_EXHAUSTED = "budget_exhausted"


class LLMError(AppError):
    status_code = 502
    title = "LLM provider error"
    problem_type = "https://snowobs.dev/problems/llm"


class BudgetExceededError(AppError):
    """The turn, user, or tenant budget is spent (§12.1)."""

    status_code = 429
    title = "LLM budget exhausted"
    problem_type = "https://snowobs.dev/problems/llm-budget"


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may call. Mirrors the Anthropic tool schema."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    #: Provider-reported cost when available; otherwise estimated by the caller.
    cost_usd: Decimal | None = None

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cost_usd=(
                (self.cost_usd or Decimal(0)) + (other.cost_usd or Decimal(0))
                if (self.cost_usd is not None or other.cost_usd is not None)
                else None
            ),
        )

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class Completion:
    """One model turn: text, any tool calls it wants, and what it cost."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: StopReason = StopReason.END_TURN
    usage: Usage = field(default_factory=Usage)
    model: str = ""

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@dataclass(frozen=True)
class StreamEvent:
    """A streamed chunk. ``kind`` is one of text, tool_call, done."""

    kind: str
    text: str = ""
    tool_call: ToolCall | None = None
    usage: Usage | None = None


class LLMProvider(ABC):
    """What every provider implements. Application code sees only this."""

    #: True when the provider can generate prose. False for deterministic mode.
    generates_narrative: bool = True

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def model(self) -> str: ...

    @abstractmethod
    def complete(
        self,
        system: str,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion:
        """One turn. Deterministic by default — temperature 0."""

    def stream(
        self,
        system: str,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Iterator[StreamEvent]:
        """Stream a turn. The default emits the completed turn as one event.

        Providers that support streaming override this; the SSE endpoint works
        either way, which is what lets the deterministic path share the same UI.
        """
        completion = self.complete(
            system, messages, tools=tools, max_tokens=max_tokens, temperature=temperature
        )
        if completion.text:
            yield StreamEvent(kind="text", text=completion.text)
        for call in completion.tool_calls:
            yield StreamEvent(kind="tool_call", tool_call=call)
        yield StreamEvent(kind="done", usage=completion.usage)
