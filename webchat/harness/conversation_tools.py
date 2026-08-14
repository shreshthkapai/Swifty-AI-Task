"""Static semantic tools for the conversational runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, TypeAlias

from .conversation import ConversationToolCall
from .contracts import (
    PreparationCommand,
    PreparationCommandName,
    ReadCommand,
    ReadCommandName,
    freeze_json_object,
)
from .tools import (
    CommandKind,
    SemanticCommandSpec,
    command_catalogue,
    command_spec,
)


class ConversationControlName(StrEnum):
    CONFIRM_PENDING_ACTION = "confirm_pending_action"
    CANCEL_PENDING_ACTION = "cancel_pending_action"
    RETRY_PENDING_ACTION = "retry_pending_action"
    SHOW_MORE_RESULTS = "show_more_results"
    START_OVER = "start_over"
    SELECT_PRESENTED_ENTITY = "select_presented_entity"


@dataclass(frozen=True, slots=True)
class ConversationControl:
    name: ConversationControlName
    arguments: tuple[tuple[str, Any], ...] = ()

    def arguments_dict(self) -> dict[str, Any]:
        return dict(self.arguments)


ConversationCommand: TypeAlias = ReadCommand | PreparationCommand | ConversationControl


def _control_spec(
    name: ConversationControlName,
    description: str,
    properties: Mapping[str, Mapping[str, Any]] | None = None,
) -> SemanticCommandSpec:
    return SemanticCommandSpec(
        name=name.value,
        kind=CommandKind.CONTROL,
        domains=(),
        description=description,
        _argument_schema=freeze_json_object(
            {
                "type": "object",
                "properties": dict(properties or {}),
                "required": [],
                "additionalProperties": False,
            },
            field=f"{name.value}.arguments",
        ),
    )


_CONTROL_SPECS = (
    _control_spec(
        ConversationControlName.CONFIRM_PENDING_ACTION,
        "Confirm the single pending action only when the customer clearly agrees.",
    ),
    _control_spec(
        ConversationControlName.CANCEL_PENDING_ACTION,
        "Cancel the single pending action only when the customer clearly declines it.",
    ),
    _control_spec(
        ConversationControlName.RETRY_PENDING_ACTION,
        "Retry a failed pending action when the customer explicitly asks to retry.",
    ),
    _control_spec(
        ConversationControlName.SHOW_MORE_RESULTS,
        "Show the next page of the most recent vehicle results.",
    ),
    _control_spec(
        ConversationControlName.START_OVER,
        "Clear the active workflow when the customer explicitly asks to start over.",
    ),
    _control_spec(
        ConversationControlName.SELECT_PRESENTED_ENTITY,
        "Select one entity from the most recently presented ordered group.",
        {"ordinal": {"type": "integer", "minimum": 1}},
    ),
)
_CONTROL_BY_NAME = {item.name: item for item in _CONTROL_SPECS}


def conversation_tool_catalogue() -> tuple[SemanticCommandSpec, ...]:
    return command_catalogue() + _CONTROL_SPECS


def conversation_command(call: ConversationToolCall) -> ConversationCommand:
    arguments = call.arguments_dict()
    if call.name in _CONTROL_BY_NAME:
        _validate_arguments(call.name, arguments, _CONTROL_BY_NAME[call.name])
        return ConversationControl(
            ConversationControlName(call.name),
            tuple(sorted(arguments.items())),
        )
    spec = command_spec(call.name)
    _validate_arguments(call.name, arguments, spec)
    if call.name in ReadCommandName._value2member_map_:
        return ReadCommand.from_mapping(ReadCommandName(call.name), arguments)
    if call.name in PreparationCommandName._value2member_map_:
        return PreparationCommand.from_mapping(
            PreparationCommandName(call.name),
            arguments,
        )
    raise ValueError(f"unsupported semantic tool: {call.name}")


def _validate_arguments(
    name: str,
    arguments: Mapping[str, Any],
    spec: SemanticCommandSpec,
) -> None:
    properties = spec.argument_schema["properties"]
    unknown = set(arguments) - set(properties)
    if unknown:
        raise ValueError(f"unknown arguments for {name}: {sorted(unknown)}")
    for field, value in arguments.items():
        field_schema = properties[field]
        allowed_types = field_schema.get("type")
        if isinstance(allowed_types, str):
            allowed_types = [allowed_types]
        if value is None and "null" in allowed_types:
            continue
        expected = [item for item in allowed_types if item != "null"]
        if not _matches_json_type(value, expected):
            raise ValueError(f"argument {name}.{field} has the wrong type")
        if isinstance(value, str) and not value.strip():
            raise ValueError(f"argument {name}.{field} cannot be blank")
        if "enum" in field_schema and value not in field_schema["enum"]:
            raise ValueError(f"argument {name}.{field} has an unsupported value")
        if (
            type(value) is int
            and "minimum" in field_schema
            and value < field_schema["minimum"]
        ):
            raise ValueError(f"argument {name}.{field} is below its minimum")
        if isinstance(value, list):
            item_schema = field_schema.get("items", {})
            item_type = item_schema.get("type")
            if item_type and not all(
                _matches_json_type(item, [item_type]) for item in value
            ):
                raise ValueError(
                    f"argument {name}.{field} contains the wrong item type"
                )
            item_enum = item_schema.get("enum")
            if item_enum and any(item not in item_enum for item in value):
                raise ValueError(
                    f"argument {name}.{field} contains an unsupported value"
                )
            if len(value) > field_schema.get("maxItems", len(value)):
                raise ValueError(f"argument {name}.{field} has too many items")


def _matches_json_type(value: Any, expected: list[str]) -> bool:
    return any(
        kind == "string" and isinstance(value, str)
        or kind == "integer" and type(value) is int
        or kind == "number" and type(value) in {int, float}
        or kind == "boolean" and isinstance(value, bool)
        or kind == "array" and isinstance(value, list)
        or kind == "object" and isinstance(value, Mapping)
        for kind in expected
    )
