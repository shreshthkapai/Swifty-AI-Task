"""Provider-neutral conversational model port and safe failure contracts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Protocol, runtime_checkable

from webchat.harness.conversation import ConversationRequest, ConversationResult


class ProviderErrorKind(StrEnum):
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    HTTP_ERROR = "http_error"
    INVALID_RESPONSE = "invalid_response"
    REFUSAL = "refusal"


class ConversationProviderError(Exception):
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
