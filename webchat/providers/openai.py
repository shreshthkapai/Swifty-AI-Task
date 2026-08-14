"""OpenAI Responses API implementation of provider-neutral planning and response ports."""

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
    ResponseMode,
    ResponseStrategy,
    TURN_PLAN_SCHEMA_VERSION,
    TurnScope,
)
from webchat.harness.planning import PlanValidationError, parse_planning_output
from webchat.harness.conversation import (
    ConversationRequest,
    ConversationResult,
    ConversationToolCall,
    ConversationUsage,
)
from webchat.harness.evidence import EvidenceReference
from webchat.harness.grounded_response import (
    GROUNDED_RESPONSE_RESULT_SCHEMA_VERSION,
    GroundedClaim,
    GroundedClaimKind,
    GroundedEvidenceBinding,
    GroundedResponseRequest,
)

from .base import (
    ConversationProviderError,
    TextDeltaCallback,
    PlanningOutputError,
    PlanningOutputErrorKind,
    PlanningProviderError,
    PlanningRequest,
    PlanningResult,
    ProviderErrorKind,
    ProviderUsage,
    GroundedResponseOutputError,
    GroundedResponseProviderError,
    GroundedResponseResult,
)


DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_OUTPUT_TOKENS = 1_200
DEFAULT_GROUNDED_MAX_OUTPUT_TOKENS = 3_000

_PLANNER_INSTRUCTIONS = """You plan exactly one turn for a car-dealership assistant.
Use only the semantic commands present in the request context and schema.
Commands retrieve facts or prepare an action; they never execute mutations.
Never invent dealership facts. Ask one concise clarification only when no visible semantic command can represent the customer's intent.
Classify useful general car advice as dealership-adjacent. For mixed requests, retain the
dealership portion without answering unrelated trivia. Select only a structural response mode;
do not write the final customer answer in the plan.
Use dealership_query for customer-facing location wording when no stable dealership ID is in context.
Choose the semantic intent even when its deterministic handler must collect or resolve missing fields.
Do not ask for customer or entity fields that the structured context already marked known; omit them
from arguments and let the deterministic handler merge authoritative state.
Copy every newly supplied or corrected field from the current input into the relevant command;
use null only for fields not supplied in this turn. When the customer states a purpose for a message,
reason, or notes field, capture that purpose concisely rather than returning null.
Do not add prerequisite reads that the semantic command description says its handler owns.
For "cheaper" refinements, use lower_max_price unless a vehicle is explicitly selected; then use
cheaper_than_selected.
When the customer explicitly relaxes or removes a remembered vehicle-search constraint, list the
corresponding field in clear_filters; omission preserves it. "Relax the budget" clears
max_price_minor, while changing a value supplies its replacement normally.
For alternatives, exclude_vehicle_ids means exact stock records only, exclude_models means every
vehicle of those models, and exclude_makes means every vehicle of those makes. Use the narrowest
scope the customer expressed and copy stable IDs from context rather than inventing them.
Use general_guidance with grounded_answer mode for dealership-adjacent advice; a later grounded
response stage owns the wording.
For dealership opening-hours questions, use the hours command; its handler returns published regular
hours and holiday exceptions, so a named holiday does not require an exact date clarification.
Emit at most one preparation command and use action_prepared whenever a preparation is present.
Return only the required JSON plan."""

