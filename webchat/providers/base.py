"""Provider-neutral request, result, metrics, and failure contracts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
import json
import math
from typing import Any, Protocol, runtime_checkable

from webchat.domain.common import require_non_empty
from webchat.harness.contracts import TurnPlan
from webchat.harness.grounded_response import GroundedClaim, GroundedResponseRequest
from webchat.harness.tool_gate import SemanticCommandSpec
from webchat.harness.conversation import ConversationRequest, ConversationResult


PLANNING_REQUEST_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class PlanningRequest:
    context: str
    commands: tuple[SemanticCommandSpec, ...]
    schema_version: int = PLANNING_REQUEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != PLANNING_REQUEST_SCHEMA_VERSION:
            raise ValueError(f"unsupported PlanningRequest schema version: {self.schema_version}")
        if not isinstance(self.context, str) or not self.context:
            raise ValueError("context must be a non-empty canonical JSON string")
        try:
            decoded = json.loads(self.context)
        except json.JSONDecodeError as exc:
            raise ValueError("context must be a non-empty canonical JSON string") from exc
        canonical = json.dumps(
            decoded,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if canonical != self.context:
            raise ValueError("context must be canonical JSON")
        if not isinstance(self.commands, tuple) or not all(
            isinstance(item, SemanticCommandSpec) for item in self.commands
        ):
            raise ValueError("commands must be a tuple of SemanticCommandSpec values")
        if not self.commands:
            raise ValueError("commands must contain at least one semantic command")
        names = [item.name for item in self.commands]
        if len(names) != len(set(names)):
            raise ValueError("commands must be unique by name")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "context": self.context,
            "commands": [item.to_dict() for item in self.commands],
        }


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.total_tokens < self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens cannot be less than input plus output tokens")


@dataclass(frozen=True, slots=True)
class PlanningResult:
    plan: TurnPlan
    usage: ProviderUsage
    latency_ms: float
    provider: str
    model: str

    def __post_init__(self) -> None:
        if not isinstance(self.plan, TurnPlan):
            raise ValueError("plan must be a TurnPlan")
        if not isinstance(self.usage, ProviderUsage):
            raise ValueError("usage must be ProviderUsage")
        if not isinstance(self.latency_ms, (int, float)) or not math.isfinite(self.latency_ms) or self.latency_ms < 0:
            raise ValueError("latency_ms must be a non-negative finite number")
        object.__setattr__(self, "provider", require_non_empty(self.provider, "provider"))
        object.__setattr__(self, "model", require_non_empty(self.model, "model"))


@dataclass(frozen=True, slots=True)
class GroundedResponseResult:
    claims: tuple[GroundedClaim, ...]
    usage: ProviderUsage
    latency_ms: float
    provider: str
    model: str
    focused_entity_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.claims, tuple) or not self.claims or not all(
            isinstance(item, GroundedClaim) for item in self.claims
        ):
            raise ValueError("claims must contain at least one GroundedClaim")
        if not isinstance(self.usage, ProviderUsage):
            raise ValueError("usage must be ProviderUsage")
        if (
            not isinstance(self.latency_ms, (int, float))
            or not math.isfinite(self.latency_ms)
            or self.latency_ms < 0
        ):
            raise ValueError("latency_ms must be a non-negative finite number")
        object.__setattr__(self, "provider", require_non_empty(self.provider, "provider"))
        object.__setattr__(self, "model", require_non_empty(self.model, "model"))
        if self.focused_entity_id is not None:
            object.__setattr__(
                self,
                "focused_entity_id",
                require_non_empty(self.focused_entity_id, "focused_entity_id"),
            )


class ProviderErrorKind(StrEnum):
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    HTTP_ERROR = "http_error"
    INVALID_RESPONSE = "invalid_response"
    REFUSAL = "refusal"


class PlanningOutputErrorKind(StrEnum):
    INVALID_JSON = "invalid_json"
    INVALID_PLAN = "invalid_plan"


class PlanningOutputError(Exception):
    """Safe classification for model output that cannot become a TurnPlan."""

    def __init__(self, kind: PlanningOutputErrorKind) -> None:
        if not isinstance(kind, PlanningOutputErrorKind):
            raise TypeError("kind must be a PlanningOutputErrorKind")
        self.kind = kind
        super().__init__(kind.value)


class PlanningProviderError(Exception):
    def __init__(self, kind: ProviderErrorKind, *, retryable: bool) -> None:
        if not isinstance(kind, ProviderErrorKind):
            raise TypeError("kind must be a ProviderErrorKind")
        if not isinstance(retryable, bool):
            raise TypeError("retryable must be boolean")
        self.kind = kind
        self.retryable = retryable
        super().__init__(kind.value)


class GroundedResponseOutputError(Exception):
    """Safe classification for a structurally invalid grounded answer."""

    def __init__(self) -> None:
        super().__init__("invalid_grounded_response")


class GroundedResponseProviderError(Exception):
    def __init__(
        self,
        kind: ProviderErrorKind,
        *,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        if not isinstance(kind, ProviderErrorKind):
            raise TypeError("kind must be a ProviderErrorKind")
        if not isinstance(retryable, bool):
            raise TypeError("retryable must be boolean")
        if status_code is not None and (
            type(status_code) is not int or not 100 <= status_code <= 599
        ):
            raise TypeError("status_code must be an HTTP status code or None")
        self.kind = kind
        self.retryable = retryable
        self.status_code = status_code
        super().__init__(kind.value)


class ConversationProviderError(Exception):
    """Safe provider failure for the conversational runtime."""

    def __init__(
        self,
        kind: ProviderErrorKind,
        *,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        if not isinstance(kind, ProviderErrorKind):
            raise TypeError("kind must be a ProviderErrorKind")
        if not isinstance(retryable, bool):
            raise TypeError("retryable must be boolean")
        if status_code is not None and (
            type(status_code) is not int or not 100 <= status_code <= 599
        ):
            raise TypeError("status_code must be an HTTP status code or None")
        self.kind = kind
        self.retryable = retryable
        self.status_code = status_code
        super().__init__(kind.value)


TextDeltaCallback = Callable[[str], Awaitable[None]]


@runtime_checkable
class ConversationProvider(Protocol):
    async def converse(
        self,
        request: ConversationRequest,
        *,
        on_text_delta: TextDeltaCallback | None = None,
    ) -> ConversationResult:
        """Return natural text or one bounded batch of semantic tool calls."""
        ...


@runtime_checkable
class PlanningProvider(Protocol):
    async def plan(self, request: PlanningRequest) -> PlanningResult:
        """Return one typed plan without retaining authoritative conversation state."""
        ...


@runtime_checkable
class GroundedResponseProvider(Protocol):
    async def respond(
        self,
        request: GroundedResponseRequest,
    ) -> GroundedResponseResult:
        """Return one answer grounded in the request's explicit evidence."""
        ...
