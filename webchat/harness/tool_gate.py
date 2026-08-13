"""Semantic command catalogue and policy-driven planner visibility."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import json
import re
from typing import Any, Iterable

from webchat.domain.common import require_aware

from .actions import PendingActionState
from .contracts import (
    FrozenObject,
    PreparationCommandName,
    ReadCommandName,
    freeze_json_object,
    thaw_json_object,
)
from .state import ConversationState, WorkflowDomain


TOOL_GATE_POLICY_VERSION = 1
CommandName = ReadCommandName | PreparationCommandName


class CommandKind(StrEnum):
    READ = "read"
    PREPARATION = "preparation"


class InclusionReason(StrEnum):
    NO_ACTIVE_WORKFLOW = "no_active_workflow"
    CURRENT_DOMAIN = "current_domain"
    SAFE_CROSS_DOMAIN_ENTRY = "safe_cross_domain_entry"
    EXPLICIT_DOMAIN_SIGNAL = "explicit_domain_signal"


class ExclusionReason(StrEnum):
    UNRELATED_DOMAIN = "unrelated_domain"
    PENDING_ACTION_COMPETITION = "pending_action_competition"


@dataclass(frozen=True, slots=True)
class SemanticCommandSpec:
    name: str
    kind: CommandKind
    domains: tuple[WorkflowDomain, ...]
    description: str
    _argument_schema: FrozenObject

    @property
    def argument_schema(self) -> dict[str, Any]:
        return thaw_json_object(self._argument_schema)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "domains": [domain.value for domain in self.domains],
            "description": self.description,
            "argument_schema": self.argument_schema,
        }


def _nullable(kind: str, **extra: Any) -> dict[str, Any]:
    return {"type": [kind, "null"], **extra}


def _array(item_type: str) -> dict[str, Any]:
    return {"type": ["array", "null"], "items": {"type": item_type}}


def _spec(
    name: CommandName,
    domains: tuple[WorkflowDomain, ...],
    description: str,
    properties: dict[str, dict[str, Any]] | None = None,
) -> SemanticCommandSpec:
    schema = {
        "type": "object",
        "properties": properties or {},
        "required": [],
        "additionalProperties": False,
    }
    return SemanticCommandSpec(
        name=name.value,
        kind=(
            CommandKind.READ
            if isinstance(name, ReadCommandName)
            else CommandKind.PREPARATION
        ),
        domains=domains,
        description=description,
        _argument_schema=freeze_json_object(schema, field=f"{name.value}.arguments"),
    )


V = WorkflowDomain.VEHICLES
S = WorkflowDomain.SALES
T = WorkflowDomain.TEST_DRIVE
W = WorkflowDomain.WORKSHOP
D = WorkflowDomain.DEALERSHIP

_VEHICLE_ID = {"vehicle_id": _nullable("string")}
_CUSTOMER = {
    "first_name": _nullable("string"),
    "last_name": _nullable("string"),
    "email": _nullable("string"),
    "phone": _nullable("string"),
}

_CATALOGUE = (
    _spec(
        ReadCommandName.SEARCH_VEHICLES,
        (V, S, T),
        "Search dealership inventory using customer constraints; returned facts remain dealer-authoritative.",
        {
            "query": _nullable("string"),
            "make": _nullable("string"),
            "model": _nullable("string"),
            "fuel_type": _nullable("string"),
            "transmission": _nullable("string"),
            "body_style": _nullable("string"),
            "availability": _nullable("string"),
            "dealership_id": _nullable("string"),
            "min_price_minor": _nullable("integer", minimum=0),
            "max_price_minor": _nullable("integer", minimum=0),
            "currency": _nullable("string"),
            "max_mileage": _nullable("integer", minimum=0),
            "min_year": _nullable("integer"),
            "sort": _nullable("string"),
            "page": _nullable("integer", minimum=1),
            "page_size": _nullable("integer", minimum=1),
        },
    ),
    _spec(ReadCommandName.GET_VEHICLE_DETAILS, (V, S, T), "Get one vehicle's dealer-authored details.", _VEHICLE_ID),
    _spec(ReadCommandName.COMPARE_VEHICLES, (V,), "Compare a bounded set of known vehicle IDs.", {"vehicle_ids": _array("string")}),
    _spec(ReadCommandName.CHECK_VEHICLE_AVAILABILITY, (V, S, T), "Read live vehicle availability and permitted next actions.", _VEHICLE_ID),
    _spec(ReadCommandName.LIST_NEW_CAR_OFFERS, (V, S), "List current new-car offers.", {"make": _nullable("string"), "product_type": _nullable("string")}),
    _spec(
        ReadCommandName.FIND_TEST_DRIVE_SLOTS,
        (T,),
        "Find current test-drive slots for an optional vehicle, dealer and date range.",
        {**_VEHICLE_ID, "dealership_id": _nullable("string"), "date_from": _nullable("string"), "date_to": _nullable("string")},
    ),
    _spec(ReadCommandName.LIST_WORKSHOP_SERVICES, (W,), "List workshop service types and dealer-provided prices."),
    _spec(ReadCommandName.LIST_WORKSHOP_LOCATIONS, (W,), "List dealer locations that provide workshop services."),
    _spec(
        ReadCommandName.FIND_WORKSHOP_SLOTS,
        (W,),
        "Find current workshop slots for a service, dealer and date range.",
        {"dealership_id": _nullable("string"), "service_type_id": _nullable("string"), "date_from": _nullable("string"), "date_to": _nullable("string")},
    ),
    _spec(
        ReadCommandName.RETRIEVE_WORKSHOP_BOOKING,
        (W,),
        "Verify customer identity and retrieve a workshop booking.",
        {"reference": _nullable("string"), "last_name": _nullable("string"), "registration": _nullable("string"), "phone": _nullable("string")},
    ),
    _spec(ReadCommandName.LIST_DEALERSHIPS, (D, S, T, W), "List dealership locations."),
    _spec(ReadCommandName.GET_DEALERSHIP_DETAILS, (D,), "Get contact and location details for one dealership.", {"dealership_id": _nullable("string")}),
    _spec(ReadCommandName.GET_DEALERSHIP_HOURS, (D,), "Get regular and holiday opening hours by department.", {"dealership_id": _nullable("string"), "department": _nullable("string")}),
    _spec(ReadCommandName.GET_BUSINESS_INFORMATION, (D, S), "Get authoritative finance, part-exchange and privacy notices."),
    _spec(
        PreparationCommandName.PREPARE_TEST_DRIVE_BOOKING,
        (T,),
        "Prepare, but never execute, a test-drive booking for confirmation.",
        {"slot_id": _nullable("string"), **_CUSTOMER, "notes": _nullable("string")},
    ),
    _spec(
        PreparationCommandName.PREPARE_SALES_ENQUIRY,
        (S,),
        "Prepare, but never send, a vehicle or general sales enquiry.",
        {"dealership_id": _nullable("string"), "enquiry_type": _nullable("string"), **_CUSTOMER, "message": _nullable("string"), **_VEHICLE_ID},
    ),
    _spec(
        PreparationCommandName.PREPARE_VEHICLE_INTEREST,
        (S,),
        "Prepare interest registration for an eligible reserved vehicle.",
        {**_VEHICLE_ID, **_CUSTOMER, "notes": _nullable("string")},
    ),
    _spec(
        PreparationCommandName.PREPARE_CALLBACK,
        (S, W, D),
        "Prepare a dealership callback request.",
        {"dealership_id": _nullable("string"), "department": _nullable("string"), **_CUSTOMER, "reason": _nullable("string"), "preferred_time": _nullable("string"), **_VEHICLE_ID},
    ),
    _spec(
        PreparationCommandName.PREPARE_PART_EXCHANGE,
        (S,),
        "Prepare a part-exchange valuation request.",
        {"dealership_id": _nullable("string"), **_CUSTOMER, "registration": _nullable("string"), "mileage": _nullable("integer", minimum=0), "condition": _nullable("string")},
    ),
    _spec(
        PreparationCommandName.PREPARE_WORKSHOP_BOOKING,
        (W,),
        "Prepare, but never execute, a workshop booking for confirmation.",
        {"slot_id": _nullable("string"), **_CUSTOMER, "registration": _nullable("string"), "mileage": _nullable("integer", minimum=0), "notes": _nullable("string")},
    ),
    _spec(
        PreparationCommandName.PREPARE_WORKSHOP_AMENDMENT,
        (W,),
        "Prepare an amendment to an authorised workshop booking.",
        {"booking_id": _nullable("string"), "slot_id": _nullable("string"), "mileage": _nullable("integer", minimum=0), "notes": _nullable("string")},
    ),
    _spec(PreparationCommandName.PREPARE_WORKSHOP_CANCELLATION, (W,), "Prepare cancellation of an authorised workshop booking.", {"booking_id": _nullable("string")}),
    _spec(
        PreparationCommandName.PREPARE_DEALERSHIP_MESSAGE,
        (D,),
        "Prepare a message to a dealership department.",
        {"dealership_id": _nullable("string"), "department": _nullable("string"), "subject": _nullable("string"), "message": _nullable("string"), **_CUSTOMER, "preferred_contact_method": _nullable("string")},
    ),
)

_SPEC_BY_NAME = {spec.name: spec for spec in _CATALOGUE}
_SAFE_ENTRY_READS = {
    ReadCommandName.SEARCH_VEHICLES.value,
    ReadCommandName.LIST_NEW_CAR_OFFERS.value,
    ReadCommandName.LIST_WORKSHOP_SERVICES.value,
    ReadCommandName.LIST_WORKSHOP_LOCATIONS.value,
    ReadCommandName.FIND_WORKSHOP_SLOTS.value,
    ReadCommandName.LIST_DEALERSHIPS.value,
    ReadCommandName.GET_BUSINESS_INFORMATION.value,
}
_DOMAIN_SIGNALS = {
    V: re.compile(r"\b(vehicle|car|cars|bmw|mini|suv|hatchback|saloon|automatic|manual|petrol|diesel|electric|budget|cheaper)\b"),
    S: re.compile(
        r"\b(sales|enquiry|callback|call me|part exchange|trade in|valuation|interest)\b"
    ),
    T: re.compile(r"\b(test drive|drive it|try it)\b"),
    W: re.compile(r"\b(workshop|service|servicing|mot|repair|maintenance|tyres?)\b"),
    D: re.compile(r"\b(dealer|dealership|branch|location|opening|hours|contact)\b"),
}


def command_catalogue() -> tuple[SemanticCommandSpec, ...]:
    return _CATALOGUE


def command_spec(name: str) -> SemanticCommandSpec:
    try:
        return _SPEC_BY_NAME[name]
    except KeyError as exc:
        raise ValueError(f"unknown semantic command: {name}") from exc


@dataclass(frozen=True, slots=True)
class IncludedCommand:
    name: str
    reason: InclusionReason

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "reason": self.reason.value}


@dataclass(frozen=True, slots=True)
class ExcludedCommand:
    name: str
    reason: ExclusionReason

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "reason": self.reason.value}


@dataclass(frozen=True, slots=True)
class ToolSelection:
    included: tuple[IncludedCommand, ...]
    excluded: tuple[ExcludedCommand, ...]
    action_controls: tuple[str, ...] = ()
    policy_version: int = TOOL_GATE_POLICY_VERSION

    def __post_init__(self) -> None:
        names = [item.name for item in self.included] + [item.name for item in self.excluded]
        if len(names) != len(set(names)) or set(names) != set(_SPEC_BY_NAME):
            raise ValueError("tool selection must account for every command exactly once")

    @property
    def included_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.included)

    @property
    def specifications(self) -> tuple[SemanticCommandSpec, ...]:
        return tuple(_SPEC_BY_NAME[name] for name in self.included_names)

    def reason_for(self, name: str) -> InclusionReason | ExclusionReason:
        for item in (*self.included, *self.excluded):
            if item.name == name:
                return item.reason
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_gate_policy_version": self.policy_version,
            "included": [item.to_dict() for item in self.included],
            "excluded": [item.to_dict() for item in self.excluded],
            "action_controls": list(self.action_controls),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


class ToolGate:
    """Select planner-visible commands without granting execution authority."""

    def select(
        self,
        *,
        state: ConversationState,
        current_input: str,
        now: datetime,
    ) -> ToolSelection:
        if not isinstance(state, ConversationState):
            raise ValueError("state must be a ConversationState")
        if not isinstance(current_input, str):
            raise ValueError("current_input must be a string")
        require_aware(now, "now")
        active_domain = state.workflow.domain
        signalled_domains = {
            domain
            for domain, pattern in _DOMAIN_SIGNALS.items()
            if pattern.search(current_input.casefold())
        }
        pending = state.pending_action
        pending_is_live = (
            pending is not None
            and not pending.is_expired(now)
            and pending.state
            in {
                PendingActionState.AWAITING_CONFIRMATION,
                PendingActionState.CONFIRMED,
                PendingActionState.EXECUTING,
                PendingActionState.FAILED,
            }
        )

        included: list[IncludedCommand] = []
        excluded: list[ExcludedCommand] = []
        for spec in _CATALOGUE:
            if pending_is_live and spec.kind is CommandKind.PREPARATION:
                excluded.append(
                    ExcludedCommand(spec.name, ExclusionReason.PENDING_ACTION_COMPETITION)
                )
                continue
            reason = self._inclusion_reason(
                spec,
                active_domain=active_domain,
                signalled_domains=signalled_domains,
            )
            if reason is None:
                excluded.append(ExcludedCommand(spec.name, ExclusionReason.UNRELATED_DOMAIN))
            else:
                included.append(IncludedCommand(spec.name, reason))

        controls: tuple[str, ...] = ()
        if pending_is_live:
            if pending.state is PendingActionState.AWAITING_CONFIRMATION:
                controls = ("cancel_pending_action", "confirm_pending_action")
            elif pending.state is PendingActionState.FAILED:
                controls = ("cancel_pending_action", "retry_failed_action")
            elif pending.state is PendingActionState.CONFIRMED:
                controls = ("cancel_pending_action",)
        return ToolSelection(
            included=tuple(sorted(included, key=lambda item: item.name)),
            excluded=tuple(sorted(excluded, key=lambda item: item.name)),
            action_controls=controls,
        )

    @staticmethod
    def _inclusion_reason(
        spec: SemanticCommandSpec,
        *,
        active_domain: WorkflowDomain,
        signalled_domains: Iterable[WorkflowDomain],
    ) -> InclusionReason | None:
        if active_domain is WorkflowDomain.NONE:
            return InclusionReason.NO_ACTIVE_WORKFLOW
        if active_domain in spec.domains:
            return InclusionReason.CURRENT_DOMAIN
        if any(domain in spec.domains for domain in signalled_domains):
            return InclusionReason.EXPLICIT_DOMAIN_SIGNAL
        if spec.name in _SAFE_ENTRY_READS:
            return InclusionReason.SAFE_CROSS_DOMAIN_ENTRY
        return None
