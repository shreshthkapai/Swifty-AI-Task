"""OpenAI Responses API implementation of the conversational provider port."""

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
from webchat.harness.conversation import (
    ConversationRequest,
    ConversationResult,
    ConversationToolCall,
    ConversationUsage,
)

from .base import (
    ConversationProviderError,
    ProviderErrorKind,
    TextDeltaCallback,
)


DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_OUTPUT_TOKENS = 1_200

_CONVERSATION_INSTRUCTIONS = """You are a concise, natural car-dealership assistant.
Answer the customer's actual question directly. Use the supplied conversation state and recent
messages to resolve follow-ups. Call the available semantic tools whenever current dealership facts
or an action are required; never invent inventory, pricing, availability, opening hours, slots,
offers, bookings, or customer records. You may call multiple independent read tools together, but
never combine a preparation or action-control tool with another tool. After tool results arrive,
answer using only their facts for dealership-specific claims, state important unknowns plainly, and
do not call another tool. Consequential operations must be prepared and then explicitly confirmed;
never claim a mutation succeeded unless its tool result says so. Give useful general automotive
guidance when asked, clearly separating it from dealer facts. For mixed requests, answer the useful
dealership portion without becoming a general trivia assistant. Keep answers conversational and
brief; supporting cards and actions are rendered separately."""


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


