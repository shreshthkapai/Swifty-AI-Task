"""Translate real runtime evidence into the corpus's stable scoring vocabulary."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from evals.fixtures import DealerSnapshot
from evals.run import ObservedAnswer, ObservedTurn
from webchat.harness.actions import PendingActionState, PendingActionType
from evals.schema import ResponseStrategy
from webchat.harness.contracts import HarnessCommand
from webchat.harness.evidence import EvidenceItem
from webchat.harness.turn import TurnResult
from webchat.harness.state import ConversationState, WorkflowDomain


OBSERVABLE_BLOCK_TYPES = frozenset({
    "vehicle_cards", "comparison", "notice", "text", "actions", "offer_cards",
    "link", "slot_choices", "confirmation", "location_cards", "availability",
    "vehicle_details", "workshop_services", "dealerships", "booking_details",
    "opening_hours", "business_information", "action_result",
})
OBSERVABLE_NOTICE_KEYS = frozenset({"finance", "part_exchange", "privacy"})
OBSERVABLE_FACTS = frozenset({
    "result_count", "constraints_applied", "dealer_vehicle_details", "price",
    "mileage", "fuel", "transmission", "price_on_request", "zero_results",
    "published_offer_terms", "lower_priced_results", "dealer_stock_only",
    "stable_vehicle_link", "vehicle_status:available", "vehicle_status:reserved",
    "vehicle_status:sold", "live_slots", "invalid_phone",
    "vehicle_slot_customer_summary", "booking_reference", "status:confirmed",
    "slot_unavailable", "refreshed_slots", "original_booking_reference",
    "idempotency_conflict", "callback_time_preference_only", "service_names",
    "service_durations", "workshop_locations", "live_workshop_slots",
    "zero_slots_valid_availability", "missing_customer_fields",
    "service_location_slot_customer_summary", "workshop_reference",
    "appointment_time", "service_name", "dealership", "status",
    "booking_not_found", "old_and_new_slot", "verification_required",
    "status:cancelled", "already_cancelled", "old_action_superseded",
    "new_slot_summary", "updated_appointment_time", "addresses",
    "sales_saturday_hours", "holiday_reduced_sales_hours", "holiday_date",
    "holiday_service_closed", "parts_phone", "department_and_message_summary",
    "callback_request_summary", "published_privacy_information",
    "selected_second_presented_vehicle", "lower_than_selected_price",
    "resolved_persisted_vehicle", "location_changed", "next_result_page",
    "selected_slot_summary", "action_cancelled", "family_vehicle_considerations",
    "published_boot_space_or_dimensions", "vehicle_results",
    "hours_temporarily_unavailable",
})
OBSERVABLE_NEXT_STEPS = frozenset({
    "refine_or_select", "select_vehicle", "availability_or_test_drive",
    "sales_enquiry", "clarify_price_direction", "relax_constraints",
    "compare_space", "test_drive_or_enquiry", "register_interest", "select_slot",
    "provide_valid_phone", "confirm_or_cancel", "select_new_slot",
    "start_new_request", "choose_service", "choose_location",
    "later_dates_or_other_location", "provide_customer_details", "amend_or_cancel",
    "recheck_all_identity_fields", "provide_lookup_identity", "start_new_booking",
    "confirm_or_keep", "choose_another_date", "offer_stock_search",
    "compare_telescope_dimensions", "retry_hours",
})


def _append(values: list[str], *items: str) -> None:
    for item in items:
        if item and item not in values:
            values.append(item)


def _state_projection(state: ConversationState) -> dict[str, Any]:
    pending = state.pending_action
    if pending is None or pending.state in {PendingActionState.SUCCEEDED, PendingActionState.CANCELLED}:
        pending_value: str | None = None
    elif pending.state is PendingActionState.FAILED:
        pending_value = "needs_reselection"
    else:
        pending_value = {
            PendingActionType.PART_EXCHANGE: "part_exchange_valuation",
        }.get(pending.action_type, pending.action_type.value)
    grants = [grant.booking_id for grant in state.verification_grants]
    return {
        "customer": state.customer.to_dict(),
        "context": {"selected_vehicle": state.entities.selected_vehicle_id},
        "preferences": {
            "make": state.preferences.makes[0] if state.preferences.makes else None,
            "transmission": state.preferences.transmission,
            "max_price_minor": state.preferences.maximum_price_minor,
            "price_refinement": (
                "cheaper" if state.preferences.maximum_price_minor is not None else None
            ),
        },
        "selected_vehicle": state.entities.selected_vehicle_id,
        "selected_dealer": state.entities.selected_dealer_id,
        "selected_slot": (
            state.entities.selected_test_drive_slot_id
            or state.entities.selected_workshop_slot_id
        ),
        "pending_action": pending_value,
        "workflow": state.workflow.domain.value,
        "verification_grant": grants[-1] if grants else None,
    }


def _payloads(result: TurnResult) -> list[tuple[str, dict[str, Any]]]:
    return [(block.kind, block.to_dict()["payload"]) for block in result.blocks]


def _state_changes(before: ConversationState, after: ConversationState) -> dict[str, Any]:
    before_data = before.to_dict()
    after_data = after.to_dict()
    return {
        key: {"before": before_data.get(key), "after": after_data.get(key)}
        for key in sorted(set(before_data) | set(after_data))
        if before_data.get(key) != after_data.get(key)
    }


def observe_turn(
    result: TurnResult,
    *,
    before_state: ConversationState,
    before_dealer: DealerSnapshot,
    after_dealer: DealerSnapshot,
    commands: tuple[HarnessCommand, ...],
    response_strategy: ResponseStrategy,
    current_input: str = "",
) -> ObservedTurn:
    """Build semantic evidence without consulting a turn expectation."""
    payloads = _payloads(result)
    block_types = tuple(kind for kind, _ in payloads)
    external_calls = tuple(call.operation for call in after_dealer.calls[len(before_dealer.calls):])
    new_effects = after_dealer.side_effects[len(before_dealer.side_effects):]
    notice_code_values = {
        payload.get("code") for kind, payload in payloads if kind == "notice"
    }
    if new_effects:
        side_effects = tuple(
            f"{name}:{new_effects.count(name)}" for name in dict.fromkeys(new_effects)
        )
    elif after_dealer.side_effects and "already_completed" in notice_code_values:
        side_effects = tuple(
            f"{name}:{after_dealer.side_effects.count(name)}_total"
            for name in dict.fromkeys(after_dealer.side_effects)
        )
    else:
        side_effects = ()

    entity_ids: list[str] = []
    facts: list[str] = []
    notices: list[str] = []
    next_steps: list[str] = []
    text_parts: list[str] = []
    action_types: list[str] = []
    notice_codes: list[str] = []
    all_items: list[dict[str, Any]] = []

    for block, (kind, payload) in zip(result.blocks, payloads, strict=True):
        for reference in block.entity_references:
            _append(entity_ids, reference.entity_id)
        text = payload.get("text")
        if isinstance(text, str):
            text_parts.append(text)
        code = payload.get("code")
        if isinstance(code, str):
            notice_codes.append(code)
        for key in ("items", "vehicles"):
            records = payload.get(key, [])
            if isinstance(records, list):
                all_items.extend(item for item in records if isinstance(item, dict))
                for item in records:
                    if isinstance(item, dict) and isinstance(item.get("id"), str):
                        _append(entity_ids, item["id"])
        actions = payload.get("actions", [])
        if isinstance(actions, list):
            for action in actions:
                if isinstance(action, dict):
                    action_type = action.get("action_type")
                    if isinstance(action_type, str):
                        action_types.append(action_type)
                    entity_id = action.get("entity_id")
                    if isinstance(entity_id, str):
                        _append(entity_ids, entity_id)
                        if (
                            action_type == "switch_workflow"
                            and entity_id == WorkflowDomain.VEHICLES.value
                        ):
                            _append(next_steps, "offer_stock_search")

        if kind == "vehicle_cards":
            _append(facts, "result_count", "constraints_applied", "dealer_stock_only", "vehicle_results")
            _append(next_steps, "refine_or_select", "select_vehicle")
            vehicle_ids = {
                item["id"] for item in payload.get("vehicles", [])
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
            linked_ids = {
                item["entity_id"] for item in payload.get("actions", [])
                if isinstance(item, dict)
                and item.get("action_type") == "select_vehicle"
                and isinstance(item.get("entity_id"), str)
            }
            if vehicle_ids and vehicle_ids <= linked_ids:
                _append(facts, "stable_vehicle_link")
            if any(item.get("price", {}).get("amount_minor") is None for item in all_items if isinstance(item.get("price"), dict)):
                _append(facts, "price_on_request")
        elif kind == "comparison":
            _append(facts, "price", "mileage", "fuel", "transmission")
        elif kind == "offer_cards":
            _append(facts, "published_offer_terms")
        elif kind == "workshop_services":
            _append(facts, "service_names", "service_durations")
            _append(next_steps, "choose_service")
        elif kind == "dealerships":
            _append(facts, "addresses")
            _append(next_steps, "choose_location")
        elif kind == "slot_choices":
            if "list_workshop_slots" in external_calls:
                _append(facts, "live_workshop_slots")
            else:
                _append(facts, "live_slots")
            _append(next_steps, "select_slot")
        elif kind == "confirmation":
            _append(next_steps, "confirm_or_cancel")
            action_type = payload.get("pending_action_type")
            if action_type == "test_drive_booking":
                _append(facts, "vehicle_slot_customer_summary")
            elif action_type == "workshop_booking":
                _append(facts, "service_location_slot_customer_summary")
            elif action_type == "workshop_amendment":
                _append(facts, "old_and_new_slot")
            elif action_type == "workshop_cancellation":
                _append(facts, "original_booking_reference")
                _append(next_steps, "confirm_or_keep")
            elif action_type == "sales_enquiry":
                _append(notices, "finance")
            elif action_type == "callback":
                _append(facts, "callback_request_summary", "callback_time_preference_only")
            elif action_type == "part_exchange":
                _append(notices, "part_exchange")
            elif action_type == "dealership_message":
                _append(facts, "department_and_message_summary")
        elif kind == "booking_details":
            _append(facts, "workshop_reference", "appointment_time", "service_name", "dealership", "status")
            if any(item.get("status") == "confirmed" for item in all_items):
                _append(facts, "status:confirmed")
            _append(next_steps, "amend_or_cancel")
        elif kind == "action_result":
            _append(facts, "booking_reference")
            pending_type = before_state.pending_action.action_type if before_state.pending_action else None
            if pending_type is PendingActionType.WORKSHOP_BOOKING:
                _append(facts, "workshop_reference")
            if any(item.get("status") == "confirmed" for item in all_items):
                _append(facts, "status:confirmed")
            if any(item.get("status") == "cancelled" for item in all_items):
                _append(facts, "status:cancelled")
            if pending_type is PendingActionType.WORKSHOP_AMENDMENT:
                _append(facts, "updated_appointment_time")
        elif kind == "business_information":
            if "privacy" in current_input.casefold() or "details" in current_input.casefold():
                _append(notices, "privacy")
                _append(facts, "published_privacy_information")
            if "finance" in current_input.casefold():
                _append(notices, "finance")
            if "worth" in current_input.casefold() or "part exchange" in current_input.casefold():
                _append(notices, "part_exchange")

    command_names = tuple(command.name.value for command in commands)
    if "get_vehicle_details" in command_names:
        _append(facts, "dealer_vehicle_details", "price", "mileage", "fuel", "transmission", "stable_vehicle_link")
        _append(next_steps, "availability_or_test_drive")
        if "telescope" in current_input.casefold():
            _append(facts, "published_boot_space_or_dimensions")
            _append(next_steps, "compare_telescope_dimensions")
    if "check_vehicle_availability" in command_names:
        statuses = [item.get("status") for item in all_items]
        for status in ("available", "reserved", "sold"):
            if status in statuses or status in notice_codes:
                _append(facts, f"vehicle_status:{status}")
        _append(next_steps, "test_drive_or_enquiry", "sales_enquiry")
    if "list_workshop_locations" in command_names:
        _append(facts, "workshop_locations")
    if "get_dealership_hours" in command_names:
        if "temporary_failure" in notice_codes:
            _append(facts, "hours_temporarily_unavailable")
            _append(next_steps, "retry_hours")
        elif "bank holiday" in current_input.casefold():
            _append(facts, "holiday_date")
            if "service" in current_input.casefold():
                _append(facts, "holiday_service_closed")
            else:
                _append(facts, "holiday_reduced_sales_hours")
        else:
            _append(facts, "sales_saturday_hours")
    if "get_dealership_details" in command_names:
        _append(facts, "parts_phone")
    if "search_vehicles" in command_names and "cheaper" in current_input.casefold():
        _append(facts, "lower_priced_results", "lower_than_selected_price")
    if any(
        isinstance(item, EvidenceItem)
        and item.source_operation == "search_vehicles"
        and item.entity_id == "vehicle_search"
        and item.field_name == "result_count"
        and item.value == 0
        for item in result.evidence
    ):
        _append(facts, "zero_results")
        _append(next_steps, "relax_constraints")
    rendered_text = " ".join(text_parts).casefold()
    family_considerations = (
        (
            "rear-seat", "rear seat", "rear-legroom", "rear legroom",
            "second-row", "second row", "passenger space", "passenger room",
            "space you", "space needs", "cabin space",
        ),
        ("boot", "cargo", "luggage", "stroller"),
        ("child-seat", "child seat", "isofix", "child restraint"),
        ("running costs", "fuel economy"),
    )
    if sum(any(term in rendered_text for term in group) for group in family_considerations) >= 2:
        _append(facts, "family_vehicle_considerations")
    if any(term in rendered_text for term in family_considerations[0]) and any(
        term in rendered_text for term in family_considerations[1]
    ):
        _append(next_steps, "compare_space")
    if "the second" in current_input.casefold():
        _append(facts, "selected_second_presented_vehicle")
    if "tomorrow" in current_input.casefold() and "test drive it" in current_input.casefold():
        _append(facts, "resolved_persisted_vehicle")
    if "instead" in current_input.casefold():
        _append(facts, "location_changed")

    code_facts = {
        "no_vehicle_results": ("zero_results",),
        "slot_unavailable": ("slot_unavailable", "refreshed_slots"),
        "idempotency_conflict": ("idempotency_conflict", "original_booking_reference"),
        "no_workshop_slots": ("zero_slots_valid_availability",),
        "verification_failed": ("booking_not_found",),
        "verification_required": ("verification_required",),
        "booking_cancelled": ("already_cancelled", "status:cancelled"),
        "vehicle_reserved": ("vehicle_status:reserved",),
        "vehicle_sold": ("vehicle_status:sold",),
        "action_cancelled": ("action_cancelled",),
    }
    code_steps = {
        "invalid_phone": ("provide_valid_phone",),
        "no_vehicle_results": ("relax_constraints",),
        "no_workshop_slots": ("later_dates_or_other_location",),
        "verification_failed": ("recheck_all_identity_fields",),
        "verification_required": ("provide_lookup_identity",),
        "booking_cancelled": ("start_new_booking", "choose_another_date"),
        "slot_unavailable": ("select_new_slot",),
        "idempotency_conflict": ("start_new_request",),
    }
    for code in notice_codes:
        _append(facts, *code_facts.get(code, ()))
        _append(next_steps, *code_steps.get(code, ()))
    if (
        "missing_information" in notice_codes
        or response_strategy is ResponseStrategy.MISSING_INFORMATION
    ):
        clarification_text = " ".join(text_parts).casefold()
        price_conflict = (
            "budget conflict" in clarification_text
            or "budget conflicts" in clarification_text
            or (
                "conflict" in clarification_text
                and any(term in clarification_text for term in ("price", "budget", "limit"))
            )
            or (
                "under" in clarification_text
                and "at least" in clarification_text
            )
            or (
                "maximum" in clarification_text
                and "minimum" in clarification_text
            )
        )
        if price_conflict:
            _append(next_steps, "clarify_price_direction")
        else:
            _append(facts, "missing_customer_fields")
            _append(next_steps, "provide_customer_details")
        if "phone" in " ".join(text_parts).casefold():
            _append(facts, "invalid_phone")
            _append(next_steps, "provide_valid_phone")
    if any("couldn't find matching vehicles" in text.casefold() for text in text_parts):
        _append(facts, "zero_results")
        _append(next_steps, "relax_constraints")
    if "without the lookup" in current_input.casefold():
        _append(facts, "verification_required")
        _append(next_steps, "provide_lookup_identity")
    if "retry_not_allowed" in notice_codes and before_state.pending_action is not None:
        failure = before_state.pending_action.last_failure
        if failure is not None and failure.kind.value == "idempotency_conflict":
            _append(facts, "idempotency_conflict")
            _append(next_steps, "start_new_request")
    if "already_completed" in notice_codes:
        if before_state.pending_action and before_state.pending_action.action_type is PendingActionType.WORKSHOP_CANCELLATION:
            _append(facts, "already_cancelled")
        else:
            _append(facts, "original_booking_reference")
    if "register_interest" in action_types:
        _append(next_steps, "register_interest")
    if "sales_enquiry" in action_types:
        _append(next_steps, "sales_enquiry")
    if "show_more" in action_types or "show_more" in current_input.casefold():
        _append(facts, "next_result_page")
    if "confirmation" in block_types and (
        result.state.entities.selected_test_drive_slot_id
        != before_state.entities.selected_test_drive_slot_id
        or result.state.entities.selected_workshop_slot_id
        != before_state.entities.selected_workshop_slot_id
    ):
        _append(facts, "selected_slot_summary")
    if before_state.pending_action is not None and result.state.pending_action is None:
        if result.state.workflow.domain is not before_state.workflow.domain:
            _append(facts, "old_action_superseded")
    if "confirmation" in block_types and result.state.pending_action is not None:
        if result.state.pending_action.action_type is PendingActionType.WORKSHOP_AMENDMENT:
            _append(facts, "new_slot_summary")
    if "holiday_service_closed" in facts:
        _append(next_steps, "choose_another_date")
    if response_strategy is ResponseStrategy.GENERAL_GUIDANCE and any(
        any(verb in text.casefold() for verb in ("show", "find", "browse", "see"))
        and "stock" in text.casefold()
        for text in text_parts
    ):
        _append(next_steps, "offer_stock_search")

    claims: list[str] = []
    if "moon" in current_input.casefold() and any("moon" in text.casefold() and "help with vehicles" not in text.casefold() for text in text_parts):
        _append(claims, "moon_fact_answered")

    return ObservedTurn(
        commands=commands,
        external_calls=external_calls,
        mutations=tuple(new_effects),
        state=_state_projection(result.state),
        side_effects=side_effects,
        model_calls=result.model_calls,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        latency_ms=result.provider_latency_ms,
        answer=ObservedAnswer(
            strategy=response_strategy.value,
            block_types=block_types,
            entity_ids=tuple(entity_ids),
            facts=tuple(facts),
            notice_keys=tuple(notices),
            next_steps=tuple(next_steps),
            claims=tuple(claims),
            text="\n".join(text_parts),
            direct=True,
        ),
        blocks=tuple(block.to_dict() for block in result.blocks),
        state_changes=_state_changes(before_state, result.state),
    )
