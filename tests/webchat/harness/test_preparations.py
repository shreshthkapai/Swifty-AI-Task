from dataclasses import replace
from datetime import timedelta
import unittest

from webchat.domain.vehicles import VehicleAvailabilityStatus
from webchat.harness.actions import PendingActionType
from webchat.harness.contracts import (
    PreparationCommand,
    PreparationCommandName,
    ResponseStrategy,
    TurnPlan,
    TurnScope,
)
from webchat.harness.planning import TurnRequest
from webchat.harness.state import (
    ConversationState,
    CustomerState,
    VerificationGrant,
    WorkflowDomain,
    WorkflowStage,
    WorkflowState,
)

from tests.webchat.harness.test_runtime import (
    NOW,
    availability,
    dealer,
    runtime,
    workshop_booking,
)


CUSTOMER_STATE = CustomerState(
    "Jamie", "Taylor", "jamie@example.com", "07700900123", "AB12 CDE"
)


class PreparationWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def _prepare(self, name, arguments, *, state=None, configure=None):
        fake = dealer()
        if configure is not None:
            configure(fake)
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (PreparationCommand.from_mapping(name, arguments),),
            ResponseStrategy.ACTION_PREPARED,
        )
        harness, _ = runtime(fake, plan)
        result = await harness.handle(TurnRequest(
            current_input="Prepare this dealership request",
            state=state or ConversationState(customer=CUSTOMER_STATE),
            now=NOW,
        ))
        return fake, result

    async def test_every_consequential_capability_prepares_without_mutating(self) -> None:
        cases = (
            (
                PreparationCommandName.PREPARE_SALES_ENQUIRY,
                {"dealership_id": "northstar-manchester", "message": "Finance question"},
                PendingActionType.SALES_ENQUIRY,
                None,
            ),
            (
                PreparationCommandName.PREPARE_VEHICLE_INTEREST,
                {"vehicle_id": "veh-003"},
                PendingActionType.VEHICLE_INTEREST,
                lambda fake: setattr(fake.get_vehicle_availability, "return_value", availability(VehicleAvailabilityStatus.RESERVED)),
            ),
            (
                PreparationCommandName.PREPARE_CALLBACK,
                {"dealership_id": "northstar-manchester", "department": "sales", "reason": "Finance"},
                PendingActionType.CALLBACK,
                None,
            ),
            (
                PreparationCommandName.PREPARE_PART_EXCHANGE,
                {"dealership_id": "northstar-manchester", "registration": "AB12 CDE", "mileage": 50_000, "condition": "good"},
                PendingActionType.PART_EXCHANGE,
                None,
            ),
            (
                PreparationCommandName.PREPARE_WORKSHOP_BOOKING,
                {"slot_id": "ws-slot-1", "registration": "AB12 CDE", "mileage": 50_000},
                PendingActionType.WORKSHOP_BOOKING,
                None,
            ),
            (
                PreparationCommandName.PREPARE_DEALERSHIP_MESSAGE,
                {"dealership_id": "northstar-manchester", "department": "service", "message": "I will be ten minutes late"},
                PendingActionType.DEALERSHIP_MESSAGE,
                None,
            ),
        )

        for name, arguments, expected_type, configure in cases:
            with self.subTest(command=name):
                fake, result = await self._prepare(name, arguments, configure=configure)
                self.assertEqual(result.state.pending_action.action_type, expected_type)
                self.assertEqual(result.blocks[0].kind, "confirmation")
                for method in (
                    fake.create_sales_enquiry, fake.register_vehicle_interest,
                    fake.request_callback, fake.value_part_exchange,
                    fake.book_workshop, fake.send_dealership_message,
                ):
                    method.assert_not_awaited()

    async def test_verified_workshop_changes_prepare_but_do_not_execute(self) -> None:
        state = ConversationState(
            customer=CUSTOMER_STATE,
            verification_grants=(VerificationGrant.issue("wsb-1", now=NOW),),
        )
        cases = (
            (
                PreparationCommandName.PREPARE_WORKSHOP_AMENDMENT,
                {"booking_id": "wsb-1", "mileage": 51_000},
                PendingActionType.WORKSHOP_AMENDMENT,
            ),
            (
                PreparationCommandName.PREPARE_WORKSHOP_CANCELLATION,
                {"booking_id": "wsb-1"},
                PendingActionType.WORKSHOP_CANCELLATION,
            ),
        )
        for name, arguments, expected_type in cases:
            with self.subTest(command=name):
                fake, result = await self._prepare(
                    name, arguments, state=state,
                    configure=lambda item: setattr(item.get_workshop_booking, "return_value", workshop_booking()),
                )
                self.assertEqual(result.state.pending_action.action_type, expected_type)
                fake.amend_workshop_booking.assert_not_awaited()
                fake.cancel_workshop_booking.assert_not_awaited()

    async def test_service_callback_remains_in_workshop_workflow_and_marks_timing_as_preference(self) -> None:
        state = ConversationState(
            customer=CUSTOMER_STATE,
            workflow=WorkflowState(WorkflowDomain.WORKSHOP, WorkflowStage.DISCOVERY),
        )

        _, result = await self._prepare(
            PreparationCommandName.PREPARE_CALLBACK,
            {
                "dealership_id": "northstar-manchester",
                "department": "service",
                "reason": "Discuss my service",
                "preferred_time": "2pm tomorrow",
            },
            state=state,
        )

        self.assertEqual(result.state.workflow.domain, WorkflowDomain.WORKSHOP)
        self.assertIn("callback_timing", {
            block.to_dict()["payload"].get("code") for block in result.blocks
        })

    async def test_callback_resolves_customer_facing_dealership_reference(self) -> None:
        from tests.webchat.harness.test_runtime import location

        manchester = location()
        liverpool = replace(
            manchester,
            id="northstar-liverpool",
            name="Northstar Liverpool",
            address=replace(manchester.address, town="Liverpool"),
        )

        _, result = await self._prepare(
            PreparationCommandName.PREPARE_CALLBACK,
            {
                "dealership_query": "Liverpool",
                "department": "parts",
                "reason": "Parts availability",
            },
            configure=lambda fake: setattr(
                fake.list_dealerships,
                "return_value",
                (manchester, liverpool),
            ),
        )

        payload = result.state.pending_action.request_payload_dict()
        self.assertEqual(payload["dealership_id"], "northstar-liverpool")


if __name__ == "__main__":
    unittest.main()
