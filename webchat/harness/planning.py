"""Bounded orchestration from a user turn to a validated provider plan."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from webchat.domain.common import require_aware
from webchat.domain.errors import DealerFailure
from webchat.providers.base import (
    PlanningProvider,
    PlanningRequest,
    PlanningResult,
)

from .context import CompiledContext, ContextCompiler
from .contracts import (
    PreparationCommand,
    PreparationCommandName,
    ReadCommandName,
    ResponseStrategy,
    TurnPlan,
    TurnScope,
)
from .scope import DeterministicRoute, ScopeGate
from .state import ActionReference, ConversationState, PageContext, StructuredMessage
from .tool_gate import ToolGate, ToolSelection, command_spec


MAX_ADJACENT_ADVICE_CHARS = 600
MAX_CLARIFICATION_CHARS = 300


class PlanValidationError(ValueError):
    """A provider proposed a plan outside the planner contract."""


@dataclass(frozen=True, slots=True)
class TurnRequest:
    current_input: str | None
    state: ConversationState
    now: datetime
    recent_messages: tuple[StructuredMessage, ...] = ()
    prior_failures: tuple[DealerFailure, ...] = ()
    action_reference: ActionReference | None = None
    page_observation: PageContext | None = None

    def __post_init__(self) -> None:
        if self.current_input is not None and not isinstance(self.current_input, str):
            raise ValueError("current_input must be a string or None")
        if self.action_reference is not None and self.current_input is not None:
            raise ValueError("a turn accepts either text or an action reference")
        if self.action_reference is None and (
            self.current_input is None or not self.current_input.strip()
        ):
            raise ValueError("a turn requires text or an action reference")
        if not isinstance(self.state, ConversationState):
            raise ValueError("state must be a ConversationState")
        require_aware(self.now, "now")
        if not isinstance(self.recent_messages, tuple) or not all(
            isinstance(item, StructuredMessage) for item in self.recent_messages
        ):
            raise ValueError("recent_messages must contain StructuredMessage values")
        if not isinstance(self.prior_failures, tuple) or not all(
            isinstance(item, DealerFailure) for item in self.prior_failures
        ):
            raise ValueError("prior_failures must contain DealerFailure values")
        if self.action_reference is not None and not isinstance(
            self.action_reference, ActionReference
        ):
            raise ValueError("action_reference must be an ActionReference")
        if self.page_observation is not None and not isinstance(
            self.page_observation,
            PageContext,
        ):
            raise ValueError("page_observation must be a PageContext or None")


@dataclass(frozen=True, slots=True)
class PlanningDecision:
    deterministic_route: DeterministicRoute | None = None
    planning_result: PlanningResult | None = None
    compiled_context: CompiledContext | None = None
    tool_selection: ToolSelection | None = None

    def __post_init__(self) -> None:
        deterministic = self.deterministic_route is not None
        planned = self.planning_result is not None
        if deterministic == planned:
            raise ValueError("a planning decision must be deterministic or provider-planned")
        if deterministic and (self.compiled_context is not None or self.tool_selection is not None):
            raise ValueError("deterministic decisions cannot contain provider context")
        if planned and (self.compiled_context is None or self.tool_selection is None):
            raise ValueError("planned decisions require context and tool selection")


_READ_STRATEGIES = {
    ReadCommandName.SEARCH_VEHICLES.value: ResponseStrategy.SEARCH_RESULTS,
    ReadCommandName.GET_VEHICLE_DETAILS.value: ResponseStrategy.VEHICLE_DETAILS,
    ReadCommandName.COMPARE_VEHICLES.value: ResponseStrategy.COMPARISON,
    ReadCommandName.CHECK_VEHICLE_AVAILABILITY.value: ResponseStrategy.AVAILABILITY_RESULT,
    ReadCommandName.LIST_NEW_CAR_OFFERS.value: ResponseStrategy.OFFER_RESULTS,
    ReadCommandName.FIND_TEST_DRIVE_SLOTS.value: ResponseStrategy.SLOT_RESULTS,
    ReadCommandName.LIST_WORKSHOP_SERVICES.value: ResponseStrategy.WORKSHOP_DETAILS,
    ReadCommandName.LIST_WORKSHOP_LOCATIONS.value: ResponseStrategy.WORKSHOP_DETAILS,
    ReadCommandName.FIND_WORKSHOP_SLOTS.value: ResponseStrategy.SLOT_RESULTS,
    ReadCommandName.RETRIEVE_WORKSHOP_BOOKING.value: ResponseStrategy.BOOKING_DETAILS,
    ReadCommandName.LIST_DEALERSHIPS.value: ResponseStrategy.DEALERSHIP_DETAILS,
    ReadCommandName.GET_DEALERSHIP_DETAILS.value: ResponseStrategy.DEALERSHIP_DETAILS,
    ReadCommandName.GET_DEALERSHIP_HOURS.value: ResponseStrategy.DEALERSHIP_DETAILS,
    ReadCommandName.GET_BUSINESS_INFORMATION.value: ResponseStrategy.BUSINESS_INFORMATION,
}
_COMMAND_REQUIRED_STRATEGIES = frozenset(_READ_STRATEGIES.values()) | {
    ResponseStrategy.ACTION_PREPARED
}


def parse_planning_output(
    value: object,
    *,
    allowed_commands: set[str] | frozenset[str],
) -> TurnPlan:
    try:
        plan = TurnPlan.from_dict(_canonicalize_command_metadata(value))
        validate_turn_plan(plan, allowed_commands=allowed_commands)
        return plan
    except (TypeError, ValueError) as exc:
        raise PlanValidationError(str(exc)) from exc


def _canonicalize_command_metadata(value: object) -> object:
    """Derive non-executing renderer metadata from an otherwise explicit command."""
    if not isinstance(value, Mapping):
        return value
    commands = value.get("commands")
    if not isinstance(commands, list) or not commands:
        return value
    names = [
        item.get("name") if isinstance(item, Mapping) else None
        for item in commands
    ]
    if any(not isinstance(name, str) for name in names):
        return value
    if any(name in PreparationCommandName._value2member_map_ for name in names):
        strategy = ResponseStrategy.ACTION_PREPARED
    else:
        strategy = _READ_STRATEGIES.get(names[0])
        if strategy is None:
            return value
    normalized = dict(value)
    normalized["response_strategy"] = strategy.value
    normalized["clarification_question"] = None
    return normalized


def validate_turn_plan(
    plan: TurnPlan,
    *,
    allowed_commands: set[str] | frozenset[str],
) -> None:
    if not isinstance(plan, TurnPlan):
        raise PlanValidationError("provider result must contain a TurnPlan")
    allowed = set(allowed_commands)
    for command in plan.commands:
        name = command.name.value
        if name not in allowed:
            raise PlanValidationError(f"command is not visible for this turn: {name}")
        _validate_arguments(name, command.to_dict()["arguments"])

    preparation_count = sum(isinstance(command, PreparationCommand) for command in plan.commands)
    if not plan.commands and plan.response_strategy in _COMMAND_REQUIRED_STRATEGIES:
        raise PlanValidationError(
            f"response strategy {plan.response_strategy.value} requires a dealer command"
        )
    if preparation_count:
        if plan.response_strategy is not ResponseStrategy.ACTION_PREPARED:
            raise PlanValidationError("preparation command requires action_prepared response strategy")
    elif plan.response_strategy is ResponseStrategy.ACTION_PREPARED:
        raise PlanValidationError("action_prepared response strategy requires a preparation command")

    if plan.commands and preparation_count == 0:
        expected = _READ_STRATEGIES[plan.commands[0].name.value]
        if plan.response_strategy is not expected:
            raise PlanValidationError(
                f"response strategy {plan.response_strategy.value} does not match primary command"
            )
    if plan.scope is TurnScope.DEALERSHIP_ADJACENT and plan.commands:
        raise PlanValidationError("dealership-adjacent advice cannot use dealer commands")
    if plan.scope is TurnScope.DEALERSHIP_ADJACENT and (
        plan.response_strategy is not ResponseStrategy.ADJACENT_ADVICE
    ):
        raise PlanValidationError("dealership-adjacent scope requires adjacent_advice strategy")
    if plan.scope is TurnScope.MIXED and not plan.commands:
        raise PlanValidationError("mixed scope requires a dealership command")
    if (
        plan.adjacent_advice is not None
        and len(plan.adjacent_advice) > MAX_ADJACENT_ADVICE_CHARS
    ):
        raise PlanValidationError("adjacent_advice exceeds the bounded length")
    if (
        plan.clarification_question is not None
        and len(plan.clarification_question) > MAX_CLARIFICATION_CHARS
    ):
        raise PlanValidationError("clarification_question exceeds the bounded length")


def _validate_arguments(name: str, arguments: Mapping[str, Any]) -> None:
    schema = command_spec(name).argument_schema
    properties = schema["properties"]
    unknown = set(arguments) - set(properties)
    if unknown:
        raise PlanValidationError(f"unknown arguments for {name}: {sorted(unknown)}")
    for field, value in arguments.items():
        field_schema = properties[field]
        allowed_types = field_schema.get("type")
        if isinstance(allowed_types, str):
            allowed_types = [allowed_types]
        if value is None and "null" in allowed_types:
            continue
        expected = [item for item in allowed_types if item != "null"]
        if not _matches_json_type(value, expected):
            raise PlanValidationError(f"argument {name}.{field} has the wrong type")
        if isinstance(value, str) and not value.strip():
            raise PlanValidationError(f"argument {name}.{field} cannot be blank")
        if "enum" in field_schema and value not in field_schema["enum"]:
            raise PlanValidationError(
                f"argument {name}.{field} has an unsupported value"
            )
        if isinstance(value, int) and "minimum" in field_schema and value < field_schema["minimum"]:
            raise PlanValidationError(f"argument {name}.{field} is below its minimum")
        if isinstance(value, list):
            item_type = field_schema.get("items", {}).get("type")
            if item_type and not all(_matches_json_type(item, [item_type]) for item in value):
                raise PlanValidationError(f"argument {name}.{field} contains the wrong item type")


def _matches_json_type(value: Any, expected: list[str]) -> bool:
    return any(
        (
            kind == "string" and isinstance(value, str)
            or kind == "integer" and type(value) is int
            or kind == "number" and type(value) in {int, float}
            or kind == "boolean" and isinstance(value, bool)
            or kind == "array" and isinstance(value, list)
            or kind == "object" and isinstance(value, Mapping)
        )
        for kind in expected
    )


class PlanningEngine:
    def __init__(
        self,
        provider: PlanningProvider,
        *,
        scope_gate: ScopeGate | None = None,
        tool_gate: ToolGate | None = None,
        context_compiler: ContextCompiler | None = None,
    ) -> None:
        if not isinstance(provider, PlanningProvider):
            raise ValueError("provider must implement PlanningProvider")
        self._provider = provider
        self._scope_gate = scope_gate or ScopeGate()
        self._tool_gate = tool_gate or ToolGate()
        self._context_compiler = context_compiler or ContextCompiler()

    async def decide(self, request: TurnRequest) -> PlanningDecision:
        if not isinstance(request, TurnRequest):
            raise ValueError("request must be a TurnRequest")
        route = self._scope_gate.route(
            text=request.current_input,
            state=request.state,
            now=request.now,
            action_reference=request.action_reference,
        )
        if route is not None:
            return PlanningDecision(deterministic_route=route)

        current_input = request.current_input
        if current_input is None:
            raise ValueError("planner-bound turns require current_input")
        selection = self._tool_gate.select(
            state=request.state,
            current_input=current_input,
            now=request.now,
        )
        compiled = self._context_compiler.compile(
            current_input=current_input,
            now=request.now,
            state=request.state,
            tool_selection=selection,
            recent_messages=request.recent_messages,
            prior_failures=request.prior_failures,
            page_observation=request.page_observation,
        )
        provider_request = PlanningRequest(
            context=compiled.serialized,
            commands=selection.specifications,
        )
        result = await self._provider.plan(provider_request)
        if not isinstance(result, PlanningResult):
            raise PlanValidationError("provider returned a non-PlanningResult value")
        validate_turn_plan(
            result.plan,
            allowed_commands=set(selection.included_names),
        )
        return PlanningDecision(
            planning_result=result,
            compiled_context=compiled,
            tool_selection=selection,
        )
