"""Provider-neutral semantic dealership tool catalogue."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from .contracts import (
    FrozenObject,
    PreparationCommandName,
    ReadCommandName,
    freeze_json_object,
    thaw_json_object,
)
from .state import WorkflowDomain


CommandName = ReadCommandName | PreparationCommandName


class CommandKind(StrEnum):
    READ = "read"
    PREPARATION = "preparation"
    CONTROL = "control"


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
    if "enum" in extra and None not in extra["enum"]:
        extra = {**extra, "enum": [*extra["enum"], None]}
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

_DEPARTMENT = _nullable("string", enum=["sales", "service", "parts", "general"])

_VEHICLE_ID = {"vehicle_id": _nullable("string")}
_DEALERSHIP_REFERENCE = {
    "dealership_id": _nullable("string"),
    "dealership_query": _nullable("string"),
}
_CUSTOMER = {
    "first_name": _nullable("string"),
    "last_name": _nullable("string"),
    "email": _nullable("string"),
    "phone": _nullable("string"),
}

_CLEARABLE_VEHICLE_FILTERS = (
    "min_price_minor",
    "max_price_minor",
    "make",
    "model",
    "fuel_type",
    "transmission",
    "body_style",
)

_CATALOGUE = (
    _spec(
        ReadCommandName.SEARCH_VEHICLES,
        (V, S, T),
        "Search dealership inventory using customer constraints; use dealership_query for customer-facing location wording; returned facts remain dealer-authoritative.",
        {
            "query": _nullable("string"),
            "make": _nullable("string"),
            "model": _nullable("string"),
            "fuel_type": _nullable(
                "string", enum=["Petrol", "Diesel", "Hybrid", "Electric"]
            ),
            "transmission": _nullable(
                "string", enum=["Automatic", "Manual"]
            ),
            "body_style": _nullable(
                "string", enum=["SUV", "Hatchback", "Saloon", "Estate"]
            ),
            "availability": _nullable("string", enum=["available", "reserved", "sold"]),
            **_DEALERSHIP_REFERENCE,
            "min_price_minor": _nullable("integer", minimum=0),
            "max_price_minor": _nullable("integer", minimum=0),
            "currency": _nullable("string"),
            "max_mileage": _nullable("integer", minimum=0),
            "min_year": _nullable("integer"),
            "sort": _nullable("string", enum=["newest", "price_asc", "price_desc", "mileage_asc"]),
            "refinement": _nullable("string", enum=["lower_max_price", "cheaper_than_selected"]),
            "clear_filters": {
                "type": ["array", "null"],
                "items": {
                    "type": "string",
                    "enum": list(_CLEARABLE_VEHICLE_FILTERS),
                },
                "maxItems": len(_CLEARABLE_VEHICLE_FILTERS),
            },
            "exclude_vehicle_ids": {
                "type": ["array", "null"],
                "items": {"type": "string"},
                "maxItems": 20,
            },
            "exclude_models": {
                "type": ["array", "null"],
                "items": {"type": "string"},
                "maxItems": 20,
            },
            "exclude_makes": {
                "type": ["array", "null"],
                "items": {"type": "string"},
                "maxItems": 20,
            },
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
        "Find current test-drive slots for an optional vehicle, dealer and date range; use dealership_query for customer-facing location wording.",
        {**_VEHICLE_ID, **_DEALERSHIP_REFERENCE, "date_from": _nullable("string"), "date_to": _nullable("string")},
    ),
    _spec(ReadCommandName.LIST_WORKSHOP_SERVICES, (W,), "List workshop service types and dealer-provided prices."),
    _spec(ReadCommandName.LIST_WORKSHOP_LOCATIONS, (W,), "List dealer locations that provide workshop services."),
    _spec(
        ReadCommandName.FIND_WORKSHOP_SLOTS,
        (W,),
        "Find current workshop slots for a service, dealer and date range; use dealership_query for customer-facing location wording.",
        {**_DEALERSHIP_REFERENCE, "service_type_id": _nullable("string"), "date_from": _nullable("string"), "date_to": _nullable("string")},
    ),
    _spec(
        ReadCommandName.RETRIEVE_WORKSHOP_BOOKING,
        (W,),
        "Verify customer identity and retrieve a workshop booking; copy every newly supplied or corrected identity field from the current input while the handler merges omitted known fields.",
        {"reference": _nullable("string"), "last_name": _nullable("string"), "registration": _nullable("string"), "phone": _nullable("string")},
    ),
    _spec(ReadCommandName.LIST_DEALERSHIPS, (D, S, T, W), "List dealership locations."),
    _spec(ReadCommandName.GET_DEALERSHIP_DETAILS, (D,), "Get contact and location details for one dealership; use dealership_query for customer-facing location wording rather than listing first.", _DEALERSHIP_REFERENCE),
    _spec(ReadCommandName.GET_DEALERSHIP_HOURS, (D,), "Get regular and holiday opening hours by department; use dealership_query for customer-facing location wording rather than listing first.", {**_DEALERSHIP_REFERENCE, "department": _DEPARTMENT}),
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
        "Prepare, but never send, a vehicle or general sales enquiry; capture the customer's stated reason in message and use dealership_query for customer-facing location wording.",
        {**_DEALERSHIP_REFERENCE, "enquiry_type": _nullable("string", enum=["general", "availability", "finance", "part_exchange"]), **_CUSTOMER, "message": _nullable("string"), **_VEHICLE_ID},
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
        "Prepare a dealership callback request; use dealership_query for customer-facing location wording.",
        {**_DEALERSHIP_REFERENCE, "department": _DEPARTMENT, **_CUSTOMER, "reason": _nullable("string"), "preferred_time": _nullable("string"), **_VEHICLE_ID},
    ),
    _spec(
        PreparationCommandName.PREPARE_PART_EXCHANGE,
        (S,),
        "Prepare a part-exchange valuation request; use dealership_query for customer-facing location wording.",
        {**_DEALERSHIP_REFERENCE, **_CUSTOMER, "registration": _nullable("string"), "mileage": _nullable("integer", minimum=0), "condition": _nullable("string", enum=["excellent", "good", "fair"])},
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
        "Use for an explicit amendment request even when verification or change details are missing; the handler enforces authorization and collects omissions. Use slot_ordinal for an ordered fresh slot choice.",
        {"booking_id": _nullable("string"), "slot_id": _nullable("string"), "slot_ordinal": _nullable("integer", minimum=1), "date_from": _nullable("string"), "date_to": _nullable("string"), "mileage": _nullable("integer", minimum=0), "notes": _nullable("string")},
    ),
    _spec(PreparationCommandName.PREPARE_WORKSHOP_CANCELLATION, (W,), "Prepare cancellation of an authorised workshop booking.", {"booking_id": _nullable("string")}),
    _spec(
        PreparationCommandName.PREPARE_DEALERSHIP_MESSAGE,
        (D,),
        "Prepare a message to a dealership department; use dealership_query for customer-facing location wording.",
        {**_DEALERSHIP_REFERENCE, "department": _DEPARTMENT, "subject": _nullable("string"), "message": _nullable("string"), **_CUSTOMER, "preferred_contact_method": _nullable("string", enum=["email", "phone"])},
    ),
)

_SPEC_BY_NAME = {spec.name: spec for spec in _CATALOGUE}

def command_catalogue() -> tuple[SemanticCommandSpec, ...]:
    return _CATALOGUE


def command_spec(name: str) -> SemanticCommandSpec:
    try:
        return _SPEC_BY_NAME[name]
    except KeyError as exc:
        raise ValueError(f"unknown semantic command: {name}") from exc