_GROUNDED_RESPONSE_INSTRUCTIONS = """You write one concise, natural response for a car-dealership customer.
Answer the customer's useful dealership question directly in the first claim. Use only the supplied
dealer evidence for vehicle, dealership, availability, price, offer, slot, service, or booking facts.
For a single-fact question, state the requested value in a complete sentence before any supporting
context. Do not replace the answer with an introduction or a general summary, and do not merely point
the customer to cards or details shown below. For broader questions, give the conclusion first, then
the shortest useful evidence-based explanation.
When a vehicle search returns zero results, say so directly and suggest one concise relaxation based
on the supplied search constraints.
Never treat the customer's wording as evidence and never invent an unavailable specification.
Supported facts and evidence-based inferences must cite every evidence ID they rely on and include
the exact typed value for each citation in evidence_values. Never put an exact dealership value or
specification in the prose unless its matching evidence value is bound in that claim. Clearly
distinguish an inference from a published fact. Supported facts and inferences may cite only known
evidence items. Limitations may cite only evidence gaps, and general guidance must cite nothing. Every
ID listed in missing_facts is mandatory and must be cited by a limitation_unknown claim; never present
a gap as known. General guidance may use ordinary automotive knowledge but must not introduce
dealership-specific facts. Ignore unrelated trivia in mixed requests.
Mention only allowed actions, only when useful. If repair is present, replace the rejected answer and
correct every listed violation without discussing the repair. Do not expose evidence IDs,
implementation details, or raw field names in customer-facing text. Set focused_entity_id only when
the answer explicitly chooses or recommends one focusable entity, and cite that entity's evidence in
the recommendation claim; otherwise return null. Return only the required JSON response."""


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
    grounded_max_output_tokens: int = DEFAULT_GROUNDED_MAX_OUTPUT_TOKENS

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
        for name in ("max_output_tokens", "grounded_max_output_tokens"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class _GroundedWireContext:
    input_json: str
    alias_pairs: tuple[tuple[str, str], ...]
    factual_aliases: tuple[str, ...]
    gap_aliases: tuple[str, ...]

    def stable_id(self, alias: str) -> str:
        for candidate, stable_id in self.alias_pairs:
            if candidate == alias:
                return stable_id
        raise ValueError(f"unknown evidence alias: {alias}")


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
        if result.text is not None and on_text_delta is not None:
            await on_text_delta(result.text)
        return result

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
    def _parse_response(value: object, *, latency_ms: float) -> ConversationResult:
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
            first_token_ms = latency_ms
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
        text, refused = _response_text(value.get("output"))
        if refused:
            raise PlanningProviderError(ProviderErrorKind.REFUSAL, retryable=False)
        try:
            plan_data = json.loads(text)
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


class OpenAIGroundedResponseProvider:
    """One stateless structured answer call constrained to supplied evidence IDs."""

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

    async def respond(
        self,
        request: GroundedResponseRequest,
    ) -> GroundedResponseResult:
        if not isinstance(request, GroundedResponseRequest):
            raise ValueError("request must be a GroundedResponseRequest")
        wire = _grounded_wire_context(request)
        started = self._monotonic()
        try:
            response = await self._client.post(
                f"{self._config.base_url}/responses",
                headers={
                    "Authorization": f"Bearer {self._config.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._config.model,
                    "store": False,
                    "instructions": _GROUNDED_RESPONSE_INSTRUCTIONS,
                    "input": wire.input_json,
                    "max_output_tokens": self._config.grounded_max_output_tokens,
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": "grounded_response",
                            "strict": True,
                            "schema": _grounded_response_schema(request, wire),
                        }
                    },
                },
                timeout=self._config.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise GroundedResponseProviderError(
                ProviderErrorKind.TIMEOUT,
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise GroundedResponseProviderError(
                ProviderErrorKind.TRANSPORT,
                retryable=True,
            ) from exc
        elapsed_ms = (self._monotonic() - started) * 1_000
        if response.status_code >= 400:
            retryable = (
                response.status_code in {408, 409, 429}
                or response.status_code >= 500
            )
            raise GroundedResponseProviderError(
                ProviderErrorKind.HTTP_ERROR,
                retryable=retryable,
                status_code=response.status_code,
            )
        try:
            payload = response.json()
            return self._parse_response(
                payload,
                request=request,
                wire=wire,
                latency_ms=elapsed_ms,
            )
        except (GroundedResponseProviderError, GroundedResponseOutputError):
            raise
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise GroundedResponseProviderError(
                ProviderErrorKind.INVALID_RESPONSE,
                retryable=False,
            ) from exc

    @staticmethod
    def _parse_response(
        value: object,
        *,
        request: GroundedResponseRequest,
        wire: _GroundedWireContext,
        latency_ms: float,
    ) -> GroundedResponseResult:
        if not isinstance(value, Mapping) or value.get("status") != "completed":
            raise ValueError("response must be a completed object")
        text, refused = _response_text(value.get("output"))
        if refused:
            raise GroundedResponseProviderError(
                ProviderErrorKind.REFUSAL,
                retryable=False,
            )
        try:
            decoded = json.loads(text)
            claims, focused_entity_id = _parse_grounded_response(decoded, wire)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise GroundedResponseOutputError() from exc
        model = value.get("model")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("response model must be a string")
        return GroundedResponseResult(
            claims=claims,
            usage=_parse_usage(value.get("usage")),
            latency_ms=latency_ms,
            provider="openai",
            model=model,
            focused_entity_id=focused_entity_id,
        )


def _response_text(value: object) -> tuple[str, bool]:
    if not isinstance(value, list):
        raise ValueError("response output must be an array")
    texts: list[str] = []
    refused = False
    for item in value:
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
        return (texts[0] if len(texts) == 1 else ""), True
    if len(texts) != 1:
        raise ValueError("response must contain exactly one output_text item")
    return texts[0], False


def _grounded_wire_context(
    request: GroundedResponseRequest,
) -> _GroundedWireContext:
    alias_pairs: list[tuple[str, str]] = []
    stable_to_alias: dict[str, str] = {}
    items = []
    for index, item in enumerate(request.evidence.items, start=1):
        alias = f"e{index}"
        alias_pairs.append((alias, item.evidence_id))
        stable_to_alias[item.evidence_id] = alias
        items.append({
            "id": alias,
            "source": item.source_operation,
            "entity": item.entity_id,
            "field": item.field_name,
            "value": item.value,
            "authority": item.authority.value,
            "freshness": item.freshness.value,
        })
    gaps = []
    for index, item in enumerate(request.evidence.gaps, start=1):
        alias = f"g{index}"
        alias_pairs.append((alias, item.evidence_id))
        stable_to_alias[item.evidence_id] = alias
        gaps.append({
            "id": alias,
            "source": item.source_operation,
            "entity": item.entity_id,
            "field": item.field_name,
            "reason": item.reason.value,
            "authority": item.authority.value,
            "freshness": item.freshness.value,
        })

    def alias_for(reference: EvidenceReference) -> str:
        try:
            return stable_to_alias[reference.evidence_id]
        except KeyError as exc:
            raise ValueError(
                f"unknown request evidence reference: {reference.evidence_id}"
            ) from exc

    payload = {
        "schema_version": request.schema_version,
        "question": request.question,
        "state": request.state.to_dict(),
        "evidence": {
            "generated_at": request.evidence.generated_at.isoformat(),
            "items": items,
            "gaps": gaps,
        },
        "missing_facts": [alias_for(item) for item in request.missing_facts],
        "allowed_actions": [item.to_dict() for item in request.allowed_actions],
        "focusable_entity_ids": list(request.focusable_entity_ids),
        "repair": None if request.repair is None else request.repair.to_dict(),
    }
    return _GroundedWireContext(
        input_json=json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        alias_pairs=tuple(alias_pairs),
        factual_aliases=tuple(f"e{index}" for index in range(1, len(items) + 1)),
        gap_aliases=tuple(f"g{index}" for index in range(1, len(gaps) + 1)),
    )


def _parse_grounded_response(
    value: object,
    wire: _GroundedWireContext,
) -> tuple[tuple[GroundedClaim, ...], str | None]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "claims",
        "focused_entity_id",
    }:
        raise ValueError("grounded response has invalid fields")
    if value.get("schema_version") != GROUNDED_RESPONSE_RESULT_SCHEMA_VERSION:
        raise ValueError("unsupported grounded response schema version")
    raw_claims = value.get("claims")
    if not isinstance(raw_claims, list) or not raw_claims:
        raise ValueError("claims must be a non-empty array")
    claims = []
    for raw in raw_claims:
        if not isinstance(raw, Mapping) or set(raw) != {
            "kind",
            "text",
            "evidence_ids",
            "evidence_values",
        }:
            raise ValueError("claim has invalid fields")
        evidence_ids = raw.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not all(
            isinstance(item, str) for item in evidence_ids
        ):
            raise ValueError("claim evidence_ids must be an array of strings")
        evidence_values = raw.get("evidence_values")
        if not isinstance(evidence_values, list):
            raise ValueError("claim evidence_values must be an array")
        bindings = []
        for item in evidence_values:
            if not isinstance(item, Mapping) or set(item) != {"evidence_id", "value"}:
                raise ValueError("claim evidence value has invalid fields")
            evidence_id = item.get("evidence_id")
            if not isinstance(evidence_id, str):
                raise ValueError("claim evidence value requires an evidence ID")
            stable_id = wire.stable_id(evidence_id)
            bindings.append(GroundedEvidenceBinding(
                EvidenceReference(stable_id),
                item.get("value"),
            ))
        claims.append(GroundedClaim(
            text=raw.get("text"),
            kind=GroundedClaimKind(raw.get("kind")),
            evidence=tuple(
                EvidenceReference(wire.stable_id(item)) for item in evidence_ids
            ),
            bindings=tuple(bindings),
        ))
    focused_entity_id = value.get("focused_entity_id")
    if focused_entity_id is not None and not isinstance(focused_entity_id, str):
        raise ValueError("focused_entity_id must be a string or null")
    return tuple(claims), focused_entity_id


