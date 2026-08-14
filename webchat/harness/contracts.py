"""Provider-neutral, mutation-free turn planning contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
from typing import Any, Mapping, TypeAlias


TURN_PLAN_SCHEMA_VERSION = 2


class TurnScope(StrEnum):
    IN_DOMAIN = "in_domain"
    DEALERSHIP_ADJACENT = "dealership_adjacent"
    MIXED = "mixed"
    OUT_OF_SCOPE = "out_of_scope"


class ResponseStrategy(StrEnum):
    SEARCH_RESULTS = "search_results"
    VEHICLE_DETAILS = "vehicle_details"
    COMPARISON = "comparison"
    AVAILABILITY_RESULT = "availability_result"
    OFFER_RESULTS = "offer_results"
    SLOT_RESULTS = "slot_results"
    WORKSHOP_DETAILS = "workshop_details"
    BOOKING_DETAILS = "booking_details"
    DEALERSHIP_DETAILS = "dealership_details"
    BUSINESS_INFORMATION = "business_information"
    MISSING_INFORMATION = "missing_information"
    ACTION_PREPARED = "action_prepared"
    GENERAL_GUIDANCE = "general_guidance"
    DOMAIN_REDIRECT = "domain_redirect"
    RECOVERY = "recovery"
    ACKNOWLEDGEMENT = "acknowledgement"


class ResponseMode(StrEnum):
    """Select the response owner without supplying customer-facing prose."""

    GROUNDED_ANSWER = "grounded_answer"
    CLARIFICATION = "clarification"
    ACTION_PREPARED = "action_prepared"
    DETERMINISTIC = "deterministic"


class ReadCommandName(StrEnum):
    SEARCH_VEHICLES = "search_vehicles"
    GET_VEHICLE_DETAILS = "get_vehicle_details"
    COMPARE_VEHICLES = "compare_vehicles"
    CHECK_VEHICLE_AVAILABILITY = "check_vehicle_availability"
    LIST_NEW_CAR_OFFERS = "list_new_car_offers"
    FIND_TEST_DRIVE_SLOTS = "find_test_drive_slots"
    LIST_WORKSHOP_SERVICES = "list_workshop_services"
    LIST_WORKSHOP_LOCATIONS = "list_workshop_locations"
    FIND_WORKSHOP_SLOTS = "find_workshop_slots"
    RETRIEVE_WORKSHOP_BOOKING = "retrieve_workshop_booking"
    LIST_DEALERSHIPS = "list_dealerships"
    GET_DEALERSHIP_DETAILS = "get_dealership_details"
    GET_DEALERSHIP_HOURS = "get_dealership_hours"
    GET_BUSINESS_INFORMATION = "get_business_information"


class PreparationCommandName(StrEnum):
    PREPARE_TEST_DRIVE_BOOKING = "prepare_test_drive_booking"
    PREPARE_SALES_ENQUIRY = "prepare_sales_enquiry"
    PREPARE_VEHICLE_INTEREST = "prepare_vehicle_interest"
    PREPARE_CALLBACK = "prepare_callback"
    PREPARE_PART_EXCHANGE = "prepare_part_exchange"
    PREPARE_WORKSHOP_BOOKING = "prepare_workshop_booking"
    PREPARE_WORKSHOP_AMENDMENT = "prepare_workshop_amendment"
    PREPARE_WORKSHOP_CANCELLATION = "prepare_workshop_cancellation"
    PREPARE_DEALERSHIP_MESSAGE = "prepare_dealership_message"


JsonScalar: TypeAlias = None | bool | int | float | str


@dataclass(frozen=True, slots=True)
class FrozenArray:
    items: tuple[FrozenJson, ...]


@dataclass(frozen=True, slots=True)
class FrozenObject:
    fields: tuple[tuple[str, FrozenJson], ...]


FrozenJson: TypeAlias = JsonScalar | FrozenArray | FrozenObject


def _freeze_json(value: Any, *, path: str) -> FrozenJson:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} numbers must be finite")
        return value
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, (list, tuple)):
        return FrozenArray(
            tuple(_freeze_json(item, path=f"{path}[]") for item in value)
        )
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) or not key for key in value):
            raise ValueError(f"{path} keys must be non-empty strings")
        fields: list[tuple[str, FrozenJson]] = []
        for key in sorted(value):
            fields.append((key, _freeze_json(value[key], path=f"{path}.{key}")))
        return FrozenObject(tuple(fields))
    raise ValueError(f"{path} contains a non-JSON value")


def _thaw_json(value: FrozenJson) -> Any:
    if isinstance(value, FrozenArray):
        return [_thaw_json(item) for item in value.items]
    if isinstance(value, FrozenObject):
        return {key: _thaw_json(item) for key, item in value.fields}
    return value


def _freeze_arguments(arguments: Mapping[str, Any]) -> tuple[tuple[str, FrozenJson], ...]:
    frozen = _freeze_json(arguments, path="arguments")
    if not isinstance(frozen, FrozenObject):
        raise ValueError("arguments must be an object")
    return frozen.fields


def _arguments_to_dict(arguments: tuple[tuple[str, FrozenJson], ...]) -> dict[str, Any]:
    return {key: _thaw_json(value) for key, value in arguments}


def freeze_json_object(value: Mapping[str, Any], *, field: str) -> FrozenObject:
    """Return a canonical immutable JSON object for a harness-owned payload."""
    frozen = _freeze_json(value, path=field)
    if not isinstance(frozen, FrozenObject):
        raise ValueError(f"{field} must be a JSON object")
    return frozen


def thaw_json_object(value: FrozenObject) -> dict[str, Any]:
    """Return ordinary JSON data from a frozen harness payload."""
    if not isinstance(value, FrozenObject):
        raise ValueError("value must be a FrozenObject")
    return {key: _thaw_json(item) for key, item in value.fields}


def validate_frozen_json_object(value: object, *, field: str) -> FrozenObject:
    """Reject manually constructed frozen data that is not canonical JSON."""
    if not isinstance(value, FrozenObject):
        raise ValueError(f"{field} must be canonical frozen JSON")
    try:
        canonical = freeze_json_object(thaw_json_object(value), field=field)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be canonical frozen JSON") from exc
    if canonical != value:
        raise ValueError(f"{field} must be canonical frozen JSON")
    return value


def _validate_frozen_arguments(
    arguments: tuple[tuple[str, FrozenJson], ...],
) -> None:
    try:
        canonical = _freeze_arguments(_arguments_to_dict(arguments))
    except (TypeError, ValueError) as exc:
        raise ValueError("command arguments must be canonical frozen JSON") from exc
    if canonical != arguments:
        raise ValueError("command arguments must be canonical frozen JSON")


@dataclass(frozen=True, slots=True)
class ReadCommand:
    name: ReadCommandName
    arguments: tuple[tuple[str, FrozenJson], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, ReadCommandName):
            raise ValueError("ReadCommand name must be a ReadCommandName")
        _validate_frozen_arguments(self.arguments)

    @classmethod
    def from_mapping(
        cls,
        name: ReadCommandName,
        arguments: Mapping[str, Any],
    ) -> ReadCommand:
        return cls(name=name, arguments=_freeze_arguments(arguments))

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name.value, "arguments": _arguments_to_dict(self.arguments)}


@dataclass(frozen=True, slots=True)
class PreparationCommand:
    name: PreparationCommandName
    arguments: tuple[tuple[str, FrozenJson], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, PreparationCommandName):
            raise ValueError(
                "PreparationCommand name must be a PreparationCommandName"
            )
        _validate_frozen_arguments(self.arguments)

    @classmethod
    def from_mapping(
        cls,
        name: PreparationCommandName,
        arguments: Mapping[str, Any],
    ) -> PreparationCommand:
        return cls(name=name, arguments=_freeze_arguments(arguments))

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name.value, "arguments": _arguments_to_dict(self.arguments)}


HarnessCommand: TypeAlias = ReadCommand | PreparationCommand


def _command_from_dict(data: object) -> HarnessCommand:
    if not isinstance(data, Mapping):
        raise ValueError("each command must be an object")
    unknown = set(data) - {"name", "arguments"}
    if unknown:
        raise ValueError(f"unknown command fields: {sorted(unknown)}")
    name = data.get("name")
    arguments = data.get("arguments", {})
    if not isinstance(name, str):
        raise ValueError("command name must be a string")
    if not isinstance(arguments, Mapping):
        raise ValueError("command arguments must be an object")
    if name in ReadCommandName._value2member_map_:
        return ReadCommand.from_mapping(ReadCommandName(name), arguments)
    if name in PreparationCommandName._value2member_map_:
        return PreparationCommand.from_mapping(PreparationCommandName(name), arguments)
    raise ValueError(f"unknown command: {name}")


@dataclass(frozen=True, slots=True)
class TurnPlan:
    scope: TurnScope
    commands: tuple[HarnessCommand, ...]
    response_strategy: ResponseStrategy
    response_mode: ResponseMode | None = None
    clarification_question: str | None = None
    schema_version: int = TURN_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != TURN_PLAN_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported TurnPlan schema version: {self.schema_version}"
            )
        if not isinstance(self.scope, TurnScope):
            raise ValueError("TurnPlan scope must be a TurnScope")
        if not isinstance(self.response_strategy, ResponseStrategy):
            raise ValueError("TurnPlan response_strategy must be a ResponseStrategy")
        if self.response_mode is None:
            object.__setattr__(
                self,
                "response_mode",
                _default_response_mode(self.response_strategy),
            )
        elif not isinstance(self.response_mode, ResponseMode):
            raise ValueError("TurnPlan response_mode must be a ResponseMode")
        expected_mode = _default_response_mode(self.response_strategy)
        if self.response_mode is not expected_mode:
            raise ValueError(
                f"{self.response_strategy.value} requires {expected_mode.value} mode"
            )
        if not isinstance(self.commands, tuple):
            raise ValueError("TurnPlan commands must be a tuple")
        if len(self.commands) > 2:
            raise ValueError("a TurnPlan may contain at most two commands")
        if not all(
            isinstance(command, (ReadCommand, PreparationCommand))
            for command in self.commands
        ):
            raise ValueError("TurnPlan commands must be HarnessCommand values")
        if sum(isinstance(command, PreparationCommand) for command in self.commands) > 1:
            raise ValueError("a TurnPlan may contain at most one preparation command")
        if self.scope is TurnScope.OUT_OF_SCOPE and self.commands:
            raise ValueError("an out-of-scope TurnPlan cannot contain dealer commands")
        if self.scope is TurnScope.OUT_OF_SCOPE:
            if self.response_strategy is not ResponseStrategy.DOMAIN_REDIRECT:
                raise ValueError("an out-of-scope TurnPlan must use domain_redirect")
            if self.response_mode is not ResponseMode.DETERMINISTIC:
                raise ValueError("an out-of-scope TurnPlan must use deterministic mode")
        elif self.response_strategy is ResponseStrategy.DOMAIN_REDIRECT:
            raise ValueError("domain_redirect is only valid for out-of-scope plans")
        if self.clarification_question is not None:
            if not isinstance(self.clarification_question, str):
                raise ValueError("clarification_question must be a string")
            if not self.clarification_question.strip():
                raise ValueError("clarification_question cannot be blank")
            if self.response_strategy is not ResponseStrategy.MISSING_INFORMATION:
                raise ValueError(
                    "clarification_question requires missing_information strategy"
                )
        elif self.response_strategy is ResponseStrategy.MISSING_INFORMATION:
            raise ValueError("missing_information requires clarification_question")
        if self.response_mode is ResponseMode.CLARIFICATION:
            if self.response_strategy is not ResponseStrategy.MISSING_INFORMATION:
                raise ValueError("clarification mode requires missing_information strategy")
        elif self.response_strategy is ResponseStrategy.MISSING_INFORMATION:
            raise ValueError("missing_information requires clarification mode")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "scope": self.scope.value,
            "commands": [command.to_dict() for command in self.commands],
            "response_strategy": self.response_strategy.value,
            "response_mode": self.response_mode.value,
        }
        if self.clarification_question is not None:
            result["clarification_question"] = self.clarification_question
        return result

    @classmethod
    def from_dict(cls, data: object) -> TurnPlan:
        if not isinstance(data, Mapping):
            raise ValueError("TurnPlan must be an object")
        allowed = {
            "schema_version",
            "scope",
            "commands",
            "response_strategy",
            "response_mode",
            "clarification_question",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unknown TurnPlan fields: {sorted(unknown)}")
        commands = data.get("commands")
        if not isinstance(commands, list):
            raise ValueError("TurnPlan commands must be an array")
        try:
            scope = TurnScope(data.get("scope"))
            strategy = ResponseStrategy(data.get("response_strategy"))
            response_mode = ResponseMode(data.get("response_mode"))
        except (TypeError, ValueError) as exc:
            raise ValueError("TurnPlan contains an unknown scope, strategy, or mode") from exc
        clarification = data.get("clarification_question")
        if clarification is not None and not isinstance(clarification, str):
            raise ValueError("clarification_question must be a string")
        version = data.get("schema_version")
        if not isinstance(version, int):
            raise ValueError("schema_version must be an integer")
        return cls(
            schema_version=version,
            scope=scope,
            commands=tuple(_command_from_dict(command) for command in commands),
            response_strategy=strategy,
            response_mode=response_mode,
            clarification_question=clarification,
        )


def _default_response_mode(strategy: ResponseStrategy) -> ResponseMode:
    if strategy is ResponseStrategy.MISSING_INFORMATION:
        return ResponseMode.CLARIFICATION
    if strategy is ResponseStrategy.ACTION_PREPARED:
        return ResponseMode.ACTION_PREPARED
    if strategy in {
        ResponseStrategy.DOMAIN_REDIRECT,
        ResponseStrategy.RECOVERY,
        ResponseStrategy.ACKNOWLEDGEMENT,
    }:
        return ResponseMode.DETERMINISTIC
    return ResponseMode.GROUNDED_ANSWER
