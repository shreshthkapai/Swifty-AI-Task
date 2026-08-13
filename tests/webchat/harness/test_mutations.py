from datetime import timedelta
from types import SimpleNamespace
import unittest

from webchat.domain.common import BookingStatus, Money
from webchat.domain.dealerships import DealershipMessageRequest
from webchat.domain.sales import (
    CallbackRequest,
    PartExchangeRequest,
    SalesEnquiryRequest,
    VehicleInterestRequest,
)
from webchat.domain.vehicles import VehicleAvailabilityStatus
from webchat.domain.workshop import (
    WorkshopBookingAmendment,
    WorkshopBookingRequest,
    WorkshopCancellationRequest,
    WorkshopSlot,
)
from webchat.harness.actions import (
    PendingAction,
    PendingActionState,
    PendingActionType,
    PendingRequestType,
)
from webchat.harness.contracts import ResponseStrategy, TurnPlan, TurnScope
from webchat.harness.planning import TurnRequest
from webchat.harness.state import ConversationState, VerificationGrant

from tests.webchat.harness.test_runtime import (
    NOW,
    availability,
    dealer,
    runtime,
    workshop_booking,
)


CUSTOMER = {
    "first_name": "Jamie", "last_name": "Taylor",
    "email": "jamie@example.com", "phone": "07700900123",
}


def pending(action_type, request_type, payload):
    return PendingAction.from_mapping(
        action_id="pending-1", action_type=action_type, request_type=request_type,
        request_payload=payload, state=PendingActionState.AWAITING_CONFIRMATION,
        idempotency_key="stable-idempotency-key", created_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )


def workshop_slot() -> WorkshopSlot:
    return WorkshopSlot(
        id="ws-slot-1", dealership_id="northstar-manchester",
        service_type_id="service-mot", starts_at=NOW + timedelta(days=1),
        dealership_name="Northstar Manchester", service_name="MOT",
        duration_minutes=60, price_from=Money(5_499, "GBP"),
    )


class MutationExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def _confirm(self, action, *, configure=None, grants=()):
        fake = dealer()
        if configure:
            configure(fake)
        inert = TurnPlan(TurnScope.IN_DOMAIN, (), ResponseStrategy.ACKNOWLEDGEMENT)
        harness, provider = runtime(fake, inert)
        result = await harness.handle(TurnRequest(
            current_input="confirm",
            state=ConversationState(pending_action=action, verification_grants=grants),
            now=NOW,
        ))
        self.assertEqual(provider.requests, [])
        self.assertEqual(result.state.pending_action.state, PendingActionState.SUCCEEDED)
        return fake

    async def test_sales_writes_use_typed_requests_and_stable_idempotency(self) -> None:
        cases = (
            (
                PendingActionType.SALES_ENQUIRY, PendingRequestType.SALES_ENQUIRY,
                {"dealership_id": "northstar-manchester", "enquiry_type": "finance", "customer": CUSTOMER, "message": "Finance question", "vehicle_id": "veh-003"},
                "create_sales_enquiry", SalesEnquiryRequest, None,
            ),
            (
                PendingActionType.VEHICLE_INTEREST, PendingRequestType.VEHICLE_INTEREST,
                {"vehicle_id": "veh-003", "customer": CUSTOMER, "notes": None},
                "register_vehicle_interest", VehicleInterestRequest,
                lambda fake: setattr(fake.get_vehicle_availability, "return_value", availability(VehicleAvailabilityStatus.RESERVED)),
            ),
            (
                PendingActionType.CALLBACK, PendingRequestType.CALLBACK,
                {"dealership_id": "northstar-manchester", "department": "sales", "customer": CUSTOMER, "reason": "Finance", "preferred_time": None, "vehicle_id": None},
                "request_callback", CallbackRequest, None,
            ),
            (
                PendingActionType.PART_EXCHANGE, PendingRequestType.PART_EXCHANGE,
                {"dealership_id": "northstar-manchester", "customer": CUSTOMER, "registration": "AB12 CDE", "mileage": 50_000, "condition": "good"},
                "value_part_exchange", PartExchangeRequest, None,
            ),
        )
        for action_type, request_type, payload, method_name, request_class, configure in cases:
            with self.subTest(action_type=action_type):
                action = pending(action_type, request_type, payload)
                def setup(fake):
                    if configure:
                        configure(fake)
                    getattr(fake, method_name).return_value = SimpleNamespace(id="result-1", reference="REF-1", status="ok")
                fake = await self._confirm(action, configure=setup)
                call = getattr(fake, method_name).await_args
                self.assertIsInstance(call.args[0], request_class)
                self.assertEqual(call.kwargs["idempotency_key"], "stable-idempotency-key")

    async def test_workshop_and_message_writes_use_typed_requests(self) -> None:
        cases = (
            (
                pending(PendingActionType.WORKSHOP_BOOKING, PendingRequestType.WORKSHOP_BOOKING, {
                    "slot_id": "ws-slot-1", "customer": CUSTOMER, "registration": "AB12 CDE", "mileage": 50_000, "notes": None,
                }),
                "book_workshop", WorkshopBookingRequest,
                lambda fake: setattr(fake.list_workshop_slots, "return_value", (workshop_slot(),)),
                (), True,
            ),
            (
                pending(PendingActionType.WORKSHOP_AMENDMENT, PendingRequestType.WORKSHOP_AMENDMENT, {
                    "booking_id": "wsb-1", "slot_id": None, "mileage": 51_000, "notes": None,
                }),
                "amend_workshop_booking", WorkshopBookingAmendment,
                lambda fake: setattr(fake.get_workshop_booking, "return_value", workshop_booking()),
                (VerificationGrant.issue("wsb-1", now=NOW),), False,
            ),
            (
                pending(PendingActionType.WORKSHOP_CANCELLATION, PendingRequestType.WORKSHOP_CANCELLATION, {"booking_id": "wsb-1"}),
                "cancel_workshop_booking", WorkshopCancellationRequest,
                lambda fake: setattr(fake.get_workshop_booking, "return_value", workshop_booking()),
                (VerificationGrant.issue("wsb-1", now=NOW),), False,
            ),
            (
                pending(PendingActionType.DEALERSHIP_MESSAGE, PendingRequestType.DEALERSHIP_MESSAGE, {
                    "dealership_id": "northstar-manchester", "department": "service",
                    "subject": "Running late", "message": "Ten minutes late",
                    "customer": CUSTOMER, "preferred_contact_method": "email",
                }),
                "send_dealership_message", DealershipMessageRequest,
                None, (), True,
            ),
        )
        for action, method_name, request_class, configure, grants, has_idempotency in cases:
            with self.subTest(action_type=action.action_type):
                def setup(fake):
                    if configure:
                        configure(fake)
                    getattr(fake, method_name).return_value = SimpleNamespace(id="result-1", reference="REF-1", status=BookingStatus.CONFIRMED)
                fake = await self._confirm(action, configure=setup, grants=grants)
                call = getattr(fake, method_name).await_args
                self.assertIsInstance(call.args[0], request_class)
                if has_idempotency:
                    self.assertEqual(call.kwargs["idempotency_key"], "stable-idempotency-key")


if __name__ == "__main__":
    unittest.main()