class OpenAIConversationProvider:
    """One stateless Responses API conversation with one bounded tool continuation."""

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

    async def converse(
        self,
        request: ConversationRequest,
        *,
        on_text_delta: TextDeltaCallback | None = None,
    ) -> ConversationResult:
        if not isinstance(request, ConversationRequest):
            raise ValueError("request must be a ConversationRequest")
        if on_text_delta is not None and not callable(on_text_delta):
            raise ValueError("on_text_delta must be callable or None")
        if on_text_delta is not None:
            return await self._converse_stream(request, on_text_delta)
        started = self._monotonic()
        try:
            body = self._request_body(request)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ConversationProviderError(
                ProviderErrorKind.INVALID_RESPONSE,
                retryable=False,
            ) from exc
        try:
            response = await self._client.post(
                f"{self._config.base_url}/responses",
                headers={
                    "Authorization": f"Bearer {self._config.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self._config.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ConversationProviderError(
                ProviderErrorKind.TIMEOUT,
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise ConversationProviderError(
                ProviderErrorKind.TRANSPORT,
                retryable=True,
            ) from exc
        elapsed_ms = (self._monotonic() - started) * 1_000
        if response.status_code >= 400:
            raise ConversationProviderError(
                ProviderErrorKind.HTTP_ERROR,
                retryable=(
                    response.status_code in {408, 409, 429}
                    or response.status_code >= 500
                ),
                status_code=response.status_code,
            )
        try:
            result = self._parse_response(response.json(), latency_ms=elapsed_ms)
        except ConversationProviderError:
            raise
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ConversationProviderError(
                ProviderErrorKind.INVALID_RESPONSE,
                retryable=False,
            ) from exc
        return result

    async def _converse_stream(
        self,
        request: ConversationRequest,
        on_text_delta: TextDeltaCallback,
    ) -> ConversationResult:
        started = self._monotonic()
        completed: object | None = None
        streamed_parts: list[str] = []
        first_token_ms: float | None = None
        try:
            body = self._request_body(request)
            body["stream"] = True
            async with self._client.stream(
                "POST",
                f"{self._config.base_url}/responses",
                headers={
                    "Authorization": f"Bearer {self._config.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self._config.timeout_seconds,
            ) as response:
                if response.status_code >= 400:
                    raise ConversationProviderError(
                        ProviderErrorKind.HTTP_ERROR,
                        retryable=(
                            response.status_code in {408, 409, 429}
                            or response.status_code >= 500
                        ),
                        status_code=response.status_code,
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:]
                    if raw == "[DONE]":
                        continue
                    event = json.loads(raw)
                    if not isinstance(event, Mapping):
                        raise ValueError("stream event must be an object")
                    event_type = event.get("type")
                    if event_type == "response.output_text.delta":
                        delta = event.get("delta")
                        if not isinstance(delta, str) or not delta:
                            raise ValueError("text delta must be a non-empty string")
                        if first_token_ms is None:
                            first_token_ms = (self._monotonic() - started) * 1_000
                        streamed_parts.append(delta)
                        await on_text_delta(delta)
                    elif event_type == "response.completed":
                        completed = event.get("response")
                    elif event_type in {"response.failed", "response.incomplete"}:
                        raise ValueError("provider stream did not complete")
        except ConversationProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise ConversationProviderError(
                ProviderErrorKind.TIMEOUT,
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise ConversationProviderError(
                ProviderErrorKind.TRANSPORT,
                retryable=True,
            ) from exc
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ConversationProviderError(
                ProviderErrorKind.INVALID_RESPONSE,
                retryable=False,
            ) from exc
        elapsed_ms = (self._monotonic() - started) * 1_000
        try:
            result = self._parse_response(
                completed,
                latency_ms=elapsed_ms,
                time_to_first_token_ms=first_token_ms,
            )
            if result.text is not None and result.text != "".join(streamed_parts):
                raise ValueError("streamed text does not match completed response")
            if result.text is None and streamed_parts:
                raise ValueError("tool response cannot contain streamed text")
            return result
        except ConversationProviderError:
            raise
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ConversationProviderError(
                ProviderErrorKind.INVALID_RESPONSE,
                retryable=False,
            ) from exc

    def _request_body(self, request: ConversationRequest) -> dict[str, Any]:
        exchange = request.exchange
        body: dict[str, Any] = {
            "model": self._config.model,
            "store": False,
            "instructions": _CONVERSATION_INSTRUCTIONS,
            "input": [{"role": "user", "content": request.context}],
            "tools": [_conversation_tool(item) for item in request.tools],
            "tool_choice": "none" if exchange is not None else "auto",
            "parallel_tool_calls": exchange is None,
            "max_output_tokens": self._config.max_output_tokens,
        }
        if exchange is None:
            return body
        if exchange.continuation is None:
            raise ValueError("OpenAI tool continuations require prior output items")
        previous_output = json.loads(exchange.continuation)
        if not isinstance(previous_output, list) or not all(
            isinstance(item, Mapping) for item in previous_output
        ):
            raise ValueError("OpenAI continuation must encode an output-item array")
        body["input"].extend(previous_output)
        for result in exchange.results:
            body["input"].append({
                "type": "function_call_output",
                "call_id": result.call_id,
                "output": json.dumps(
                    result.output_dict(),
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            })
        return body

    @staticmethod
    def _parse_response(
        value: object,
        *,
        latency_ms: float,
        time_to_first_token_ms: float | None = None,
    ) -> ConversationResult:
        if not isinstance(value, Mapping) or value.get("status") != "completed":
            raise ValueError("response must be a completed object")
        output = value.get("output")
        if not isinstance(output, list):
            raise ValueError("response output must be an array")
        calls: list[ConversationToolCall] = []
        texts: list[str] = []
        refused = False
        for item in output:
            if not isinstance(item, Mapping):
                raise ValueError("response output items must be objects")
            if item.get("type") == "function_call":
                arguments = item.get("arguments")
                if not isinstance(arguments, str):
                    raise ValueError("function arguments must be JSON text")
                decoded = json.loads(arguments)
                if not isinstance(decoded, Mapping):
                    raise ValueError("function arguments must encode an object")
                calls.append(ConversationToolCall(
                    call_id=item.get("call_id"),
                    name=item.get("name"),
                    arguments=decoded,
                ))
            elif item.get("type") == "message":
                content = item.get("content")
                if not isinstance(content, list):
                    raise ValueError("message content must be an array")
                for part in content:
                    if not isinstance(part, Mapping):
                        raise ValueError("message content items must be objects")
                    if part.get("type") == "refusal":
                        refused = True
                    elif part.get("type") == "output_text":
                        text = part.get("text")
                        if not isinstance(text, str) or not text.strip():
                            raise ValueError("output_text must contain text")
                        texts.append(text)
        if refused:
            raise ConversationProviderError(
                ProviderErrorKind.REFUSAL,
                retryable=False,
            )
        if calls and texts:
            raise ValueError("response cannot mix customer text and function calls")
        if calls:
            text = None
            continuation = json.dumps(
                output,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            first_token_ms = None
        else:
            if len(texts) != 1:
                raise ValueError("response must contain exactly one output_text item")
            text = texts[0]
            continuation = None
            first_token_ms = (
                latency_ms
                if time_to_first_token_ms is None
                else time_to_first_token_ms
            )
        model = value.get("model")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("response model must be a string")
        return ConversationResult(
            usage=_parse_conversation_usage(value.get("usage")),
            latency_ms=latency_ms,
            provider="openai",
            model=model,
            text=text,
            tool_calls=tuple(calls),
            time_to_first_token_ms=first_token_ms,
            continuation=continuation,
        )


def _conversation_tool(spec) -> dict[str, Any]:
    parameters = json.loads(json.dumps(spec.argument_schema))
    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("tool argument schema requires object properties")
    parameters["required"] = sorted(properties)
    parameters["additionalProperties"] = False
    return {
        "type": "function",
        "name": spec.name,
        "description": spec.description,
        "parameters": parameters,
        "strict": True,
    }


def _parse_conversation_usage(value: object) -> ConversationUsage:
    if not isinstance(value, Mapping):
        raise ValueError("response usage must be an object")
    values: dict[str, int] = {}
    for name in ("input_tokens", "output_tokens", "total_tokens"):
        count = value.get(name)
        if type(count) is not int:
            raise ValueError(f"usage {name} must be an integer")
        values[name] = count
    return ConversationUsage(**values)
