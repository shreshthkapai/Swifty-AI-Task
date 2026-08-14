"""Provider-neutral semantic dealership command contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
from typing import Any, Mapping, TypeAlias


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


def command_from_dict(data: object) -> HarnessCommand:
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
