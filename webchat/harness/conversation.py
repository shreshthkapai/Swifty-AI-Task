"""Provider-neutral contracts for one bounded conversational model turn."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from typing import Any, Mapping

from webchat.domain.common import require_non_empty

from .contracts import (
    FrozenObject,
    freeze_json_object,
    thaw_json_object,
    validate_frozen_json_object,
)
from .tool_gate import SemanticCommandSpec


CONVERSATION_REQUEST_SCHEMA_VERSION = 1
MAX_TOOL_CALLS = 4


def _frozen_object(value: FrozenObject | Mapping[str, Any], field_name: str) -> FrozenObject:
    if isinstance(value, Mapping):
        return freeze_json_object(value, field=field_name)
    return validate_frozen_json_object(value, field=field_name)


@dataclass(frozen=True, slots=True)
class ConversationUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int

    def __post_init__(self) -> None:
        for field_name in ("input_tokens", "output_tokens", "total_tokens"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.total_tokens < self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens cannot be less than input plus output tokens")


@dataclass(frozen=True, slots=True)
class ConversationToolCall:
    call_id: str
    name: str
    arguments: FrozenObject | Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "call_id", require_non_empty(self.call_id, "call_id"))
        object.__setattr__(self, "name", require_non_empty(self.name, "name"))
        object.__setattr__(
            self,
            "arguments",
            _frozen_object(self.arguments, "tool call arguments"),
        )

    def arguments_dict(self) -> dict[str, Any]:
        return thaw_json_object(self.arguments)


@dataclass(frozen=True, slots=True)
class ConversationToolResult:
    call_id: str
    name: str
    output: FrozenObject | Mapping[str, Any] = field(default_factory=dict)
    is_error: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "call_id", require_non_empty(self.call_id, "call_id"))
        object.__setattr__(self, "name", require_non_empty(self.name, "name"))
        object.__setattr__(self, "output", _frozen_object(self.output, "tool result output"))
        if not isinstance(self.is_error, bool):
            raise ValueError("is_error must be boolean")

    def output_dict(self) -> dict[str, Any]:
        return thaw_json_object(self.output)


@dataclass(frozen=True, slots=True)
class ToolExchange:
    calls: tuple[ConversationToolCall, ...]
    results: tuple[ConversationToolResult, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.calls, tuple) or not self.calls:
            raise ValueError("calls must contain at least one ConversationToolCall")
        if len(self.calls) > MAX_TOOL_CALLS:
            raise ValueError(f"calls cannot exceed {MAX_TOOL_CALLS}")
        if not all(isinstance(item, ConversationToolCall) for item in self.calls):
            raise ValueError("calls must contain ConversationToolCall values")
        if not isinstance(self.results, tuple) or not all(
            isinstance(item, ConversationToolResult) for item in self.results
        ):
            raise ValueError("results must contain ConversationToolResult values")
        call_pairs = [(item.call_id, item.name) for item in self.calls]
        result_pairs = [(item.call_id, item.name) for item in self.results]
        call_ids = [item.call_id for item in self.calls]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("tool call IDs must be unique")
        if result_pairs != call_pairs:
            raise ValueError("tool results must match calls in order by ID and name")


@dataclass(frozen=True, slots=True)
class ConversationRequest:
    context: str
    tools: tuple[SemanticCommandSpec, ...]
    exchange: ToolExchange | None = None
    schema_version: int = CONVERSATION_REQUEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != CONVERSATION_REQUEST_SCHEMA_VERSION
        ):
            raise ValueError(
                f"unsupported ConversationRequest schema version: {self.schema_version}"
            )
        if not isinstance(self.context, str) or not self.context:
            raise ValueError("context must be non-empty canonical JSON")
        try:
            decoded = json.loads(self.context)
        except json.JSONDecodeError as exc:
            raise ValueError("context must be non-empty canonical JSON") from exc
        if not isinstance(decoded, dict):
            raise ValueError("context must encode a JSON object")
        canonical = json.dumps(
            decoded,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if canonical != self.context:
            raise ValueError("context must be canonical JSON")
        if "provider_response_id" in decoded:
            raise ValueError("context cannot contain provider-owned continuation state")
        current_input = decoded.get("current_input")
        if not isinstance(current_input, str) or not current_input.strip():
            raise ValueError("context must contain a non-empty current_input")
        if not isinstance(self.tools, tuple) or not self.tools or not all(
            isinstance(item, SemanticCommandSpec) for item in self.tools
        ):
            raise ValueError("tools must contain SemanticCommandSpec values")
        names = tuple(item.name for item in self.tools)
        if len(names) != len(set(names)):
            raise ValueError("tools must be unique by name")
        if self.exchange is not None and not isinstance(self.exchange, ToolExchange):
            raise ValueError("exchange must be a ToolExchange or None")

    @property
    def current_input(self) -> str:
        return json.loads(self.context)["current_input"]


@dataclass(frozen=True, slots=True)
class ConversationResult:
    usage: ConversationUsage
    latency_ms: float
    provider: str
    model: str
    text: str | None = None
    tool_calls: tuple[ConversationToolCall, ...] = ()
    time_to_first_token_ms: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.usage, ConversationUsage):
            raise ValueError("usage must be ConversationUsage")
        if (
            isinstance(self.latency_ms, bool)
            or not isinstance(self.latency_ms, (int, float))
            or not math.isfinite(self.latency_ms)
            or self.latency_ms < 0
        ):
            raise ValueError("latency_ms must be a non-negative finite number")
        object.__setattr__(self, "provider", require_non_empty(self.provider, "provider"))
        object.__setattr__(self, "model", require_non_empty(self.model, "model"))
        if self.text is not None:
            object.__setattr__(self, "text", require_non_empty(self.text, "text"))
        if not isinstance(self.tool_calls, tuple) or not all(
            isinstance(item, ConversationToolCall) for item in self.tool_calls
        ):
            raise ValueError("tool_calls must contain ConversationToolCall values")
        if len(self.tool_calls) > MAX_TOOL_CALLS:
            raise ValueError(f"tool_calls cannot exceed {MAX_TOOL_CALLS}")
        call_ids = tuple(item.call_id for item in self.tool_calls)
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("tool call IDs must be unique")
        if (self.text is None) == (not self.tool_calls):
            raise ValueError("result must contain either text or tool calls, but not both")
        if self.time_to_first_token_ms is not None:
            if (
                isinstance(self.time_to_first_token_ms, bool)
                or not isinstance(self.time_to_first_token_ms, (int, float))
                or not math.isfinite(self.time_to_first_token_ms)
                or self.time_to_first_token_ms < 0
                or self.time_to_first_token_ms > self.latency_ms
            ):
                raise ValueError(
                    "time_to_first_token_ms must be between zero and latency_ms"
                )
        elif self.text is not None:
            raise ValueError("text results require time_to_first_token_ms")
