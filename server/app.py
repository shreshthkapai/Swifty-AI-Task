"""ASGI composition root and anonymous conversation HTTP API."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
import hashlib
import json
import time

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from webchat.domain.errors import DealerError
from webchat.harness.planning import PlanValidationError, TurnRequest
from webchat.harness.runtime import TurnResult
from webchat.persistence import (
    PersistenceError,
    RevisionConflictError,
)
from webchat.providers.base import PlanningProviderError

from .config import AppConfig
from .contracts import (
    COOKIE_NAME,
    ApiProblem,
    IncomingTurn,
    conversation_id as _conversation_id,
    error_response as _error_response,
    parse_turn_payload as _parse_turn_payload,
    read_json as _read_json,
    request_id as _request_id,
    session_payload as _session_payload,
    turn_messages as _turn_messages,
    turn_payload as _turn_payload,
)
from .observability import JsonEventLogger, log_context
from .services import ChatServices, build_services


class ChatHttpApplication:
    def __init__(
        self,
        *,
        config: AppConfig,
        services: ChatServices,
        logger: JsonEventLogger,
        clock: Callable[[], datetime],
    ) -> None:
        self.config = config
        self.services = services
        self.logger = logger
        self.clock = clock
        self._locks = tuple(asyncio.Lock() for _ in range(256))

    async def health(self, request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def get_session(self, request: Request) -> Response:
        request_id = _request_id()
        conversation_id, _ = _conversation_id(request)
        with log_context(
            request_id=request_id,
            conversation_id=conversation_id,
            route="GET /api/chat/session",
        ):
            try:
                record = self.services.store.load_or_create(
                    conversation_id, now=self.clock()
                )
                response = JSONResponse(_session_payload(record))
            except PersistenceError:
                self.logger.emit(
                    "http_request",
                    operation="get_session",
                    outcome="error",
                    error_kind="persistence_error",
                    status_code=503,
                )
                response = _error_response(
                    503,
                    "service_unavailable",
                    "Chat history is temporarily unavailable.",
                    request_id,
                )
            self._set_cookie(response, conversation_id)
            response.headers["X-Request-ID"] = request_id
            return response

    async def post_turn(self, request: Request) -> Response:
        request_id = _request_id()
        conversation_id, _ = _conversation_id(request)
        started = time.perf_counter()
        turn_id: str | None = None
        with log_context(
            request_id=request_id,
            conversation_id=conversation_id,
            route="POST /api/chat/turns",
        ):
            try:
                payload = await _read_json(request, self.config.max_body_bytes)
                turn = _parse_turn_payload(
                    payload,
                    now=self.clock(),
                    max_message_chars=self.config.max_message_chars,
                )
                turn_id = turn.client_turn_id
                with log_context(
                    request_id=request_id,
                    conversation_id=conversation_id,
                    route="POST /api/chat/turns",
                    turn_id=turn_id,
                ):
                    response, result = await self._execute_turn(conversation_id, turn)
                    self.logger.emit(
                        "chat_turn",
                        operation="handle_turn",
                        duration_ms=(time.perf_counter() - started) * 1_000,
                        retries=0,
                        model_calls=0 if result is None else result.model_calls,
                        input_tokens=0 if result is None else result.input_tokens,
                        output_tokens=0 if result is None else result.output_tokens,
                        outcome="ok",
                        status_code=response.status_code,
                    )
            except ApiProblem as exc:
                response = _error_response(
                    exc.status_code, exc.code, exc.safe_message, request_id
                )
                self.logger.emit(
                    "chat_turn",
                    operation="handle_turn",
                    duration_ms=(time.perf_counter() - started) * 1_000,
                    retries=0,
                    model_calls=0,
                    input_tokens=0,
                    output_tokens=0,
                    outcome="rejected",
                    error_kind=exc.code,
                    status_code=exc.status_code,
                )
            except PlanningProviderError:
                response = _error_response(
                    503,
                    "planner_unavailable",
                    "The assistant is temporarily unavailable. Please try again.",
                    request_id,
                )
                self._log_turn_error(
                    started, "planner_unavailable", 503,
                    request_id=request_id, conversation_id=conversation_id,
                    turn_id=turn_id,
                )
            except PlanValidationError:
                response = _error_response(
                    503,
                    "planner_unavailable",
                    "The assistant is temporarily unavailable. Please try again.",
                    request_id,
                )
                self._log_turn_error(
                    started, "invalid_plan", 503,
                    request_id=request_id, conversation_id=conversation_id,
                    turn_id=turn_id,
                )
            except DealerError:
                response = _error_response(
                    503,
                    "dealer_unavailable",
                    "Dealership information is temporarily unavailable. Please try again.",
                    request_id,
                )
                self._log_turn_error(
                    started, "dealer_unavailable", 503,
                    request_id=request_id, conversation_id=conversation_id,
                    turn_id=turn_id,
                )
            except RevisionConflictError:
                response = _error_response(
                    409,
                    "turn_conflict",
                    "Another message is already being processed. Please retry once it finishes.",
                    request_id,
                )
                self._log_turn_error(
                    started, "revision_conflict", 409,
                    request_id=request_id, conversation_id=conversation_id,
                    turn_id=turn_id,
                )
            except PersistenceError:
                response = _error_response(
                    503,
                    "service_unavailable",
                    "The conversation could not be saved. Please try again.",
                    request_id,
                )
                self._log_turn_error(
                    started, "persistence_error", 503,
                    request_id=request_id, conversation_id=conversation_id,
                    turn_id=turn_id,
                )
            except Exception:
                response = _error_response(
                    500,
                    "internal_error",
                    "The request could not be completed.",
                    request_id,
                )
                self._log_turn_error(
                    started, "internal_error", 500,
                    request_id=request_id, conversation_id=conversation_id,
                    turn_id=turn_id,
                )
            self._set_cookie(response, conversation_id)
            response.headers["X-Request-ID"] = request_id
            return response

    async def post_turn_stream(self, request: Request) -> Response:
        request_id = _request_id()
        conversation_id, _ = _conversation_id(request)
        try:
            payload = await _read_json(request, self.config.max_body_bytes)
            turn = _parse_turn_payload(
                payload,
                now=self.clock(),
                max_message_chars=self.config.max_message_chars,
            )
        except ApiProblem as exc:
            response = _error_response(
                exc.status_code,
                exc.code,
                exc.safe_message,
                request_id,
            )
            self._set_cookie(response, conversation_id)
            response.headers["X-Request-ID"] = request_id
            return response

        response = StreamingResponse(
            self._stream_turn(
                conversation_id,
                turn,
                request_id=request_id,
            ),
            media_type="application/x-ndjson",
        )
        self._set_cookie(response, conversation_id)
        response.headers["X-Request-ID"] = request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Accel-Buffering"] = "no"
        return response

    async def _stream_turn(
        self,
        conversation_id: str,
        turn: IncomingTurn,
        *,
        request_id: str,
    ) -> AsyncIterator[bytes]:
        started = time.perf_counter()
        deltas: asyncio.Queue[str] = asyncio.Queue()

        async def on_text_delta(delta: str) -> None:
            await deltas.put(delta)

        with log_context(
            request_id=request_id,
            conversation_id=conversation_id,
            route="POST /api/chat/turns/stream",
            turn_id=turn.client_turn_id,
        ):
            task = asyncio.create_task(
                self._execute_turn(
                    conversation_id,
                    turn,
                    on_text_delta=on_text_delta,
                )
            )
            try:
                while not task.done() or not deltas.empty():
                    if not deltas.empty():
                        yield _ndjson({"type": "text_delta", "delta": deltas.get_nowait()})
                        continue
                    next_delta = asyncio.create_task(deltas.get())
                    done, _ = await asyncio.wait(
                        {task, next_delta},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if next_delta in done:
                        yield _ndjson({"type": "text_delta", "delta": next_delta.result()})
                    else:
                        next_delta.cancel()
                        try:
                            await next_delta
                        except asyncio.CancelledError:
                            pass
                result_response, result = await task
                payload = json.loads(bytes(result_response.body))
                yield _ndjson({"type": "complete", "payload": payload})
                self.logger.emit(
                    "chat_turn",
                    operation="handle_turn",
                    duration_ms=(time.perf_counter() - started) * 1_000,
                    retries=0,
                    model_calls=0 if result is None else result.model_calls,
                    input_tokens=0 if result is None else result.input_tokens,
                    output_tokens=0 if result is None else result.output_tokens,
                    outcome="ok",
                    status_code=200,
                )
            except asyncio.CancelledError:
                task.cancel()
                raise
            except Exception as exc:
                if not task.done():
                    task.cancel()
                code, message, retryable = _stream_failure(exc)
                self.logger.emit(
                    "chat_turn",
                    operation="handle_turn",
                    duration_ms=(time.perf_counter() - started) * 1_000,
                    retries=0,
                    model_calls=0,
                    input_tokens=0,
                    output_tokens=0,
                    outcome="error",
                    error_kind=code,
                    status_code=503,
                )
                yield _ndjson({
                    "type": "error",
                    "error": {
                        "code": code,
                        "message": message,
                        "retryable": retryable,
                    },
                })

    async def delete_session(self, request: Request) -> Response:
        request_id = _request_id()
        conversation_id, _ = _conversation_id(request)
        with log_context(
            request_id=request_id,
            conversation_id=conversation_id,
            route="DELETE /api/chat/session",
        ):
            try:
                async with self._lock_for(conversation_id):
                    self.services.store.delete(conversation_id)
                response = Response(status_code=204)
            except PersistenceError:
                response = _error_response(
                    503,
                    "service_unavailable",
                    "The conversation could not be cleared. Please try again.",
                    request_id,
                )
            response.delete_cookie(
                COOKIE_NAME,
                path="/",
                httponly=True,
                samesite="lax",
                secure=self.config.cookie_secure,
            )
            response.headers["X-Request-ID"] = request_id
            return response

    async def _execute_turn(
        self,
        conversation_id: str,
        turn: IncomingTurn,
        *,
        on_text_delta=None,
    ) -> tuple[Response, TurnResult | None]:
        async with self._lock_for(conversation_id):
            now = self.clock()
            record = self.services.store.load_or_create(conversation_id, now=now)
            committed = tuple(
                message
                for message in record.messages
                if message.client_turn_id == turn.client_turn_id
            )
            if committed:
                return (
                    JSONResponse(
                        _turn_payload(
                            revision=record.revision,
                            messages=committed,
                            duplicate=True,
                        )
                    ),
                    None,
                )
            prior_failures = ()
            pending = record.state.pending_action
            if pending is not None and pending.last_failure is not None:
                prior_failures = (pending.last_failure,)
            result = await self.services.runtime.handle(
                TurnRequest(
                    current_input=turn.text,
                    action_reference=turn.action,
                    page_observation=turn.page_observation,
                    state=record.state,
                    now=now,
                    recent_messages=record.messages,
                    prior_failures=prior_failures,
                ),
                on_text_delta=on_text_delta,
            )
            messages = _turn_messages(turn, result, now=now)
            commit = self.services.store.commit(
                conversation_id,
                expected_revision=record.revision,
                messages=messages,
                state=result.state,
                now=now,
            )
            return (
                JSONResponse(
                    _turn_payload(
                        revision=commit.record.revision,
                        messages=commit.committed_messages,
                        duplicate=commit.duplicate,
                    )
                ),
                result,
            )

    def _lock_for(self, conversation_id: str) -> asyncio.Lock:
        index = hashlib.sha256(conversation_id.encode("utf-8")).digest()[0]
        return self._locks[index]

    def _set_cookie(self, response: Response, conversation_id: str) -> None:
        response.set_cookie(
            COOKIE_NAME,
            conversation_id,
            max_age=self.config.retention_seconds,
            path="/",
            httponly=True,
            samesite="lax",
            secure=self.config.cookie_secure,
        )

    def _log_turn_error(
        self,
        started: float,
        error_kind: str,
        status_code: int,
        *,
        request_id: str,
        conversation_id: str,
        turn_id: str | None,
    ) -> None:
        with log_context(
            request_id=request_id,
            conversation_id=conversation_id,
            route="POST /api/chat/turns",
            turn_id=turn_id,
        ):
            self.logger.emit(
                "chat_turn",
                operation="handle_turn",
                duration_ms=(time.perf_counter() - started) * 1_000,
                retries=0,
                model_calls=0,
                input_tokens=0,
                output_tokens=0,
                outcome="error",
                error_kind=error_kind,
                status_code=status_code,
            )


def _ndjson(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _stream_failure(exc: Exception) -> tuple[str, str, bool]:
    if isinstance(exc, RevisionConflictError):
        return (
            "turn_conflict",
            "Another message is already being processed. Please try again.",
            True,
        )
    if isinstance(exc, PersistenceError):
        return (
            "service_unavailable",
            "The conversation could not be saved. Please try again.",
            True,
        )
    if isinstance(exc, DealerError):
        return (
            "dealer_unavailable",
            "Dealership information is temporarily unavailable. Please try again.",
            True,
        )
    return (
        "chat_unavailable",
        "The assistant is temporarily unavailable. Please try again.",
        True,
    )


def create_app(
    *,
    config: AppConfig | None = None,
    services: ChatServices | None = None,
    logger: JsonEventLogger | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Starlette:
    selected_config = config or AppConfig.from_env()
    selected_logger = logger or JsonEventLogger()
    selected_clock = clock or (lambda: datetime.now(UTC))
    supplied_services = services

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        active_services = supplied_services or build_services(
            selected_config, logger=selected_logger
        )
        controller.services = active_services
        try:
            yield
        finally:
            if supplied_services is None:
                await active_services.aclose()

    active_services = supplied_services or _DeferredServices()
    controller = ChatHttpApplication(
        config=selected_config,
        services=active_services,
        logger=selected_logger,
        clock=selected_clock,
    )
    app = Starlette(
        debug=False,
        routes=[
            Route("/health", controller.health, methods=["GET"]),
            Route("/api/chat/session", controller.get_session, methods=["GET"]),
            Route("/api/chat/turns", controller.post_turn, methods=["POST"]),
            Route(
                "/api/chat/turns/stream",
                controller.post_turn_stream,
                methods=["POST"],
            ),
            Route("/api/chat/session", controller.delete_session, methods=["DELETE"]),
        ],
        lifespan=lifespan,
        exception_handlers={Exception: _unexpected_error},
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[selected_config.allowed_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
        max_age=600,
    )
    return app


class _DeferredServices(ChatServices):
    def __init__(self) -> None:
        super().__init__(runtime=None, store=None)


async def _unexpected_error(request: Request, exc: Exception) -> Response:
    request_id = request.headers.get("X-Request-ID") or _request_id()
    return _error_response(
        500,
        "internal_error",
        "The request could not be completed.",
        request_id,
    )
