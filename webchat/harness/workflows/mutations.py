"""Confirmed mutation execution with live revalidation and typed recovery."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from webchat.domain.common import Department
from webchat.domain.dealer import DealerAdapter
from webchat.domain.dealerships import ContactMethod, DealershipMessageRequest
from webchat.domain.errors import DealerError
from webchat.domain.sales import (
    CallbackRequest,
    EnquiryType,
    PartExchangeCondition,
    PartExchangeRequest,
    PartExchangeVehicle,
    SalesEnquiryRequest,
    TestDriveBookingRequest,
    TestDriveSlotSearch,
    VehicleInterestRequest,
)
from webchat.domain.workshop import (
    WorkshopBookingAmendment,
    WorkshopBookingRequest,
    WorkshopCancellationRequest,
    WorkshopSlotSearch,
)

from ..actions import PendingActionState, PendingActionType
from ..policy import PolicyCode, PolicyEngine, PolicyError
from ..render import DeclarativeRenderer
from ..state import ConversationState, WorkflowStage
from .common import (
    CommandOutcome,
    customer_from_payload,
    dealer_recovery,
    policy_recovery,
)


async def execute_confirmed_action(
    dealer: DealerAdapter,
    *,
    state: ConversationState,
    now: datetime,
    renderer: DeclarativeRenderer,
    id_factory: Callable[[], str],
    policy: PolicyEngine,
) -> CommandOutcome:
    del id_factory
    action = state.pending_action
    if action is None:
        return CommandOutcome(state, (renderer.notice("There is no action waiting for confirmation.", code="no_pending_action"),))
    if action.state is PendingActionState.SUCCEEDED:
        return CommandOutcome(state, (renderer.notice("That action was already completed.", code="already_completed"),))
    try:
        policy.authorize_execution(action, now=now)
        result = await _execute(dealer, state=state, now=now, policy=policy)
    except PolicyError as exc:
        failed = replace(
            action,
            state=PendingActionState.FAILED,
            attempt_count=action.attempt_count + 1,
        )
        payload = action.request_payload_dict()
        recovery = policy_recovery(
            replace(state, pending_action=failed),
            exc,
            renderer=renderer,
            entity_id=payload.get("vehicle_id"),
        )
        if exc.code is PolicyCode.SLOT_UNAVAILABLE:
            refreshed = await _safe_refresh_slots(dealer, action.action_type, action.request_payload_dict(), renderer, now)
            return CommandOutcome(recovery.state, recovery.blocks + refreshed)
        return recovery
    except DealerError as exc:
        failed = replace(
            action,
            state=PendingActionState.FAILED,
            attempt_count=action.attempt_count + 1,
            last_failure=exc.failure,
        )
        recovery = dealer_recovery(replace(state, pending_action=failed), exc.failure, renderer=renderer)
        if exc.kind.value == "slot_unavailable":
            refreshed = await _safe_refresh_slots(dealer, action.action_type, action.request_payload_dict(), renderer, now)
            return CommandOutcome(recovery.state, recovery.blocks + refreshed)
        return recovery

    succeeded = replace(
        action,
        state=PendingActionState.SUCCEEDED,
        attempt_count=action.attempt_count + 1,
        last_failure=None,
    )
    next_state = replace(
        state,
        pending_action=succeeded,
        workflow=replace(state.workflow, stage=WorkflowStage.COMPLETED, missing_fields=()),
    )
    record = _result_record(result)
    return CommandOutcome(
        next_state,
        (
            renderer.notice("Done. The dealership has confirmed the request.", code="action_succeeded"),
            renderer.records("action_result", (record,), entity_type=action.action_type.value, entity_ids=(record["id"],)),
        ),
    )


async def _execute(dealer: DealerAdapter, *, state: ConversationState, now: datetime, policy: PolicyEngine):
    action = state.pending_action
    payload = action.request_payload_dict()
    customer = customer_from_payload(payload["customer"]) if "customer" in payload else None

    if action.action_type is PendingActionType.TEST_DRIVE_BOOKING:
        availability = await dealer.get_vehicle_availability(payload["vehicle_id"])
        policy.require_test_drive_eligible(availability)
        slots = await dealer.list_test_drive_slots(TestDriveSlotSearch(vehicle_id=payload["vehicle_id"]))
        selected = next((slot for slot in slots if slot.id == payload["slot_id"]), None)
        if selected is None or selected.starts_at <= now:
            raise PolicyError(PolicyCode.SLOT_UNAVAILABLE, next_steps=("reselect_slot",))
        return await dealer.book_test_drive(
            TestDriveBookingRequest(payload["slot_id"], customer, payload.get("notes")),
            idempotency_key=action.idempotency_key,
        )

    if action.action_type is PendingActionType.SALES_ENQUIRY:
        return await dealer.create_sales_enquiry(
            SalesEnquiryRequest(
                payload["dealership_id"], EnquiryType(payload["enquiry_type"]), customer,
                payload["message"], payload.get("vehicle_id"),
            ),
            idempotency_key=action.idempotency_key,
        )

    if action.action_type is PendingActionType.VEHICLE_INTEREST:
        availability = await dealer.get_vehicle_availability(payload["vehicle_id"])
        policy.require_interest_eligible(availability)
        return await dealer.register_vehicle_interest(
            VehicleInterestRequest(payload["vehicle_id"], customer, payload.get("notes")),
            idempotency_key=action.idempotency_key,
        )

    if action.action_type is PendingActionType.CALLBACK:
        return await dealer.request_callback(
            CallbackRequest(
                payload["dealership_id"], Department(payload["department"]), customer,
                payload["reason"], payload.get("preferred_time"), payload.get("vehicle_id"),
            ),
            idempotency_key=action.idempotency_key,
        )

    if action.action_type is PendingActionType.PART_EXCHANGE:
        request = PartExchangeRequest(
            payload["dealership_id"],
            PartExchangeVehicle(payload["registration"], payload["mileage"], PartExchangeCondition(payload["condition"])),
            customer,
        )
        return await dealer.value_part_exchange(request, idempotency_key=action.idempotency_key)

    if action.action_type is PendingActionType.WORKSHOP_BOOKING:
        slots = await dealer.list_workshop_slots(WorkshopSlotSearch())
        selected = next((slot for slot in slots if slot.id == payload["slot_id"]), None)
        if selected is None or selected.starts_at <= now:
            raise PolicyError(PolicyCode.SLOT_UNAVAILABLE, next_steps=("reselect_slot",))
        request = WorkshopBookingRequest(
            payload["slot_id"], customer, payload["registration"], payload["mileage"], payload.get("notes")
        )
        return await dealer.book_workshop(request, idempotency_key=action.idempotency_key)

    booking_id = payload.get("booking_id")
    if action.action_type in {PendingActionType.WORKSHOP_AMENDMENT, PendingActionType.WORKSHOP_CANCELLATION}:
        policy.require_booking_grant(state, booking_id, now=now)
        booking = await dealer.get_workshop_booking(booking_id)
        if action.action_type is PendingActionType.WORKSHOP_AMENDMENT:
            policy.require_booking_amendable(booking)
            if payload.get("slot_id") is not None:
                slots = await dealer.list_workshop_slots(WorkshopSlotSearch(
                    dealership_id=booking.dealership_id, service_type_id=booking.service_type_id
                ))
                selected = next((slot for slot in slots if slot.id == payload["slot_id"]), None)
                if selected is None or selected.starts_at <= now:
                    raise PolicyError(PolicyCode.SLOT_UNAVAILABLE, next_steps=("reselect_slot",))
            return await dealer.amend_workshop_booking(WorkshopBookingAmendment(
                booking_id, payload.get("slot_id"), payload.get("mileage"), payload.get("notes")
            ))
        return await dealer.cancel_workshop_booking(WorkshopCancellationRequest(booking_id))

    if action.action_type is PendingActionType.DEALERSHIP_MESSAGE:
        request = DealershipMessageRequest(
            payload["dealership_id"], Department(payload["department"]),
            payload["subject"], payload["message"], customer,
            ContactMethod(payload["preferred_contact_method"]),
        )
        return await dealer.send_dealership_message(request, idempotency_key=action.idempotency_key)
    raise PolicyError(PolicyCode.INVALID_ACTION_STATE)


async def _refresh_slots(dealer, action_type, payload, renderer, now):
    if action_type is PendingActionType.TEST_DRIVE_BOOKING:
        slots = await dealer.list_test_drive_slots(TestDriveSlotSearch(vehicle_id=payload.get("vehicle_id")))
        future = tuple(item for item in slots if item.starts_at > now)
        records = tuple({"id": item.id, "starts_at": item.starts_at.isoformat(), "label": item.vehicle_label} for item in future)
        ids = tuple(item.id for item in future)
        entity_type = "test_drive_slot"
        action = "select_test_drive_slot"
    elif action_type in {PendingActionType.WORKSHOP_BOOKING, PendingActionType.WORKSHOP_AMENDMENT}:
        slots = await dealer.list_workshop_slots(WorkshopSlotSearch())
        future = tuple(item for item in slots if item.starts_at > now)
        records = tuple({"id": item.id, "starts_at": item.starts_at.isoformat(), "label": item.service_name} for item in future)
        ids = tuple(item.id for item in future)
        entity_type = "workshop_slot"
        action = "select_workshop_slot"
    else:
        return ()
    if not records:
        return ()
    return (renderer.records("slot_choices", records, entity_type=entity_type, entity_ids=ids, action_type=action),)


async def _safe_refresh_slots(dealer, action_type, payload, renderer, now):
    try:
        return await _refresh_slots(dealer, action_type, payload, renderer, now)
    except DealerError:
        return ()


def _result_record(result):
    record = {"id": result.id}
    for field in ("reference", "status", "qualification"):
        if hasattr(result, field):
            value = getattr(result, field)
            record[field] = value.value if hasattr(value, "value") else value
    if hasattr(result, "estimate_low"):
        record["estimate_low"] = result.estimate_low.amount_minor
        record["estimate_high"] = result.estimate_high.amount_minor
        record["currency"] = result.estimate_low.currency
    return record
