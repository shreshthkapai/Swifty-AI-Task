"""Provider-neutral contracts for one bounded conversational model turn."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Mapping

from webchat.domain.common import require_non_empty

from .contracts import (
    FrozenObject,
    freeze_json_object,
    thaw_json_object,
    validate_frozen_json_object,
)
from .tools import SemanticCommandSpec


CONVERSATION_REQUEST_SCHEMA_VERSION = 1
MAX_TOOL_CALLS = 4
MAX_CONTINUATION_CHARS = 262_144


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
    continuation: str | None = None
    prior: ToolExchange | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.calls, tuple) or not self.calls:
            raise ValueError("calls must contain at least one ConversationToolCall")
        if not isinstance(self.results, tuple):
            raise ValueError("results must be a tuple")
        if self.prior is not None and not isinstance(self.prior, ToolExchange):
            raise ValueError("prior must be a ToolExchange or None")


@dataclass(frozen=True, slots=True)
class ConversationRequest:
    context: str
    tools: tuple[SemanticCommandSpec, ...]
    exchange: ToolExchange | None = None
    force_text: bool = False
    schema_version: int = CONVERSATION_REQUEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.context, str) or not self.context:
            raise ValueError("context must be non-empty JSON")
        if not isinstance(self.tools, tuple) or not self.tools:
            raise ValueError("tools must contain SemanticCommandSpec values")

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
    continuation: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.usage, ConversationUsage):
            raise ValueError("usage must be ConversationUsage")
        object.__setattr__(self, "provider", require_non_empty(self.provider, "provider"))
        object.__setattr__(self, "model", require_non_empty(self.model, "model"))
        if self.text is None and not self.tool_calls:
            raise ValueError("result must contain either text or tool calls")