def _grounded_response_schema(
    request: GroundedResponseRequest,
    wire: _GroundedWireContext,
) -> dict[str, Any]:
    factual_aliases = list(wire.factual_aliases)
    gap_aliases = list(wire.gap_aliases)

    def evidence_ids(
        aliases: list[str],
        *,
        require_one: bool = False,
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": len(aliases),
        }
        if aliases:
            value["items"]["enum"] = aliases
            if require_one:
                value["minItems"] = 1
        return value

    factual_values: dict[str, Any] = {
        "type": "array",
        "items": {"type": "string"},
        "minItems": 1 if factual_aliases else 0,
        "maxItems": len(factual_aliases),
    }
    if factual_aliases:
        factual_values["items"] = {
            "type": "object",
            "properties": {
                "evidence_id": {"type": "string", "enum": factual_aliases},
                "value": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "integer"},
                        {"type": "number"},
                        {"type": "boolean"},
                    ]
                },
            },
            "required": ["evidence_id", "value"],
            "additionalProperties": False,
        }

    def claim_variant(
        kind: dict[str, Any],
        ids: dict[str, Any],
        values: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "kind": kind,
                "text": {"type": "string", "minLength": 1, "maxLength": 500},
                "evidence_ids": ids,
                "evidence_values": values,
            },
            "required": ["kind", "text", "evidence_ids", "evidence_values"],
            "additionalProperties": False,
        }

    empty_values = {
        "type": "array",
        "items": {"type": "string"},
        "maxItems": 0,
    }
    claim_schema = {
        "anyOf": [
            claim_variant(
                {
                    "type": "string",
                    "enum": [
                        GroundedClaimKind.SUPPORTED_FACT.value,
                        GroundedClaimKind.EVIDENCE_BASED_INFERENCE.value,
                    ],
                },
                evidence_ids(factual_aliases, require_one=True),
                factual_values,
            ),
            claim_variant(
                {
                    "type": "string",
                    "const": GroundedClaimKind.LIMITATION_UNKNOWN.value,
                },
                evidence_ids(gap_aliases),
                empty_values,
            ),
            claim_variant(
                {
                    "type": "string",
                    "const": GroundedClaimKind.GENERAL_GUIDANCE.value,
                },
                evidence_ids([]),
                empty_values,
            ),
        ],
    }
    focus_schema: dict[str, Any]
    if request.focusable_entity_ids:
        focus_schema = {
            "anyOf": [
                {"type": "string", "enum": list(request.focusable_entity_ids)},
                {"type": "null"},
            ]
        }
    else:
        focus_schema = {"type": "null"}
    return {
        "type": "object",
        "properties": {
            "schema_version": {
                "type": "integer",
                "const": GROUNDED_RESPONSE_RESULT_SCHEMA_VERSION,
            },
            "claims": {
                "type": "array",
                "items": claim_schema,
                "minItems": 1,
                "maxItems": 6,
            },
            "focused_entity_id": focus_schema,
        },
        "required": ["schema_version", "claims", "focused_entity_id"],
        "additionalProperties": False,
    }


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
            "response_mode": {
                "type": "string",
                "enum": [item.value for item in ResponseMode],
            },
            "clarification_question": {
                "type": ["string", "null"],
                "maxLength": 300,
            },
        },
        "required": [
            "schema_version",
            "scope",
            "commands",
            "response_strategy",
            "response_mode",
            "clarification_question",
        ],
        "additionalProperties": False,
    }
    return schema
