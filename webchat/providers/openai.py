"""OpenAI Responses API implementation of the provider-neutral planner port."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import json
import math
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from webchat.domain.common import require_non_empty
from webchat.harness.contracts import (
    ResponseStrategy,
    TURN_PLAN_SCHEMA_VERSION,
    TurnScope,
)
from webchat.harness.planning import PlanValidationError, parse_planning_output

from .base import (
    PlanningOutputError,
    PlanningOutputErrorKind,
    PlanningProviderError,
    PlanningRequest,
    PlanningResult,
    ProviderErrorKind,
    ProviderUsage,
)


DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_OUTPUT_TOKENS = 1_200

_PLANNER_INSTRUCTIONS = """You plan exactly one turn for a car-dealership assistant.
Use only the semantic commands present in the request context and schema.
Commands retrieve facts or prepare an action; they never execute mutations.
Never invent dealership facts. Ask one concise clarification when required data is missing.
Classify useful general car advice as dealership-adjacent. For mixed requests, retain the
dealership portion without answering unrelated trivia.
Use dealership_query for customer-facing location wording when no stable dealership ID is in context.
Choose the semantic intent even when its deterministic handler must collect or resolve missing fields.
Do not ask for customer or entity fields that the structured context already marked known; omit them
from arguments and let the deterministic handler merge authoritative state.
Do not add prerequisite reads that the semantic command description says its handler owns.
For "cheaper" refinements, use lower_max_price unless a vehicle is explicitly selected; then use
cheaper_than_selected.
For dealership-adjacent advice, provide only useful general vehicle guidance. Do not mention stock,
prices, availability or dealer-specific facts; the harness adds the dealership next step.
For dealership opening-hours questions, use the hours command; its handler returns published regular
hours and holiday exceptions, so a named holiday does not require an exact date clarification.
Emit at most one preparation command and use action_prepared whenever a preparation is present.
Return only the required JSON plan."""


@dataclass(frozen=True, slots=True)
class OpenAIProviderConfig:
    api_key: str = field(repr=False)
    model: str
    base_url: str = DEFAULT_OPENAI_BASE_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS

    def __post_init__(self) -> None:
        object.__setattr__(self, "api_key", require_non_empty(self.api_key, "api_key"))
        object.__setattr__(self, "model", require_non_empty(self.model, "model"))
        cleaned_url = require_non_empty(self.base_url, "base_url").rstrip("/")
        parsed = urlparse(cleaned_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("base_url must be an absolute HTTP(S) URL without query or fragment")
        object.__setattr__(self, "base_url", cleaned_url)
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number")
        if type(self.max_output_tokens) is not int or self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be a positive integer")


class OpenAIPlanningProvider:
    """One stateless structured planning call over an injected async HTTP client."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        config: OpenAIProviderConfig,
        *,
        monotonic: Callable[[], float] = time.perf_counter,
    ) -> None:
        if not isinstance(client, httpx.AsyncClient):
            raise ValueError("client must be an httpx.AsyncClient")
        if not isinstance(config, OpenAIProviderConfig):
            raise ValueError("config must be OpenAIProviderConfig")
        if not callable(monotonic):
            raise ValueError("monotonic must be callable")
        self._client = client
        self._config = config
        self._monotonic = monotonic

    async def plan(self, request: PlanningRequest) -> PlanningResult:
        if not isinstance(request, PlanningRequest):
            raise ValueError("request must be a PlanningRequest")
        started = self._monotonic()
        try:
            response = await self._client.post(
                f"{self._config.base_url}/responses",
                headers={
                    "Authorization": f"Bearer {self._config.api_key}",
                    "Content-Type": "application/json",
                },
                json=self._request_body(request),
                timeout=self._config.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise PlanningProviderError(
                ProviderErrorKind.TIMEOUT,
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise PlanningProviderError(
                ProviderErrorKind.TRANSPORT,
                retryable=True,
            ) from exc
        elapsed_ms = (self._monotonic() - started) * 1_000
        if response.status_code >= 400:
            retryable = (
                response.status_code in {408, 409, 429}
                or response.status_code >= 500
            )
            raise PlanningProviderError(
                ProviderErrorKind.HTTP_ERROR,
                retryable=retryable,
            )
        try:
            payload = response.json()
            return self._parse_response(payload, request=request, latency_ms=elapsed_ms)
        except (PlanningProviderError, PlanningOutputError):
            raise
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PlanningProviderError(
                ProviderErrorKind.INVALID_RESPONSE,
                retryable=False,
            ) from exc

    def _request_body(self, request: PlanningRequest) -> dict[str, Any]:
        return {
            "model": self._config.model,
            "store": False,
            "instructions": _PLANNER_INSTRUCTIONS,
            "input": request.context,
            "max_output_tokens": self._config.max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "turn_plan",
                    "strict": True,
                    "schema": _turn_plan_schema(request),
                }
            },
        }

    def _parse_response(
        self,
        value: object,
        *,
        request: PlanningRequest,
        latency_ms: float,
    ) -> PlanningResult:
        if not isinstance(value, Mapping) or value.get("status") != "completed":
            raise ValueError("response must be a completed object")
        output = value.get("output")
        if not isinstance(output, list):
            raise ValueError("response output must be an array")
        texts: list[str] = []
        refused = False
        for item in output:
            if not isinstance(item, Mapping) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                raise ValueError("message content must be an array")
            for part in content:
                if not isinstance(part, Mapping):
                    raise ValueError("message content item must be an object")
                if part.get("type") == "refusal":
                    refused = True
                elif part.get("type") == "output_text":
                    text = part.get("text")
                    if not isinstance(text, str) or not text.strip():
                        raise ValueError("output_text must contain text")
                    texts.append(text)
        if refused:
            raise PlanningProviderError(ProviderErrorKind.REFUSAL, retryable=False)
        if len(texts) != 1:
            raise ValueError("response must contain exactly one output_text item")
        try:
            plan_data = json.loads(texts[0])
        except json.JSONDecodeError as exc:
            raise PlanningOutputError(
                PlanningOutputErrorKind.INVALID_JSON
            ) from exc
        try:
            plan = parse_planning_output(
                plan_data,
                allowed_commands={item.name for item in request.commands},
            )
        except PlanValidationError as exc:
            raise PlanningOutputError(
                PlanningOutputErrorKind.INVALID_PLAN
            ) from exc
        usage = _parse_usage(value.get("usage"))
        model = value.get("model")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("response model must be a string")
        return PlanningResult(
            plan=plan,
            usage=usage,
            latency_ms=latency_ms,
            provider="openai",
            model=model,
        )


