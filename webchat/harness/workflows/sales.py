"""Sales, test-drive, callback, interest, and part-exchange workflows."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from webchat.domain.common import Department
from webchat.domain.dealer import DealerAdapter
from webchat.domain.sales import (
    EnquiryType,
    PartExchangeCondition,
    TestDriveSlotSearch,
)

from ..actions import PendingActionType, PendingRequestType
from ..contracts import PreparationCommandName, ReadCommandName
from ..policy import PolicyCode, PolicyEngine, PolicyError
from ..render import DeclarativeRenderer
from ..state import ConversationState, CustomerState, WorkflowDomain, WorkflowStage
from .common import (
    CommandOutcome,
    customer_payload,
    enum_value,
    missing_information,
    parse_date,
    policy_recovery,
    prepare_action,
    presentation_group,
    workflow_state,
)
from .references import resolve_dealership_for_command


async def execute_sales_read(
    dealer: DealerAdapter,
    name: ReadCommandName,
    arguments: Mapping[str, Any],
    *,
    state: ConversationState,
    now: datetime,
    renderer: DeclarativeRenderer,
    id_factory: Callable[[], str],
    policy: PolicyEngine,
) -> CommandOutcome | None:
    if name is not ReadCommandName.FIND_TEST_DRIVE_SLOTS:
        return None
    vehicle_id = arguments.get("vehicle_id") or state.entities.selected_vehicle_id or state.context.page_vehicle_id
    dealership_id, failure = await resolve_dealership_for_command(
        dealer,
        arguments,
        state=state,
        domain=WorkflowDomain.TEST_DRIVE,
        renderer=renderer,
        required=False,
    )
    if failure is not None:
        return failure
    if vehicle_id is not None:
        availability = await dealer.get_vehicle_availability(vehicle_id)
        try:
            policy.require_test_drive_eligible(availability)
        except PolicyError as exc:
            return policy_recovery(
                state,
                exc,
                renderer=renderer,
                entity_id=vehicle_id,
            )
    slots = await dealer.list_test_drive_slots(
        TestDriveSlotSearch(
            vehicle_id=vehicle_id,
            dealership_id=dealership_id,
            date_from=parse_date(arguments.get("date_from"), "date_from"),
            date_to=parse_date(arguments.get("date_to"), "date_to"),
        )
    )
    slot_vehicle_ids = {slot.vehicle_id for slot in slots}
    slot_dealer_ids = {slot.dealership_id for slot in slots}
    selected_vehicle_id = (
        vehicle_id
        or (next(iter(slot_vehicle_ids)) if len(slot_vehicle_ids) == 1 else state.entities.selected_vehicle_id)
    )
    selected_dealer_id = (
        dealership_id
        or (next(iter(slot_dealer_ids)) if len(slot_dealer_ids) == 1 else state.entities.selected_dealer_id)
    )
    next_state = workflow_state(
        replace(
            state,
            entities=replace(
                state.entities,
                selected_vehicle_id=selected_vehicle_id,
                selected_dealer_id=selected_dealer_id,
            ),
        ),
        domain=WorkflowDomain.TEST_DRIVE,
        stage=WorkflowStage.SELECTING_SLOT,
        gathered={"vehicle_id": selected_vehicle_id, "dealership_id": selected_dealer_id},
    )
    if not slots:
        return CommandOutcome(
            next_state,
            (renderer.notice("There are no matching test-drive slots right now.", code="no_test_drive_slots"),),
        )
    records = tuple(
        {
            "id": slot.id, "vehicle_id": slot.vehicle_id,
            "dealership_id": slot.dealership_id, "dealership_name": slot.dealership_name,
            "vehicle_label": slot.vehicle_label, "starts_at": slot.starts_at.isoformat(),
        }
        for slot in slots
    )
    block = renderer.records(
        "slot_choices", records, entity_type="test_drive_slot",
        entity_ids=tuple(slot.id for slot in slots), action_type="select_test_drive_slot",
    )
    group = presentation_group(
        group_id=id_factory(), entity_type="test_drive_slot",
        records=tuple((slot.id, record) for slot, record in zip(slots, records, strict=True)),
        block=block, now=now,
    )
    return CommandOutcome(next_state.with_presentation_group(group), (block,))


async def execute_sales_preparation(
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
    if name is PreparationCommandName.PREPARE_TEST_DRIVE_BOOKING:
        slot_id = arguments.get("slot_id") or state.entities.selected_test_drive_slot_id
        vehicle_id = arguments.get("vehicle_id") or state.entities.selected_vehicle_id or state.context.page_vehicle_id
        missing = tuple(key for key, value in (("slot_id", slot_id), ("vehicle_id", vehicle_id)) if not value)
        if missing:
            return missing_information(state, missing, domain=WorkflowDomain.TEST_DRIVE, renderer=renderer)
        customer, failure = _customer(policy, arguments, state, WorkflowDomain.TEST_DRIVE, renderer)
        if failure:
            return failure
        availability = await dealer.get_vehicle_availability(vehicle_id)
        try:
            policy.require_test_drive_eligible(availability)
        except PolicyError as exc:
            return policy_recovery(state, exc, renderer=renderer, entity_id=vehicle_id)
        state = _remember_customer(state, customer)
        return prepare_action(
            state, action_type=PendingActionType.TEST_DRIVE_BOOKING,
            request_type=PendingRequestType.TEST_DRIVE_BOOKING,
            payload={"slot_id": slot_id, "vehicle_id": vehicle_id, "customer": customer_payload(customer), "notes": arguments.get("notes")},
            now=now, id_factory=id_factory, renderer=renderer, domain=WorkflowDomain.TEST_DRIVE,
        )

    if name is PreparationCommandName.PREPARE_SALES_ENQUIRY:
        dealership_id, resolution_failure = await resolve_dealership_for_command(
            dealer,
            arguments,
            state=state,
            domain=WorkflowDomain.SALES,
            renderer=renderer,
            required=True,
        )
        if resolution_failure is not None:
            return resolution_failure
        message = arguments.get("message")
        missing = tuple(key for key, value in (("dealership_id", dealership_id), ("message", message)) if not value)
        if missing:
            return missing_information(state, missing, domain=WorkflowDomain.SALES, renderer=renderer)
        customer, failure = _customer(policy, arguments, state, WorkflowDomain.SALES, renderer)
        if failure:
            return failure
        state = _remember_customer(state, customer)
        outcome = prepare_action(
            state, action_type=PendingActionType.SALES_ENQUIRY,
            request_type=PendingRequestType.SALES_ENQUIRY,
            payload={
                "dealership_id": dealership_id,
                "enquiry_type": enum_value(EnquiryType, arguments.get("enquiry_type"), "enquiry_type", EnquiryType.GENERAL).value,
                "customer": customer_payload(customer), "message": message,
                "vehicle_id": arguments.get("vehicle_id") or state.entities.selected_vehicle_id,
            },
            now=now, id_factory=id_factory, renderer=renderer, domain=WorkflowDomain.SALES,
        )
        if enum_value(
            EnquiryType,
            arguments.get("enquiry_type"),
            "enquiry_type",
            EnquiryType.GENERAL,
        ) is EnquiryType.FINANCE:
            return await _with_business_information(
                outcome,
                dealer=dealer,
                renderer=renderer,
            )
        return outcome

    if name is PreparationCommandName.PREPARE_VEHICLE_INTEREST:
        vehicle_id = arguments.get("vehicle_id") or state.entities.selected_vehicle_id or state.context.page_vehicle_id
        if not vehicle_id:
            return missing_information(state, ("vehicle_id",), domain=WorkflowDomain.SALES, renderer=renderer)
        customer, failure = _customer(policy, arguments, state, WorkflowDomain.SALES, renderer)
        if failure:
            return failure
        availability = await dealer.get_vehicle_availability(vehicle_id)
        try:
            policy.require_interest_eligible(availability)
        except PolicyError as exc:
            return policy_recovery(state, exc, renderer=renderer, entity_id=vehicle_id)
        state = _remember_customer(state, customer)
        return prepare_action(
            state, action_type=PendingActionType.VEHICLE_INTEREST,
            request_type=PendingRequestType.VEHICLE_INTEREST,
            payload={"vehicle_id": vehicle_id, "customer": customer_payload(customer), "notes": arguments.get("notes")},
            now=now, id_factory=id_factory, renderer=renderer, domain=WorkflowDomain.SALES,
        )

    if name is PreparationCommandName.PREPARE_CALLBACK:
        dealership_id, resolution_failure = await resolve_dealership_for_command(
            dealer,
            arguments,
            state=state,
            domain=WorkflowDomain.DEALERSHIP,
            renderer=renderer,
            required=True,
        )
        if resolution_failure is not None:
            return resolution_failure
        reason = arguments.get("reason")
        missing = tuple(key for key, value in (("dealership_id", dealership_id), ("reason", reason)) if not value)
        if missing:
            return missing_information(state, missing, domain=WorkflowDomain.SALES, renderer=renderer)
        customer, failure = _customer(policy, arguments, state, WorkflowDomain.SALES, renderer)
        if failure:
            return failure
        state = _remember_customer(state, customer)
        department = enum_value(
            Department,
            arguments.get("department"),
            "department",
            Department.GENERAL,
        )
        callback_domain = {
            Department.SALES: WorkflowDomain.SALES,
            Department.SERVICE: WorkflowDomain.WORKSHOP,
            Department.PARTS: WorkflowDomain.DEALERSHIP,
            Department.GENERAL: WorkflowDomain.DEALERSHIP,
        }[department]
        outcome = prepare_action(
            state, action_type=PendingActionType.CALLBACK,
            request_type=PendingRequestType.CALLBACK,
            payload={
                "dealership_id": dealership_id,
                "department": department.value,
                "customer": customer_payload(customer), "reason": reason,
                "preferred_time": arguments.get("preferred_time"),
                "vehicle_id": arguments.get("vehicle_id") or state.entities.selected_vehicle_id,
            },
            now=now, id_factory=id_factory, renderer=renderer, domain=callback_domain,
        )
        if arguments.get("preferred_time"):
            return CommandOutcome(
                outcome.state,
                outcome.blocks
                + (
                    renderer.notice(
                        "The requested callback time is a preference, not a guaranteed appointment.",
                        code="callback_timing",
                    ),
                ),
            )
        return outcome

    if name is PreparationCommandName.PREPARE_PART_EXCHANGE:
        dealership_id, resolution_failure = await resolve_dealership_for_command(
            dealer,
            arguments,
            state=state,
            domain=WorkflowDomain.SALES,
            renderer=renderer,
            required=True,
        )
        if resolution_failure is not None:
            return resolution_failure
        registration = arguments.get("registration") or state.customer.registration
        mileage = arguments.get("mileage")
        condition = arguments.get("condition")
        missing = tuple(key for key, value in (
            ("dealership_id", dealership_id), ("registration", registration),
            ("mileage", mileage), ("condition", condition),
        ) if value is None or value == "")
        if missing:
            return missing_information(state, missing, domain=WorkflowDomain.SALES, renderer=renderer)
        customer, failure = _customer(policy, arguments, state, WorkflowDomain.SALES, renderer)
        if failure:
            return failure
        state = _remember_customer(state, customer, registration=registration)
        outcome = prepare_action(
            state, action_type=PendingActionType.PART_EXCHANGE,
            request_type=PendingRequestType.PART_EXCHANGE,
            payload={
                "dealership_id": dealership_id, "customer": customer_payload(customer),
                "registration": registration, "mileage": mileage,
                "condition": enum_value(PartExchangeCondition, condition, "condition").value,
            },
            now=now, id_factory=id_factory, renderer=renderer, domain=WorkflowDomain.SALES,
        )
        return await _with_business_information(
            outcome,
            dealer=dealer,
            renderer=renderer,
        )
    return None


def _customer(
    policy: PolicyEngine,
    arguments: Mapping[str, Any],
    state: ConversationState,
    domain: WorkflowDomain,
    renderer: DeclarativeRenderer,
):
    try:
        return policy.customer_identity(arguments, state=state), None
    except PolicyError as exc:
        if exc.code in {PolicyCode.MISSING_CUSTOMER_DETAILS, PolicyCode.INVALID_CUSTOMER_DETAILS}:
            return None, missing_information(state, exc.fields, domain=domain, renderer=renderer)
        return None, policy_recovery(state, exc, renderer=renderer)


def _remember_customer(state: ConversationState, customer, *, registration: str | None = None):
    return replace(
        state,
        customer=CustomerState(
            first_name=customer.first_name, last_name=customer.last_name,
            email=customer.email, phone=customer.phone,
            registration=registration or state.customer.registration,
        ),
    )


async def _with_business_information(
    outcome: CommandOutcome,
    *,
    dealer: DealerAdapter,
    renderer: DeclarativeRenderer,
) -> CommandOutcome:
    info = await dealer.get_business_information()
    record = {
        "organisation": info.organisation,
        "currency": info.currency,
        "market": info.market,
        "finance_notice": info.finance_notice,
        "finance_minimum_age": info.finance_minimum_age,
        "part_exchange_notice": info.part_exchange_notice,
        "privacy_contact": info.privacy_contact,
    }
    block = renderer.records(
        "business_information",
        (record,),
        entity_type="business_information",
        entity_ids=(info.organisation,),
    )
    return CommandOutcome(outcome.state, outcome.blocks + (block,))
