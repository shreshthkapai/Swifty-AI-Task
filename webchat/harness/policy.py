"""Deterministic authorization and business-policy checks for harness workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re
from typing import Any, Mapping

from webchat.domain.common import BookingStatus, CustomerIdentity, require_aware
from webchat.domain.vehicles import VehicleAvailability, VehicleAvailabilityStatus
from webchat.domain.workshop import WorkshopBooking

from .actions import PendingAction, PendingActionState
from .state import ConversationState


class PolicyCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    MISSING_CUSTOMER_DETAILS = "missing_customer_details"
    INVALID_CUSTOMER_DETAILS = "invalid_customer_details"
    CONFIRMATION_REQUIRED = "confirmation_required"
    ACTION_EXPIRED = "action_expired"
    INVALID_ACTION_STATE = "invalid_action_state"
    VERIFICATION_REQUIRED = "verification_required"
    VEHICLE_RESERVED = "vehicle_reserved"
    VEHICLE_SOLD = "vehicle_sold"
    VEHICLE_UNAVAILABLE = "vehicle_unavailable"
    VEHICLE_NOT_RESERVED = "vehicle_not_reserved"
    SLOT_UNAVAILABLE = "slot_unavailable"
    BOOKING_CANCELLED = "booking_cancelled"


@dataclass(frozen=True, slots=True)
class PolicyFailure:
    code: PolicyCode
    fields: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()


class PolicyError(Exception):
    def __init__(
        self,
        code: PolicyCode,
        *,
        fields: tuple[str, ...] = (),
        next_steps: tuple[str, ...] = (),
    ) -> None:
        self.failure = PolicyFailure(code, fields, next_steps)
        super().__init__(code.value)

    @property
    def code(self) -> PolicyCode:
        return self.failure.code

    @property
    def fields(self) -> tuple[str, ...]:
        return self.failure.fields

    @property
    def next_steps(self) -> tuple[str, ...]:
        return self.failure.next_steps


_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
class PolicyEngine:
    """Apply hard rules independently of provider wording or tool visibility."""

    def customer_identity(
        self,
        arguments: Mapping[str, Any],
        *,
        state: ConversationState,
    ) -> CustomerIdentity:
        values = {
            name: arguments.get(name) or getattr(state.customer, name)
            for name in ("first_name", "last_name", "email", "phone")
        }
        missing = tuple(name for name, value in values.items() if not value)
        if missing:
            raise PolicyError(PolicyCode.MISSING_CUSTOMER_DETAILS, fields=missing)

        invalid: list[str] = []
        email = str(values["email"]).strip()
        phone = str(values["phone"]).strip()
        if not _EMAIL.fullmatch(email):
            invalid.append("email")
        phone_digits = re.sub(r"\D", "", phone)
        if not 10 <= len(phone_digits) <= 15:
            invalid.append("phone")
        if invalid:
            raise PolicyError(
                PolicyCode.INVALID_CUSTOMER_DETAILS,
                fields=tuple(invalid),
            )
        return CustomerIdentity(
            first_name=str(values["first_name"]),
            last_name=str(values["last_name"]),
            email=email,
            phone=phone,
        )

    def authorize_execution(self, action: PendingAction, *, now: datetime) -> None:
        require_aware(now, "now")
        if action.is_expired(now):
            raise PolicyError(PolicyCode.ACTION_EXPIRED)
        if action.state is not PendingActionState.CONFIRMED:
            if action.state is PendingActionState.AWAITING_CONFIRMATION:
                raise PolicyError(PolicyCode.CONFIRMATION_REQUIRED)
            raise PolicyError(PolicyCode.INVALID_ACTION_STATE)

    def require_booking_grant(
        self,
        state: ConversationState,
        booking_id: str,
        *,
        now: datetime,
    ) -> None:
        if not state.has_active_grant(booking_id, now=now):
            raise PolicyError(PolicyCode.VERIFICATION_REQUIRED)

    def require_test_drive_eligible(self, availability: VehicleAvailability) -> None:
        if availability.can_book_test_drive:
            return
        if availability.status is VehicleAvailabilityStatus.RESERVED:
            raise PolicyError(
                PolicyCode.VEHICLE_RESERVED,
                next_steps=("register_interest", "sales_enquiry"),
            )
        if availability.status is VehicleAvailabilityStatus.SOLD:
            raise PolicyError(
                PolicyCode.VEHICLE_SOLD,
                next_steps=("sales_enquiry",),
            )
        raise PolicyError(
            PolicyCode.VEHICLE_UNAVAILABLE,
            next_steps=("sales_enquiry",),
        )

    def require_interest_eligible(self, availability: VehicleAvailability) -> None:
        if availability.status is VehicleAvailabilityStatus.SOLD:
            raise PolicyError(
                PolicyCode.VEHICLE_SOLD,
                next_steps=("sales_enquiry",),
            )
        if not availability.can_register_interest:
            raise PolicyError(
                PolicyCode.VEHICLE_NOT_RESERVED,
                next_steps=("sales_enquiry",),
            )

    def require_booking_amendable(self, booking: WorkshopBooking) -> None:
        if booking.status is BookingStatus.CANCELLED:
            raise PolicyError(PolicyCode.BOOKING_CANCELLED)
