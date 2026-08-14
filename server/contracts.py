"""Strict browser-facing request and response contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import json
import re
import secrets
from typing import Any
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse

from webchat.harness.execution import SUPPORTED_UI_ACTION_TYPES
from webchat.harness.turn import TurnResult
from webchat.harness.state import (
    ActionReference,
    MessageBlock,
    MessageRole,
    PAGE_SEARCH_FILTER_FIELDS,
    PageContext,
    StructuredMessage,
)
from webchat.persistence import ConversationRecord


API_SCHEMA_VERSION = 1
COOKIE_NAME = "northstar_chat"
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9_-]{40,64}$")
_CLIENT_TURN_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_ENTITY_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


class ApiProblem(ValueError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.safe_message = message
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class IncomingTurn:
    client_turn_id: str
    text: str | None = None
    action: ActionReference | None = None
    page_observation: PageContext | None = None


def request_id() -> str:
    return secrets.token_urlsafe(18)


def conversation_id(request: Request) -> tuple[str, bool]:
    value = request.cookies.get(COOKIE_NAME)
    if value is not None and _OPAQUE_ID.fullmatch(value):
        return value, False
    return secrets.token_urlsafe(32), True


async def read_json(request: Request, max_body_bytes: int) -> Mapping[str, Any]:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json" and not media_type.endswith("+json"):
        raise ApiProblem(
            415,
            "unsupported_media_type",
            "The request body must use application/json.",
        )
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            parsed_content_length = int(content_length)
        except ValueError as exc:
            raise ApiProblem(400, "invalid_request", "The request is invalid.") from exc
        if parsed_content_length > max_body_bytes:
            raise ApiProblem(413, "request_too_large", "The request body is too large.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_body_bytes:
            raise ApiProblem(413, "request_too_large", "The request body is too large.")
    if not body:
        raise ApiProblem(400, "invalid_request", "A JSON request body is required.")
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiProblem(400, "invalid_request", "The request body must be valid JSON.") from exc
    if not isinstance(value, Mapping):
        raise ApiProblem(422, "invalid_request", "The request body must be a JSON object.")
    return value


def parse_turn_payload(
    value: Mapping[str, Any],
    *,
    now: datetime,
    max_message_chars: int,
) -> IncomingTurn:
    _reject_unknown(value, {"client_turn_id", "text", "action", "page_observation"})
    client_turn_id = value.get("client_turn_id")
    if not isinstance(client_turn_id, str) or not _CLIENT_TURN_ID.fullmatch(client_turn_id):
        raise ApiProblem(422, "invalid_request", "client_turn_id has an invalid format.")
    text = value.get("text")
    action_value = value.get("action")
    if (text is None) == (action_value is None):
        raise ApiProblem(422, "invalid_request", "Provide either text or one action reference.")
    action = None
    if text is not None:
        if not isinstance(text, str) or not text.strip() or len(text) > max_message_chars:
            raise ApiProblem(422, "invalid_request", "text has an invalid length.")
        text = text.strip()
    else:
        action = _parse_action(action_value)
    page = value.get("page_observation")
    return IncomingTurn(
        client_turn_id=client_turn_id,
        text=text,
        action=action,
        page_observation=None if page is None else _parse_page(page, now=now),
    )


def turn_messages(
    turn: IncomingTurn,
    result: TurnResult,
    *,
    now: datetime,
) -> tuple[StructuredMessage, ...]:
    user_blocks: tuple[MessageBlock, ...] = ()
    if turn.action is not None:
        user_blocks = (
            MessageBlock(
                "action_submission",
                {
                    "schema_version": API_SCHEMA_VERSION,
                    "action_id": turn.action.action_id,
                    "action_type": turn.action.action_type,
                },
            ),
        )
    return (
        StructuredMessage(
            message_id=secrets.token_urlsafe(18),
            client_turn_id=turn.client_turn_id,
            role=MessageRole.USER,
            created_at=now,
            text=turn.text,
            blocks=user_blocks,
        ),
        StructuredMessage(
            message_id=secrets.token_urlsafe(18),
            client_turn_id=turn.client_turn_id,
            role=MessageRole.ASSISTANT,
            created_at=now,
            blocks=result.blocks,
        ),
    )


def session_payload(record: ConversationRecord) -> dict[str, Any]:
    return {
        "schema_version": API_SCHEMA_VERSION,
        "revision": record.revision,
        "messages": [message.to_dict() for message in record.messages],
    }


def turn_payload(
    *,
    revision: int,
    messages: tuple[StructuredMessage, ...],
    duplicate: bool,
) -> dict[str, Any]:
    return {
        "schema_version": API_SCHEMA_VERSION,
        "revision": revision,
        "duplicate": duplicate,
        "messages": [message.to_dict() for message in messages],
    }


def error_response(
    status_code: int,
    code: str,
    message: str,
    current_request_id: str,
) -> JSONResponse:
    return JSONResponse(
        {
            "error": {"code": code, "message": message},
            "request_id": current_request_id,
        },
        status_code=status_code,
    )


def _parse_action(value: object) -> ActionReference:
    if not isinstance(value, Mapping):
        raise ApiProblem(422, "invalid_request", "action must be an object.")
    _reject_unknown(value, {"action_id", "action_type"})
    action_id = value.get("action_id")
    action_type = value.get("action_type")
    if not isinstance(action_id, str) or not _ENTITY_ID.fullmatch(action_id):
        raise ApiProblem(422, "invalid_request", "action_id has an invalid format.")
    if action_type not in SUPPORTED_UI_ACTION_TYPES:
        raise ApiProblem(422, "invalid_request", "action_type is not supported.")
    return ActionReference(action_id, action_type)


def _parse_page(value: object, *, now: datetime) -> PageContext:
    if not isinstance(value, Mapping):
        raise ApiProblem(422, "invalid_request", "page_observation must be an object.")
    _reject_unknown(value, {"current_url", "page_vehicle_id", "search_filters"})
    current_url = value.get("current_url")
    vehicle_id = value.get("page_vehicle_id")
    search_filters = _parse_search_filters(value.get("search_filters"))
    if current_url is None and vehicle_id is None and not search_filters:
        raise ApiProblem(422, "invalid_request", "page_observation cannot be empty.")
    clean_url = None
    if current_url is not None:
        if not isinstance(current_url, str) or len(current_url) > 300:
            raise ApiProblem(422, "invalid_request", "current_url has an invalid format.")
        parsed = urlsplit(current_url)
        if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
            raise ApiProblem(422, "invalid_request", "current_url must be a relative site URL.")
        if any(ord(character) < 32 for character in current_url):
            raise ApiProblem(422, "invalid_request", "current_url has an invalid format.")
        clean_url = parsed.path or "/"
        if parsed.fragment:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", parsed.fragment):
                raise ApiProblem(422, "invalid_request", "current_url has an invalid fragment.")
            clean_url += f"#{parsed.fragment}"
    if vehicle_id is not None and (
        not isinstance(vehicle_id, str) or not _ENTITY_ID.fullmatch(vehicle_id)
    ):
        raise ApiProblem(422, "invalid_request", "page_vehicle_id has an invalid format.")
    return PageContext(
        current_url=clean_url,
        page_vehicle_id=vehicle_id,
        search_filters=search_filters,
        observed_at=now,
        is_authoritative=False,
    )


def _parse_search_filters(value: object) -> dict[str, str | int]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ApiProblem(422, "invalid_request", "search_filters must be an object.")
    _reject_unknown(value, set(PAGE_SEARCH_FILTER_FIELDS))
    result: dict[str, str | int] = {}
    for name, item in value.items():
        if name == "max_price_minor":
            if (
                isinstance(item, bool)
                or not isinstance(item, int)
                or not 0 <= item <= 1_000_000_000
            ):
                raise ApiProblem(
                    422,
                    "invalid_request",
                    "max_price_minor has an invalid format.",
                )
            result[name] = item
            continue
        if not isinstance(item, str) or not item.strip() or len(item) > 100:
            raise ApiProblem(422, "invalid_request", f"{name} has an invalid format.")
        result[name] = item.strip()
    return result


def _reject_unknown(value: Mapping[str, Any], allowed: set[str]) -> None:
    if set(value) - allowed:
        raise ApiProblem(422, "invalid_request", "The request contains unknown fields.")
