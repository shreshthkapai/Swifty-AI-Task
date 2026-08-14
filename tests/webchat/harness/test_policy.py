from dataclasses import replace
from datetime import UTC, datetime, timedelta
import unittest

from webchat.domain.common import BookingStatus, CustomerIdentity
from webchat.domain.vehicles import VehicleAvailability, VehicleAvailabilityStatus
from webchat.domain.workshop import WorkshopBooking
from webchat.harness.actions import (
    PendingAction,
    PendingActionState,
    PendingActionType,
    PendingRequestType,
)
from webchat.harness.policy import PolicyCode, PolicyEngine, PolicyError
from webchat.harness.state import ConversationState, CustomerState, VerificationGrant


NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
CUSTOMER = CustomerIdentity("Jamie", "Taylor", "jamie@example.com", "07700900123")


def pending(state: PendingActionState = PendingActionState.AWAITING_CONFIRMATION) -> PendingAction:
    return PendingAction.from_mapping(
        action_id="action-1",
        action_type=PendingActionType.TEST_DRIVE_BOOKING,
        request_type=PendingRequestType.TEST_DRIVE_BOOKING,
        request_payload={"slot_id": "slot-1", "customer": {
            "first_name": "Jamie", "last_name": "Taylor",
            "email": "jamie@example.com", "phone": "07700900123",
        }},
        state=state,
        idempotency_key="idem-1",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )


def booking(status: BookingStatus = BookingStatus.CONFIRMED) -> WorkshopBooking:
    return WorkshopBooking(
        id="booking-1", reference="WORK-1", slot_id="slot-1",
        dealership_id="dealer-1", service_type_id="mot", customer=CUSTOMER,
        registration="AB12 CDE", mileage=40_000, notes=None, status=status,
        created_at=NOW, updated_at=NOW,
        cancelled_at=NOW if status is BookingStatus.CANCELLED else None,
    )


class PolicyEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = PolicyEngine()

    def test_customer_details_merge_state_and_reject_malformed_phone(self) -> None:
        state = ConversationState(
            customer=CustomerState(
                first_name="Jamie", last_name="Taylor", email="jamie@example.com"
            )
        )

        with self.assertRaises(PolicyError) as raised:
            self.policy.customer_identity({"phone": "123"}, state=state)

        self.assertEqual(raised.exception.code, PolicyCode.INVALID_CUSTOMER_DETAILS)
        self.assertEqual(raised.exception.fields, ("phone",))

    def test_mutation_requires_live_unexpired_confirmation(self) -> None:
        with self.assertRaises(PolicyError) as raised:
            self.policy.authorize_execution(pending(), now=NOW)
        self.assertEqual(raised.exception.code, PolicyCode.CONFIRMATION_REQUIRED)

        confirmed = replace(pending(), state=PendingActionState.CONFIRMED)
        self.policy.authorize_execution(confirmed, now=NOW)

        with self.assertRaises(PolicyError) as expired:
            self.policy.authorize_execution(confirmed, now=NOW + timedelta(hours=1))
        self.assertEqual(expired.exception.code, PolicyCode.ACTION_EXPIRED)

    def test_vehicle_status_has_deterministic_allowed_alternatives(self) -> None:
        reserved = VehicleAvailability(
            vehicle_id="veh-007", status=VehicleAvailabilityStatus.RESERVED,
            can_enquire=True, can_book_test_drive=False, can_register_interest=True,
            next_test_drive_slot=None,
        )

        with self.assertRaises(PolicyError) as raised:
            self.policy.require_test_drive_eligible(reserved)

        self.assertEqual(raised.exception.code, PolicyCode.VEHICLE_RESERVED)
        self.assertEqual(raised.exception.next_steps, ("register_interest", "sales_enquiry"))

        sold = VehicleAvailability(
            vehicle_id="veh-013", status=VehicleAvailabilityStatus.SOLD,
            can_enquire=True, can_book_test_drive=False, can_register_interest=False,
            next_test_drive_slot=None,
        )
        with self.assertRaises(PolicyError) as sold_error:
            self.policy.require_test_drive_eligible(sold)
        self.assertEqual(sold_error.exception.code, PolicyCode.VEHICLE_SOLD)
        self.assertEqual(sold_error.exception.next_steps, ("sales_enquiry",))

        with self.assertRaises(PolicyError) as sold_interest:
            self.policy.require_interest_eligible(sold)
        self.assertEqual(sold_interest.exception.code, PolicyCode.VEHICLE_SOLD)
        self.assertEqual(sold_interest.exception.next_steps, ("sales_enquiry",))

    def test_workshop_write_requires_active_booking_specific_grant(self) -> None:
        state = ConversationState(
            verification_grants=(VerificationGrant.issue("booking-2", now=NOW),)
        )
        with self.assertRaises(PolicyError) as raised:
            self.policy.require_booking_grant(state, "booking-1", now=NOW)
        self.assertEqual(raised.exception.code, PolicyCode.VERIFICATION_REQUIRED)

    def test_cancelled_workshop_booking_cannot_be_amended(self) -> None:
        with self.assertRaises(PolicyError) as raised:
            self.policy.require_booking_amendable(booking(BookingStatus.CANCELLED))
        self.assertEqual(raised.exception.code, PolicyCode.BOOKING_CANCELLED)

if __name__ == "__main__":
    unittest.main()