def _parse_usage(value: object) -> ProviderUsage:
    if not isinstance(value, Mapping):
        raise ValueError("response usage must be an object")
    fields = {}
    for name in ("input_tokens", "output_tokens", "total_tokens"):
        token_count = value.get(name)
        if type(token_count) is not int:
            raise ValueError(f"usage {name} must be an integer")
        fields[name] = token_count
    return ProviderUsage(**fields)


def _turn_plan_schema(request: PlanningRequest) -> dict[str, Any]:
    command_variants = []
    for command in request.commands:
        arguments = command.argument_schema
        arguments["required"] = sorted(arguments["properties"])
        command_variants.append(
            {
                "type": "object",
                "description": command.description,
                "properties": {
                    "name": {"type": "string", "const": command.name},
                    "arguments": arguments,
                },
                "required": ["name", "arguments"],
                "additionalProperties": False,
            }
        )
    schema = {
        "type": "object",
        "properties": {
            "schema_version": {
                "type": "integer",
                "const": TURN_PLAN_SCHEMA_VERSION,
            },
            "scope": {
                "type": "string",
                "enum": [item.value for item in TurnScope],
            },
            "commands": {
                "type": "array",
                "items": {"anyOf": command_variants},
                "maxItems": 2,
            },
            "response_strategy": {
                "type": "string",
                "enum": [item.value for item in ResponseStrategy],
            },
            "clarification_question": {
                "type": ["string", "null"],
                "maxLength": 300,
            },
            "adjacent_advice": {
                "type": ["string", "null"],
                "maxLength": 600,
            },
        },
        "required": [
            "schema_version",
            "scope",
            "commands",
            "response_strategy",
            "clarification_question",
            "adjacent_advice",
        ],
        "additionalProperties": False,
    }
    return schema
