"""Concrete LLM providers and the adapter factory (R11).

Model identifiers are verified as of 2026-08-24 (docs/ASSUMPTIONS.md §9) and
live in configuration, not in code: ``LLM__MODEL_STRONG`` / ``LLM__MODEL_FAST``.
The defaults here are the fallback when configuration says nothing.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from decimal import Decimal
from typing import Any

from snowobs_common.config import LLMSettings
from snowobs_common.logging import get_logger
from snowobs_llm.base import (
    Completion,
    LLMError,
    LLMProvider,
    Message,
    Role,
    StopReason,
    StreamEvent,
    ToolCall,
    ToolSpec,
    Usage,
)

logger = get_logger(__name__)

#: Verified 2026-08-24. Since the 4.6 generation, first-party ids are dateless
#: pinned snapshots. Opus 5 is the flagship for agentic/enterprise work.
DEFAULT_MODEL_STRONG = "claude-opus-5"
DEFAULT_MODEL_FAST = "claude-haiku-4-5"
#: Bedrock's newer Messages-API path uses the same ids with an `anthropic.` prefix.
BEDROCK_PREFIX = "anthropic."


class DeterministicProvider(LLMProvider):
    """No API key configured: answer from tools, and say narration is off (§19).

    This is not a stub. It is a supported operating mode: the agent still routes
    a question to a governed metric and reports the tool's answer. What it does
    not do is write prose about it, and it says so rather than pretending.
    """

    generates_narrative = False

    def __init__(self, model: str = "deterministic") -> None:
        self._model = model

    @property
    def name(self) -> str:
        return "none"

    @property
    def model(self) -> str:
        return self._model

    def complete(
        self,
        system: str,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion:
        del system, tools, max_tokens, temperature
        # The runtime handles routing and tool execution; this provider only
        # reports that it will not add narrative on top of the tool results.
        last = messages[-1] if messages else None
        answered = bool(last and last.tool_results)
        text = (
            "Narrative generation is disabled (no LLM provider is configured). "
            "The figures above come directly from the governed metric layer."
            if answered
            else "No LLM provider is configured, so I can answer metric questions "
            "from the catalogue but cannot discuss them."
        )
        return Completion(text=text, stop_reason=StopReason.END_TURN, model=self._model)


class AnthropicProvider(LLMProvider):
    """The Anthropic Messages API."""

    def __init__(self, model: str, api_key: str | None = None) -> None:
        self._model = model
        self._api_key = api_key
        self._client: Any | None = None

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def model(self) -> str:
        return self._model

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - dependency guard
                raise LLMError(
                    "The anthropic package is not installed. Install the "
                    "'anthropic' extra, or set LLM__PROVIDER=none."
                ) from exc
            self._client = (
                anthropic.Anthropic(api_key=self._api_key)
                if self._api_key
                else anthropic.Anthropic()
            )
        return self._client

    def complete(
        self,
        system: str,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion:
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [_to_anthropic(message) for message in messages],
        }
        if tools:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in tools
            ]

        try:
            response = self._get_client().messages.create(**payload)
        except Exception as exc:
            raise LLMError(f"Anthropic request failed: {type(exc).__name__}") from exc

        return _from_anthropic(response, self._model)

    def stream(
        self,
        system: str,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Iterator[StreamEvent]:
        """Stream text as it arrives; tool calls are emitted when complete."""
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [_to_anthropic(message) for message in messages],
        }
        if tools:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in tools
            ]

        try:
            with self._get_client().messages.stream(**payload) as stream:
                for text in stream.text_stream:
                    yield StreamEvent(kind="text", text=text)
                final = stream.get_final_message()
        except Exception as exc:
            raise LLMError(f"Anthropic stream failed: {type(exc).__name__}") from exc

        completion = _from_anthropic(final, self._model)
        for call in completion.tool_calls:
            yield StreamEvent(kind="tool_call", tool_call=call)
        yield StreamEvent(kind="done", usage=completion.usage)


class BedrockProvider(LLMProvider):
    """Claude on Amazon Bedrock, for clients who require it."""

    def __init__(self, model: str, region: str | None = None) -> None:
        self._model = model if model.startswith(BEDROCK_PREFIX) else BEDROCK_PREFIX + model
        self._region = region
        self._client: Any | None = None

    @property
    def name(self) -> str:
        return "bedrock"

    @property
    def model(self) -> str:
        return self._model

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - dependency guard
                raise LLMError(
                    "boto3 is not installed. Install the 'bedrock' extra, or set "
                    "LLM__PROVIDER=none."
                ) from exc
            self._client = boto3.client("bedrock-runtime", region_name=self._region)
        return self._client

    def complete(
        self,
        system: str,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion:
        import json

        body: dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [_to_anthropic(message) for message in messages],
        }
        if tools:
            body["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in tools
            ]

        try:
            response = self._get_client().invoke_model(modelId=self._model, body=json.dumps(body))
            payload = json.loads(response["body"].read())
        except Exception as exc:
            raise LLMError(f"Bedrock request failed: {type(exc).__name__}") from exc

        return _from_bedrock(payload, self._model)


class CortexProvider(LLMProvider):
    """Snowflake Cortex, LIVE mode only — the model runs in the customer's account.

    This is the option for clients whose data may not leave Snowflake at all:
    the request goes through their existing connection, with no external egress.
    """

    def __init__(self, model: str, connector: Any) -> None:
        self._model = model
        self._connector = connector

    @property
    def name(self) -> str:
        return "cortex"

    @property
    def model(self) -> str:
        return self._model

    def complete(
        self,
        system: str,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion:
        if tools:
            # Cortex COMPLETE has no tool-use protocol, so the runtime must run
            # the deterministic routing path and use Cortex only for narration.
            raise LLMError(
                "The Cortex provider does not support tool use. The agent runtime "
                "routes tools deterministically and uses Cortex for narration only."
            )
        del max_tokens

        prompt = _flatten(system, messages)
        connection = self._connector.connect(surface="agent")
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "SELECT SNOWFLAKE.CORTEX.COMPLETE(%s, %s, %s)",
                    (self._model, prompt, {"temperature": temperature}),
                )
                row = cursor.fetchone()
            finally:
                cursor.close()
        except Exception as exc:
            raise LLMError(f"Cortex request failed: {type(exc).__name__}") from exc
        finally:
            connection.close()

        text = str(row[0]) if row and row[0] else ""
        return Completion(text=text, stop_reason=StopReason.END_TURN, model=self._model)


# ------------------------------------------------------------------ mapping
def _to_anthropic(message: Message) -> dict[str, Any]:
    """Convert one message to the provider wire format."""
    if message.tool_results:
        return {
            "role": Role.USER.value,
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": result.call_id,
                    "content": result.content,
                    "is_error": result.is_error,
                }
                for result in message.tool_results
            ],
        }
    if message.tool_calls:
        content: list[dict[str, Any]] = []
        if message.content:
            content.append({"type": "text", "text": message.content})
        content.extend(
            {
                "type": "tool_use",
                "id": call.id,
                "name": call.name,
                "input": call.arguments,
            }
            for call in message.tool_calls
        )
        return {"role": message.role.value, "content": content}
    return {"role": message.role.value, "content": message.content}


def _from_anthropic(response: Any, model: str) -> Completion:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in getattr(response, "content", []) or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text_parts.append(getattr(block, "text", ""))
        elif block_type == "tool_use":
            tool_calls.append(
                ToolCall(
                    id=str(getattr(block, "id", "")),
                    name=str(getattr(block, "name", "")),
                    arguments=dict(getattr(block, "input", {}) or {}),
                )
            )

    raw_usage = getattr(response, "usage", None)
    usage = Usage(
        input_tokens=int(getattr(raw_usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(raw_usage, "output_tokens", 0) or 0),
    )
    stop = str(getattr(response, "stop_reason", "") or "")
    return Completion(
        text="".join(text_parts),
        tool_calls=tool_calls,
        stop_reason=StopReason.TOOL_USE if stop == "tool_use" else StopReason.END_TURN,
        usage=usage,
        model=model,
    )


def _from_bedrock(payload: dict[str, Any], model: str) -> Completion:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in payload.get("content", []):
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            tool_calls.append(
                ToolCall(
                    id=str(block.get("id", "")),
                    name=str(block.get("name", "")),
                    arguments=dict(block.get("input", {})),
                )
            )
    raw_usage = payload.get("usage", {})
    return Completion(
        text="".join(text_parts),
        tool_calls=tool_calls,
        stop_reason=(
            StopReason.TOOL_USE if payload.get("stop_reason") == "tool_use" else StopReason.END_TURN
        ),
        usage=Usage(
            input_tokens=int(raw_usage.get("input_tokens", 0)),
            output_tokens=int(raw_usage.get("output_tokens", 0)),
        ),
        model=model,
    )


def _flatten(system: str, messages: Sequence[Message]) -> str:
    """Flatten a conversation for providers with no message protocol."""
    parts = [system, ""]
    for message in messages:
        if message.content:
            parts.append(f"{message.role.value.upper()}: {message.content}")
        for result in message.tool_results:
            parts.append(f"TOOL RESULT: {result.content}")
    parts.append("ASSISTANT:")
    return "\n".join(parts)


def build_provider(
    settings: LLMSettings,
    *,
    api_key: str | None = None,
    connector: Any = None,
    fast: bool = False,
) -> LLMProvider:
    """Build the configured provider. Never raises for a missing key.

    A missing key degrades to the deterministic path rather than breaking the
    product: the demo must run with no credentials at all (§19).
    """
    model = (settings.model_fast if fast else settings.model_strong) or (
        DEFAULT_MODEL_FAST if fast else DEFAULT_MODEL_STRONG
    )

    match settings.provider:
        case "anthropic":
            return AnthropicProvider(model=model, api_key=api_key)
        case "bedrock":
            return BedrockProvider(model=model)
        case "cortex":
            if connector is None:
                logger.warning(
                    "cortex_provider_requires_connection",
                    detail="falling back to deterministic mode",
                )
                return DeterministicProvider()
            return CortexProvider(model=model, connector=connector)
        case _:
            return DeterministicProvider()


def estimate_cost(usage: Usage, *, input_per_mtok: Decimal, output_per_mtok: Decimal) -> Decimal:
    """Estimate spend from token counts, for the per-turn and daily caps."""
    million = Decimal(1_000_000)
    return (
        Decimal(usage.input_tokens) / million * input_per_mtok
        + Decimal(usage.output_tokens) / million * output_per_mtok
    ).quantize(Decimal("0.000001"))
