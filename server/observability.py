"""Metadata-only structured logging for chat requests and external calls."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
import hashlib
import json
import time
from typing import Any

from webchat.domain.errors import DealerError
from webchat.providers.base import (
    PlanningProviderError,
    PlanningRequest,
    PlanningResult,
)


_request_id: ContextVar[str | None] = ContextVar("chat_request_id", default=None)
_turn_id: ContextVar[str | None] = ContextVar("chat_turn_id", default=None)
_conversation_hash: ContextVar[str | None] = ContextVar(
    "chat_conversation_hash", default=None
)
_route: ContextVar[str | None] = ContextVar("chat_route", default=None)

_ALLOWED_METADATA = frozenset(
    {
        "duration_ms",
        "error_kind",
        "input_tokens",
        "model",
        "model_calls",
        "operation",
        "outcome",
        "output_tokens",
        "provider",
        "retries",
        "status_code",
    }
)


def conversation_hash(conversation_id: str) -> str:
    return hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:20]


@contextmanager
def log_context(
    *,
    request_id: str,
    conversation_id: str,
    route: str | None = None,
    turn_id: str | None = None,
) -> Iterator[None]:
    tokens = (
        (_request_id, _request_id.set(request_id)),
        (_turn_id, _turn_id.set(turn_id)),
        (_conversation_hash, _conversation_hash.set(conversation_hash(conversation_id))),
        (_route, _route.set(route)),
    )
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


class JsonEventLogger:
    """Emit canonical JSON while refusing arbitrary payload fields."""

    def __init__(
        self,
        *,
        sink: Callable[[str], None] = print,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sink = sink
        self._clock = clock or (lambda: datetime.now(UTC))

    def emit(self, event: str, **metadata: Any) -> None:
        record: dict[str, Any] = {
            "event": event,
            "timestamp": self._clock().astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }
        contextual = {
            "request_id": _request_id.get(),
            "turn_id": _turn_id.get(),
            "conversation_hash": _conversation_hash.get(),
            "route": _route.get(),
        }
        record.update({key: value for key, value in contextual.items() if value is not None})
        for key in sorted(_ALLOWED_METADATA):
            value = metadata.get(key)
            if value is None:
                continue
            if key == "duration_ms":
                value = round(float(value), 3)
            record[key] = value
        self._sink(
            json.dumps(
                record,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )


class ObservedPlanningProvider:
    """Log one metadata-only event around each provider planning call."""

    def __init__(
        self,
        provider: Any,
        *,
        logger: JsonEventLogger,
        monotonic: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._provider = provider
        self._logger = logger
        self._monotonic = monotonic

    async def plan(self, request: PlanningRequest) -> PlanningResult:
        started = self._monotonic()
        try:
            result = await self._provider.plan(request)
        except PlanningProviderError as exc:
            self._logger.emit(
                "external_call",
                operation="plan",
                duration_ms=(self._monotonic() - started) * 1_000,
                retries=0,
                outcome="error",
                error_kind=exc.kind.value,
            )
            raise
        except Exception:
            self._logger.emit(
                "external_call",
                operation="plan",
                duration_ms=(self._monotonic() - started) * 1_000,
                retries=0,
                outcome="error",
                error_kind="unexpected_error",
            )
            raise
        self._logger.emit(
            "external_call",
            operation="plan",
            duration_ms=(self._monotonic() - started) * 1_000,
            retries=0,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            provider=result.provider,
            model=result.model,
            outcome="ok",
        )
        return result


class ObservedDealerAdapter:
    """Transparent dealer port decorator with per-operation retry telemetry."""

    def __init__(
        self,
        dealer: Any,
        *,
        logger: JsonEventLogger,
        retry_reset: Callable[[], None] = lambda: None,
        retry_count: Callable[[], int] = lambda: 0,
        monotonic: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._dealer = dealer
        self._logger = logger
        self._retry_reset = retry_reset
        self._retry_count = retry_count
        self._monotonic = monotonic

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._dealer, name)
        if not callable(attribute) or name.startswith("_"):
            return attribute

        async def observed(*args: Any, **kwargs: Any) -> Any:
            self._retry_reset()
            started = self._monotonic()
            try:
                result = await attribute(*args, **kwargs)
            except DealerError as exc:
                self._logger.emit(
                    "external_call",
                    operation=name,
                    duration_ms=(self._monotonic() - started) * 1_000,
                    retries=self._retry_count(),
                    outcome="error",
                    error_kind=exc.kind.value,
                )
                raise
            except Exception:
                self._logger.emit(
                    "external_call",
                    operation=name,
                    duration_ms=(self._monotonic() - started) * 1_000,
                    retries=self._retry_count(),
                    outcome="error",
                    error_kind="unexpected_error",
                )
                raise
            self._logger.emit(
                "external_call",
                operation=name,
                duration_ms=(self._monotonic() - started) * 1_000,
                retries=self._retry_count(),
                outcome="ok",
            )
            return result

        return observed
