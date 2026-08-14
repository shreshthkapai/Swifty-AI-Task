"""Shared workflow result, state, serialization, and recovery helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any

from webchat.domain.common import CustomerIdentity, Money
from webchat.domain.errors import DealerErrorKind, DealerFailure

from ..actions import (
    PendingAction,
    PendingActionState,
    PendingActionType,
    PendingRequestType,
)
from ..evidence import EvidenceGap, EvidenceItem, EvidenceRecord
from ..policy import PolicyCode, PolicyError
from ..render import DeclarativeRenderer
from ..state import (
    ConversationState,
    MessageBlock,
    PresentationGroup,
    PresentationProvenance,
    PresentedEntity,
    WorkflowDomain,
    WorkflowStage,
    WorkflowState,
)


ACTION_TTL = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    state: ConversationState
    blocks: tuple[MessageBlock, ...]
    evidence: tuple[EvidenceRecord, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, tuple) or not all(
            isinstance(item, (EvidenceItem, EvidenceGap)) for item in self.evidence
        ):
            raise ValueError("evidence must contain EvidenceItem or EvidenceGap values")


def optional(arguments: Mapping[str, Any], key: str, fallback: Any = None) -> Any:
    value = arguments.get(key)
    return fallback if value is None else value


def first_known(*values: Any) -> Any | None:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def parse_date(value: Any, field: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date") from exc


def enum_value(enum_type: type[StrEnum], value: Any, field: str, default: Any = None):
    if value is None:
        return default
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} has an unsupported value") from exc


def money_from_minor(
    amount: Any,
    currency: Any,
    *,
    default_currency: str = "GBP",
) -> Money | None:
    if amount is None:
        return None
    return Money(amount_minor=amount, currency=currency or default_currency)


def customer_payload(customer: CustomerIdentity) -> dict[str, str]:
    return {
        "first_name": customer.first_name,
        "last_name": customer.last_name,
        "email": customer.email,
        "phone": customer.phone,
    }


def customer_from_payload(value: Any) -> CustomerIdentity:
    if not isinstance(value, Mapping):
        raise ValueError("customer payload must be an object")
    return CustomerIdentity(
        first_name=value.get("first_name"),
        last_name=value.get("last_name"),
        email=value.get("email"),
        phone=value.get("phone"),
    )


def workflow_state(
    state: ConversationState,
    *,
    domain: WorkflowDomain,
    stage: WorkflowStage,
    gathered: Mapping[str, Any] | None = None,
    missing: Sequence[str] = (),
) -> ConversationState:
    return replace(
        state,
        workflow=WorkflowState(
            domain=domain,
            stage=stage,
            gathered_fields=gathered or {},
            missing_fields=tuple(missing),
        ),
    )


def prepare_action(
    state: ConversationState,
    *,
    action_type: PendingActionType,
    request_type: PendingRequestType,
    payload: Mapping[str, Any],
    now: datetime,
    id_factory: Callable[[], str],
    renderer: DeclarativeRenderer,
    domain: WorkflowDomain,
) -> CommandOutcome:
    action = PendingAction.from_mapping(
        action_id=id_factory(),
        action_type=action_type,
        request_type=request_type,
        request_payload=payload,
        state=PendingActionState.AWAITING_CONFIRMATION,
        idempotency_key=id_factory(),
        created_at=now,
        expires_at=now + ACTION_TTL,
    )
    next_state = workflow_state(
        replace(state, pending_action=action),
        domain=domain,
        stage=WorkflowStage.REVIEWING_ACTION,
        gathered={"action_type": action_type.value},
    )
    block = renderer.confirmation(
        action_id=action.action_id,
        action_type=action.action_type.value,
        summary=dict(payload),
        expires_at=action.expires_at,
    )
    return CommandOutcome(next_state, (block,))


def missing_information(
    state: ConversationState,
    fields: Sequence[str],
    *,
    domain: WorkflowDomain,
    renderer: DeclarativeRenderer,
) -> CommandOutcome:
    ordered = tuple(dict.fromkeys(fields))
    next_state = workflow_state(
        state,
        domain=domain,
        stage=WorkflowStage.COLLECTING_DETAILS,
        missing=ordered,
    )
    return CommandOutcome(
        next_state,
        (
            renderer.notice(
                f"I still need: {', '.join(ordered)}.",
                code="missing_information",
            ),
        ),
    )


def policy_recovery(
    state: ConversationState,
    error: PolicyError,
    *,
    renderer: DeclarativeRenderer,
    entity_id: str | None = None,
) -> CommandOutcome:
    blocks: list[MessageBlock] = [
        renderer.notice(_POLICY_TEXT[error.code], code=error.code.value)
    ]
    if error.next_steps:
        blocks.append(
            renderer.actions(
                tuple((step, _ACTION_LABELS[step], entity_id) for step in error.next_steps)
            )
        )
    return CommandOutcome(state, tuple(blocks))


def dealer_recovery(
    state: ConversationState,
    failure: DealerFailure,
    *,
    renderer: DeclarativeRenderer,
) -> CommandOutcome:
    text, next_steps = _DEALER_RECOVERY.get(
        failure.kind,
        ("The dealership system could not complete that request.", ()),
    )
    blocks: list[MessageBlock] = [renderer.notice(text, code=failure.kind.value)]
    if next_steps:
        blocks.append(
            renderer.actions(
                tuple((step, _ACTION_LABELS[step], failure.resource) for step in next_steps)
            )
        )
    return CommandOutcome(state, tuple(blocks))


def presentation_group(
    *,
    group_id: str,
    entity_type: str,
    records: Sequence[tuple[str, Mapping[str, Any]]],
    block: MessageBlock,
    now: datetime,
) -> PresentationGroup:
    action_by_entity: dict[str, str] = {}
    payload = block.to_dict()["payload"]
    for action in payload.get("actions", []):
        entity_id = action.get("entity_id")
        if entity_id:
            action_by_entity[entity_id] = action["action_id"]
    return PresentationGroup(
        group_id=group_id,
        entity_type=entity_type,
        entities=tuple(
            PresentedEntity(
                entity_id=entity_id,
                ordinal=index,
                snapshot={**snapshot, "action_id": action_by_entity.get(entity_id)},
            )
            for index, (entity_id, snapshot) in enumerate(records, start=1)
        ),
        snapshot_at=now,
        provenance=PresentationProvenance.DEALER_API,
    )


_POLICY_TEXT = {
    PolicyCode.INVALID_REQUEST: "Those details cannot be used for this dealership request.",
    PolicyCode.MISSING_CUSTOMER_DETAILS: "Some customer details are still required.",
    PolicyCode.INVALID_CUSTOMER_DETAILS: "Some customer details are not valid.",
    PolicyCode.CONFIRMATION_REQUIRED: "Please confirm this action before it is sent.",
    PolicyCode.ACTION_EXPIRED: "That confirmation has expired. Please review the action again.",
    PolicyCode.INVALID_ACTION_STATE: "That action is no longer available.",
    PolicyCode.VERIFICATION_REQUIRED: "Please verify the workshop booking before changing it.",
    PolicyCode.VEHICLE_RESERVED: "That vehicle is reserved and cannot be booked for a test drive.",
    PolicyCode.VEHICLE_SOLD: "That vehicle has been sold and cannot be booked for a test drive.",
    PolicyCode.VEHICLE_UNAVAILABLE: "That vehicle is not currently available for a test drive.",
    PolicyCode.VEHICLE_NOT_RESERVED: "Interest registration is only available for reserved vehicles.",
    PolicyCode.SLOT_UNAVAILABLE: "That slot is no longer available.",
    PolicyCode.BOOKING_CANCELLED: "A cancelled workshop booking cannot be amended.",
}
_ACTION_LABELS = {
    "register_interest": "Register interest",
    "sales_enquiry": "Send a sales enquiry",
    "retry": "Try again",
    "reselect_slot": "Choose another slot",
}
_DEALER_RECOVERY = {
    DealerErrorKind.VEHICLE_RESERVED: (
        "That vehicle is reserved and cannot be booked for a test drive.",
        ("register_interest", "sales_enquiry"),
    ),
    DealerErrorKind.VEHICLE_UNAVAILABLE: (
        "That vehicle is no longer available.",
        ("sales_enquiry",),
    ),
    DealerErrorKind.VEHICLE_NOT_RESERVED: (
        "That vehicle is no longer reserved, so interest registration is unavailable.",
        ("sales_enquiry",),
    ),
    DealerErrorKind.SLOT_UNAVAILABLE: (
        "That slot is no longer available. Please choose another.",
        ("reselect_slot",),
    ),
    DealerErrorKind.BOOKING_CANCELLED: (
        "That workshop booking has already been cancelled.",
        (),
    ),
    DealerErrorKind.VERIFICATION_FAILED: (
        "I could not verify a workshop booking with those details.",
        (),
    ),
    DealerErrorKind.TEMPORARY_FAILURE: (
        "The dealership system is temporarily unavailable.",
        ("retry",),
    ),
    DealerErrorKind.IDEMPOTENCY_CONFLICT: (
        "That request conflicts with an earlier action and was not repeated.",
        (),
    ),
}
