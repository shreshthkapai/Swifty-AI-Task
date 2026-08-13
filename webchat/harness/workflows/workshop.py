"""Workshop discovery, booking, verification, amendment, and cancellation workflows."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from webchat.domain.dealer import DealerAdapter
from webchat.domain.errors import DealerError
from webchat.domain.workshop import WorkshopBookingLookup, WorkshopSlotSearch

from ..actions import PendingActionType, PendingRequestType
from ..contracts import PreparationCommandName, ReadCommandName
from ..policy import PolicyCode, PolicyEngine, PolicyError
from ..render import DeclarativeRenderer
from ..state import ConversationState, CustomerState, VerificationGrant, WorkflowDomain, WorkflowStage
from .common import (
    CommandOutcome,
    customer_payload,
    dealer_recovery,
    missing_information,
    parse_date,
    policy_recovery,
    prepare_action,
    presentation_group,
    workflow_state,
)
from .references import resolve_dealership_for_command


async def execute_workshop_read(
    dealer: DealerAdapter,
    name: ReadCommandName,
    arguments: Mapping[str, Any],
    *,
    state: ConversationState,
    now: datetime,
    renderer: DeclarativeRenderer,
    id_factory: Callable[[], str],
) -> CommandOutcome | None:
    if name is ReadCommandName.LIST_WORKSHOP_SERVICES:
        services = await dealer.list_service_types()
        records = tuple({
            "id": item.id, "name": item.name, "description": item.description,
            "duration_minutes": item.duration_minutes,
            "price_from": None if item.price_from is None else {
                "amount_minor": item.price_from.amount_minor, "currency": item.price_from.currency,
            },
        } for item in services)
        return CommandOutcome(
            workflow_state(state, domain=WorkflowDomain.WORKSHOP, stage=WorkflowStage.DISCOVERY),
            (renderer.records("workshop_services", records, entity_type="workshop_service", entity_ids=tuple(item.id for item in services)),),
        )

    if name is ReadCommandName.LIST_WORKSHOP_LOCATIONS:
        locations = await dealer.list_workshop_locations()
        return CommandOutcome(
            workflow_state(state, domain=WorkflowDomain.WORKSHOP, stage=WorkflowStage.DISCOVERY),
            (_locations_block(locations, renderer),),
        )

    if name is ReadCommandName.FIND_WORKSHOP_SLOTS:
        dealership_id, failure = await resolve_dealership_for_command(
            dealer,
            arguments,
            state=state,
            domain=WorkflowDomain.WORKSHOP,
            renderer=renderer,
            required=False,
        )
        if failure is not None:
            return failure
        service_id = arguments.get("service_type_id")
        slots = await dealer.list_workshop_slots(WorkshopSlotSearch(
            dealership_id=dealership_id, service_type_id=service_id,
            date_from=parse_date(arguments.get("date_from"), "date_from"),
            date_to=parse_date(arguments.get("date_to"), "date_to"),
        ))
        slot_dealer_ids = {slot.dealership_id for slot in slots}
        selected_dealer_id = (
            dealership_id
            or (next(iter(slot_dealer_ids)) if len(slot_dealer_ids) == 1 else state.entities.selected_dealer_id)
        )
        next_state = workflow_state(
            replace(
                state,
                entities=replace(state.entities, selected_dealer_id=selected_dealer_id),
            ),
            domain=WorkflowDomain.WORKSHOP,
            stage=WorkflowStage.SELECTING_SLOT,
            gathered={"dealership_id": selected_dealer_id, "service_type_id": service_id},
        )
        if not slots:
            return CommandOutcome(next_state, (renderer.notice("There are no matching workshop slots right now.", code="no_workshop_slots"),))
        records = tuple({
            "id": slot.id, "dealership_id": slot.dealership_id,
            "service_type_id": slot.service_type_id, "starts_at": slot.starts_at.isoformat(),
            "dealership_name": slot.dealership_name, "service_name": slot.service_name,
            "duration_minutes": slot.duration_minutes,
            "price_from": None if slot.price_from is None else {
                "amount_minor": slot.price_from.amount_minor, "currency": slot.price_from.currency,
            },
        } for slot in slots)
        block = renderer.records(
            "slot_choices", records, entity_type="workshop_slot",
            entity_ids=tuple(slot.id for slot in slots), action_type="select_workshop_slot",
        )
        group = presentation_group(
            group_id=id_factory(), entity_type="workshop_slot",
            records=tuple((slot.id, record) for slot, record in zip(slots, records, strict=True)),
            block=block, now=now,
        )
        return CommandOutcome(next_state.with_presentation_group(group), (block,))

    if name is ReadCommandName.RETRIEVE_WORKSHOP_BOOKING:
        fields = ("reference", "last_name", "registration", "phone")
        gathered = state.workflow.gathered_fields_dict()
        values = {
            field: arguments.get(field)
            or getattr(state.customer, field, None)
            or gathered.get(field)
            for field in fields
        }
        missing = tuple(field for field, value in values.items() if not value)
        if missing:
            return missing_information(state, missing, domain=WorkflowDomain.WORKSHOP, renderer=renderer)
        remembered_state = workflow_state(
            replace(
                state,
                customer=replace(
                    state.customer,
                    last_name=values["last_name"],
                    phone=values["phone"],
                    registration=values["registration"],
                ),
            ),
            domain=WorkflowDomain.WORKSHOP,
            stage=WorkflowStage.VERIFYING_IDENTITY,
            gathered={"reference": values["reference"]},
        )
        try:
            details = await dealer.lookup_workshop_booking(WorkshopBookingLookup(**values))
        except DealerError as exc:
            return dealer_recovery(remembered_state, exc.failure, renderer=renderer)
        grant = VerificationGrant.issue(details.booking.id, now=now)
        grants = tuple(item for item in remembered_state.verification_grants if item.booking_id != grant.booking_id) + (grant,)
        next_state = workflow_state(
            replace(
                remembered_state,
                verification_grants=grants,
            ),
            domain=WorkflowDomain.WORKSHOP,
            stage=WorkflowStage.COMPLETED,
            gathered={"booking_id": details.booking.id},
        )
        record = {
            "id": details.booking.id, "reference": details.booking.reference,
            "status": details.booking.status.value, "registration": details.booking.registration,
            "starts_at": details.starts_at.isoformat(), "service": details.service_type_name,
            "dealership_id": details.dealership.id, "dealership_name": details.dealership.name,
        }
        return CommandOutcome(
            next_state,
            (renderer.records("booking_details", (record,), entity_type="workshop_booking", entity_ids=(details.booking.id,)),),
        )
    return None


async def execute_workshop_preparation(
    dealer: DealerAdapter,
    name: PreparationCommandName,
    arguments: Mapping[str, Any],
    *,
    state: ConversationState,
    now: datetime,
    renderer: DeclarativeRenderer,
    id_factory: Callable[[], str],
    policy: PolicyEngine,
) -> CommandOutcome | None:
    if name is PreparationCommandName.PREPARE_WORKSHOP_BOOKING:
        slot_id = arguments.get("slot_id") or state.entities.selected_workshop_slot_id
        registration = arguments.get("registration") or state.customer.registration
        mileage = arguments.get("mileage")
        missing = tuple(key for key, value in (("slot_id", slot_id), ("registration", registration), ("mileage", mileage)) if value is None or value == "")
        if missing:
            return missing_information(state, missing, domain=WorkflowDomain.WORKSHOP, renderer=renderer)
        try:
            customer = policy.customer_identity(arguments, state=state)
        except PolicyError as exc:
            if exc.code in {PolicyCode.MISSING_CUSTOMER_DETAILS, PolicyCode.INVALID_CUSTOMER_DETAILS}:
                return missing_information(state, exc.fields, domain=WorkflowDomain.WORKSHOP, renderer=renderer)
            return policy_recovery(state, exc, renderer=renderer)
        state = replace(
            state,
            customer=CustomerState(customer.first_name, customer.last_name, customer.email, customer.phone, registration),
        )
        return prepare_action(
            state, action_type=PendingActionType.WORKSHOP_BOOKING,
            request_type=PendingRequestType.WORKSHOP_BOOKING,
            payload={"slot_id": slot_id, "customer": customer_payload(customer), "registration": registration, "mileage": mileage, "notes": arguments.get("notes")},
            now=now, id_factory=id_factory, renderer=renderer, domain=WorkflowDomain.WORKSHOP,
        )

    if name is PreparationCommandName.PREPARE_WORKSHOP_AMENDMENT:
        booking_id = arguments.get("booking_id")
        if not booking_id:
            return missing_information(state, ("booking_id",), domain=WorkflowDomain.WORKSHOP, renderer=renderer)
        try:
            policy.require_booking_grant(state, booking_id, now=now)
        except PolicyError as exc:
            return policy_recovery(state, exc, renderer=renderer)
        booking = await dealer.get_workshop_booking(booking_id)
        try:
            policy.require_booking_amendable(booking)
        except PolicyError as exc:
            return policy_recovery(state, exc, renderer=renderer)
        slot_id = arguments.get("slot_id") or state.entities.selected_workshop_slot_id
        if slot_id is None and arguments.get("mileage") is None and arguments.get("notes") is None:
            return missing_information(state, ("slot_id_or_booking_change",), domain=WorkflowDomain.WORKSHOP, renderer=renderer)
        return prepare_action(
            state, action_type=PendingActionType.WORKSHOP_AMENDMENT,
            request_type=PendingRequestType.WORKSHOP_AMENDMENT,
            payload={"booking_id": booking_id, "slot_id": slot_id, "mileage": arguments.get("mileage"), "notes": arguments.get("notes")},
            now=now, id_factory=id_factory, renderer=renderer, domain=WorkflowDomain.WORKSHOP,
        )

    if name is PreparationCommandName.PREPARE_WORKSHOP_CANCELLATION:
        booking_id = arguments.get("booking_id")
        if not booking_id:
            return missing_information(state, ("booking_id",), domain=WorkflowDomain.WORKSHOP, renderer=renderer)
        try:
            policy.require_booking_grant(state, booking_id, now=now)
        except PolicyError as exc:
            return policy_recovery(state, exc, renderer=renderer)
        booking = await dealer.get_workshop_booking(booking_id)
        if booking.status.value == "cancelled":
            return CommandOutcome(state, (renderer.notice("That workshop booking is already cancelled.", code="booking_cancelled"),))
        return prepare_action(
            state, action_type=PendingActionType.WORKSHOP_CANCELLATION,
            request_type=PendingRequestType.WORKSHOP_CANCELLATION,
            payload={"booking_id": booking_id}, now=now, id_factory=id_factory,
            renderer=renderer, domain=WorkflowDomain.WORKSHOP,
        )
    return None


def _locations_block(locations, renderer):
    records = tuple({
        "id": item.id, "name": item.name, "address": [*item.address.lines, item.address.town, item.address.postcode],
        "phone": item.phone, "email": item.email, "brands": list(item.brands),
    } for item in locations)
    return renderer.records("dealerships", records, entity_type="dealership", entity_ids=tuple(item.id for item in locations), action_type="select_dealership")
