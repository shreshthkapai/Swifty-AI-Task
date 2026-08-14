from dataclasses import replace
from datetime import date, time, timedelta
import unittest

from webchat.domain.common import Department
from webchat.domain.dealerships import HolidayOpening, OpeningHours
from webchat.domain.errors import DealerError, DealerErrorKind, DealerFailure
from webchat.domain.vehicles import VehicleAvailabilityStatus, VehicleDetails
from webchat.domain.workshop import WorkshopBookingDetails
from webchat.harness.actions import (
    PendingAction,
    PendingActionState,
    PendingActionType,
    PendingRequestType,
)
from webchat.harness.contracts import (
    PreparationCommand,
    PreparationCommandName,
    ReadCommand,
    ReadCommandName,
    ResponseStrategy,
    TurnPlan,
    TurnScope,
)
from webchat.harness.planning import TurnRequest
from webchat.harness.state import (
    ConversationState,
    CustomerState,
    EntityContext,
    PresentationGroup,
    PresentationProvenance,
    PresentedEntity,
)

from tests.webchat.harness.test_runtime import (
    NOW,
    availability,
    dealer,
    drive_slot,
    location,
    runtime,
    vehicle,
    workshop_booking,
)


class RuntimeQuestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_obvious_trivia_redirects_without_provider_or_dealer(self) -> None:
        fake = dealer()
        inert = TurnPlan(TurnScope.IN_DOMAIN, (), ResponseStrategy.ACKNOWLEDGEMENT)
        harness, provider = runtime(fake, inert)

        result = await harness.handle(TurnRequest(
            current_input="What size is the moon?", state=ConversationState(), now=NOW
        ))

        self.assertEqual(provider.requests, [])
        self.assertEqual(result.model_calls, 0)
        self.assertEqual(
            result.blocks[0].to_dict()["payload"]["text"],
            "I can help with vehicles, test drives, sales, servicing, and dealership "
            "information.",
        )
        self.assertEqual(fake.method_calls, [])

    async def test_malformed_phone_requests_correction_before_any_booking_call(self) -> None:
        fake = dealer()
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (PreparationCommand.from_mapping(
                PreparationCommandName.PREPARE_TEST_DRIVE_BOOKING,
                {
                    "slot_id": "td-slot-1", "first_name": "Sam", "last_name": "Jones",
                    "email": "sam@example.com", "phone": "123",
                },
            ),),
            ResponseStrategy.ACTION_PREPARED,
        )
        harness, _ = runtime(fake, plan)
        state = ConversationState(entities=EntityContext(selected_vehicle_id="veh-003"))

        result = await harness.handle(TurnRequest(
            current_input="Book it for Sam Jones, sam@example.com, phone 123",
            state=state, now=NOW,
        ))

        self.assertIsNone(result.state.pending_action)
        self.assertEqual(result.blocks[0].to_dict()["payload"]["code"], "missing_information")
        fake.get_vehicle_availability.assert_not_awaited()
        fake.book_test_drive.assert_not_awaited()

    async def test_incorrect_workshop_identity_is_safe_and_creates_no_grant(self) -> None:
        fake = dealer()
        fake.lookup_workshop_booking.side_effect = DealerError(
            DealerFailure(DealerErrorKind.VERIFICATION_FAILED)
        )
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(ReadCommandName.RETRIEVE_WORKSHOP_BOOKING, {
                "reference": "WORK-10001", "last_name": "Taylor",
                "registration": "AB12 CDE", "phone": "07700900999",
            }),),
            ResponseStrategy.BOOKING_DETAILS,
        )
        harness, _ = runtime(fake, plan)

        result = await harness.handle(TurnRequest(
            current_input="Find WORK-10001 for Taylor, AB12 CDE, 07700900999",
            state=ConversationState(), now=NOW,
        ))

        self.assertEqual(result.state.verification_grants, ())
        self.assertEqual(result.blocks[0].to_dict()["payload"]["code"], "verification_failed")
        self.assertNotIn("phone", str(result.blocks[0].to_dict()["payload"]).lower())

    async def test_bank_holiday_hours_render_the_authoritative_exception(self) -> None:
        fake = dealer()
        fake.get_opening_hours.return_value = OpeningHours(
            "northstar-manchester", "Europe/London", (),
            (HolidayOpening(date(2026, 8, 31), "Summer bank holiday", Department.SALES, time(10), time(16)),),
        )
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(ReadCommandName.GET_DEALERSHIP_HOURS, {
                "dealership_id": "northstar-manchester", "department": "sales",
            }),),
            ResponseStrategy.DEALERSHIP_DETAILS,
        )
        harness, _ = runtime(fake, plan)

        result = await harness.handle(TurnRequest(
            current_input="What time is Manchester sales open on the upcoming bank holiday?",
            state=ConversationState(), now=NOW,
        ))

        hours = next(block for block in result.blocks if block.kind == "opening_hours")
        item = hours.to_dict()["payload"]["items"][0]
        self.assertEqual(item["kind"], "holiday")
        self.assertEqual(item["opens_at"], "10:00")
        self.assertEqual(item["closes_at"], "16:00")

    async def test_second_one_resolves_from_structured_order_with_zero_model_calls(self) -> None:
        fake = dealer()
        fake.get_vehicle.return_value = VehicleDetails(vehicle("veh-002"), ())
        inert = TurnPlan(TurnScope.IN_DOMAIN, (), ResponseStrategy.ACKNOWLEDGEMENT)
        harness, provider = runtime(fake, inert)
        state = ConversationState(presentation_groups=(PresentationGroup(
            group_id="vehicles-1", entity_type="vehicle",
            entities=(PresentedEntity("veh-001", 1), PresentedEntity("veh-002", 2)),
            snapshot_at=NOW, provenance=PresentationProvenance.DEALER_API,
        ),))

        result = await harness.handle(TurnRequest(
            current_input="the second one", state=state, now=NOW
        ))

        self.assertEqual(provider.requests, [])
        fake.get_vehicle.assert_awaited_once_with("veh-002")
        self.assertEqual(result.state.entities.selected_vehicle_id, "veh-002")

    async def test_disappearing_slot_refreshes_choices_without_booking(self) -> None:
        fake = dealer()
        fake.get_vehicle_availability.return_value = availability()
        fake.list_test_drive_slots.side_effect = [(), (drive_slot("td-slot-new"),)]
        inert = TurnPlan(TurnScope.IN_DOMAIN, (), ResponseStrategy.ACKNOWLEDGEMENT)
        harness, provider = runtime(fake, inert)
        action = PendingAction.from_mapping(
            action_id="pending-1", action_type=PendingActionType.TEST_DRIVE_BOOKING,
            request_type=PendingRequestType.TEST_DRIVE_BOOKING,
            request_payload={
                "slot_id": "td-slot-old", "vehicle_id": "veh-003",
                "customer": {"first_name": "Jamie", "last_name": "Taylor", "email": "jamie@example.com", "phone": "07700900123"},
            },
            state=PendingActionState.AWAITING_CONFIRMATION,
            idempotency_key="idem-1", created_at=NOW, expires_at=NOW + timedelta(minutes=15),
        )

        result = await harness.handle(TurnRequest(
            current_input="confirm", state=ConversationState(pending_action=action), now=NOW
        ))

        self.assertEqual(provider.requests, [])
        fake.book_test_drive.assert_not_awaited()
        self.assertEqual(result.state.pending_action.state, PendingActionState.FAILED)
        self.assertIn("slot_choices", {block.kind for block in result.blocks})

    async def test_sold_vehicle_confirmation_offers_enquiry_only(self) -> None:
        fake = dealer()
        fake.get_vehicle_availability.return_value = availability(
            VehicleAvailabilityStatus.SOLD
        )
        inert = TurnPlan(TurnScope.IN_DOMAIN, (), ResponseStrategy.ACKNOWLEDGEMENT)
        harness, provider = runtime(fake, inert)
        action = PendingAction.from_mapping(
            action_id="pending-1",
            action_type=PendingActionType.TEST_DRIVE_BOOKING,
            request_type=PendingRequestType.TEST_DRIVE_BOOKING,
            request_payload={
                "slot_id": "td-slot-1",
                "vehicle_id": "veh-003",
                "customer": {
                    "first_name": "Jamie",
                    "last_name": "Taylor",
                    "email": "jamie@example.com",
                    "phone": "07700900123",
                },
            },
            state=PendingActionState.AWAITING_CONFIRMATION,
            idempotency_key="idem-1",
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=15),
        )

        result = await harness.handle(
            TurnRequest(
                current_input="confirm",
                state=ConversationState(pending_action=action),
                now=NOW,
            )
        )

        self.assertEqual(provider.requests, [])
        fake.book_test_drive.assert_not_awaited()
        actions = next(block for block in result.blocks if block.kind == "actions")
        self.assertEqual(
            [item["action_type"] for item in actions.to_dict()["payload"]["actions"]],
            ["sales_enquiry"],
        )

    async def test_corrected_workshop_identity_reuses_structured_lookup_fields(self) -> None:
        fake = dealer()
        fake.lookup_workshop_booking.side_effect = [
            DealerError(DealerFailure(DealerErrorKind.VERIFICATION_FAILED)),
            WorkshopBookingDetails(
                workshop_booking(), NOW + timedelta(days=1), "MOT", location()
            ),
        ]
        first_plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(ReadCommandName.RETRIEVE_WORKSHOP_BOOKING, {
                "reference": "WORK-1", "last_name": "Taylor",
                "registration": "AB12 CDE", "phone": "07700900999",
            }),),
            ResponseStrategy.BOOKING_DETAILS,
        )
        first_harness, _ = runtime(fake, first_plan)
        first = await first_harness.handle(TurnRequest(
            current_input="Find WORK-1 for Taylor, AB12 CDE, 07700900999",
            state=ConversationState(), now=NOW,
        ))
        corrected_plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(
                ReadCommandName.RETRIEVE_WORKSHOP_BOOKING,
                {"phone": "07700900123"},
            ),),
            ResponseStrategy.BOOKING_DETAILS,
        )
        corrected_harness, _ = runtime(fake, corrected_plan)

        corrected = await corrected_harness.handle(TurnRequest(
            current_input="Sorry, the phone is 07700900123",
            state=first.state, now=NOW,
        ))

        second_lookup = fake.lookup_workshop_booking.await_args_list[1].args[0]
        self.assertEqual(second_lookup.reference, "WORK-1")
        self.assertEqual(second_lookup.phone, "07700900123")
        self.assertTrue(corrected.state.has_active_grant("wsb-1", now=NOW))

    async def test_past_slot_is_not_booked_even_if_adapter_returns_its_id(self) -> None:
        fake = dealer()
        fake.get_vehicle_availability.return_value = availability()
        past = replace(drive_slot("td-slot-old"), starts_at=NOW - timedelta(minutes=1))
        fake.list_test_drive_slots.side_effect = [
            (past,),
            (drive_slot("td-slot-new"),),
        ]
        inert = TurnPlan(TurnScope.IN_DOMAIN, (), ResponseStrategy.ACKNOWLEDGEMENT)
        harness, _ = runtime(fake, inert)
        action = PendingAction.from_mapping(
            action_id="pending-1", action_type=PendingActionType.TEST_DRIVE_BOOKING,
            request_type=PendingRequestType.TEST_DRIVE_BOOKING,
            request_payload={
                "slot_id": "td-slot-old", "vehicle_id": "veh-003",
                "customer": {"first_name": "Jamie", "last_name": "Taylor", "email": "jamie@example.com", "phone": "07700900123"},
            },
            state=PendingActionState.AWAITING_CONFIRMATION,
            idempotency_key="idem-1", created_at=NOW, expires_at=NOW + timedelta(minutes=15),
        )

        result = await harness.handle(TurnRequest(
            current_input="confirm", state=ConversationState(pending_action=action), now=NOW
        ))

        fake.book_test_drive.assert_not_awaited()
        self.assertEqual(result.state.pending_action.state, PendingActionState.FAILED)
        self.assertIn("slot_choices", {block.kind for block in result.blocks})

    async def test_slot_refresh_outage_preserves_original_safe_recovery(self) -> None:
        fake = dealer()
        fake.get_vehicle_availability.return_value = availability()
        fake.list_test_drive_slots.side_effect = [
            (),
            DealerError(DealerFailure(DealerErrorKind.TEMPORARY_FAILURE, retryable=True)),
        ]
        inert = TurnPlan(TurnScope.IN_DOMAIN, (), ResponseStrategy.ACKNOWLEDGEMENT)
        harness, _ = runtime(fake, inert)
        action = PendingAction.from_mapping(
            action_id="pending-1", action_type=PendingActionType.TEST_DRIVE_BOOKING,
            request_type=PendingRequestType.TEST_DRIVE_BOOKING,
            request_payload={
                "slot_id": "td-slot-old", "vehicle_id": "veh-003",
                "customer": {"first_name": "Jamie", "last_name": "Taylor", "email": "jamie@example.com", "phone": "07700900123"},
            },
            state=PendingActionState.AWAITING_CONFIRMATION,
            idempotency_key="idem-1", created_at=NOW, expires_at=NOW + timedelta(minutes=15),
        )

        result = await harness.handle(TurnRequest(
            current_input="confirm", state=ConversationState(pending_action=action), now=NOW
        ))

        self.assertEqual(result.blocks[0].to_dict()["payload"]["code"], "slot_unavailable")
        fake.book_test_drive.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
