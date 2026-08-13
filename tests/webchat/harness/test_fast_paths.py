from dataclasses import replace
from datetime import timedelta
import unittest

from webchat.domain.common import Page
from webchat.domain.errors import DealerErrorKind, DealerFailure
from webchat.harness.actions import (
    PendingAction,
    PendingActionState,
    PendingActionType,
    PendingRequestType,
)
from webchat.harness.contracts import ResponseStrategy, TurnPlan, TurnScope
from webchat.harness.contracts import ReadCommand, ReadCommandName
from webchat.harness.planning import TurnRequest
from webchat.harness.state import (
    ActionReference,
    ConversationState,
    CustomerState,
    MessageBlock,
    MessageRole,
    PresentationGroup,
    PresentationProvenance,
    PresentedEntity,
    WorkflowDomain,
    WorkflowStage,
    WorkflowState,
    StructuredMessage,
)

from tests.webchat.harness.test_runtime import (
    NOW,
    availability,
    dealer,
    drive_booking,
    drive_slot,
    runtime,
    vehicle,
)


def test_drive_action(state=PendingActionState.AWAITING_CONFIRMATION, failure=None):
    return PendingAction.from_mapping(
        action_id="pending-1", action_type=PendingActionType.TEST_DRIVE_BOOKING,
        request_type=PendingRequestType.TEST_DRIVE_BOOKING,
        request_payload={
            "slot_id": "td-slot-1", "vehicle_id": "veh-003",
            "customer": {"first_name": "Jamie", "last_name": "Taylor", "email": "jamie@example.com", "phone": "07700900123"},
        },
        state=state, idempotency_key="idem-1", created_at=NOW,
        expires_at=NOW + timedelta(minutes=15), last_failure=failure,
    )


class DeterministicFastPathTests(unittest.IsolatedAsyncioTestCase):
    def _runtime(self, fake):
        inert = TurnPlan(TurnScope.IN_DOMAIN, (), ResponseStrategy.ACKNOWLEDGEMENT)
        return runtime(fake, inert)

    async def test_cancel_and_start_over_make_zero_provider_or_dealer_calls(self) -> None:
        for text, state in (
            ("cancel", ConversationState(pending_action=test_drive_action())),
            ("start over", ConversationState(
                workflow=WorkflowState(WorkflowDomain.VEHICLES, WorkflowStage.REFINING),
                presentation_groups=(PresentationGroup(
                    "group-1", "vehicle", (PresentedEntity("veh-003", 1),), NOW,
                    PresentationProvenance.DEALER_API,
                ),),
            )),
        ):
            with self.subTest(text=text):
                fake = dealer()
                harness, provider = self._runtime(fake)
                result = await harness.handle(TurnRequest(current_input=text, state=state, now=NOW))
                self.assertEqual(provider.requests, [])
                self.assertEqual(fake.method_calls, [])
                self.assertIsNone(result.state.pending_action)

    async def test_retry_is_allowed_only_for_normalized_retryable_failure(self) -> None:
        fake = dealer()
        fake.get_vehicle_availability.return_value = availability()
        fake.list_test_drive_slots.return_value = (drive_slot(),)
        fake.book_test_drive.return_value = drive_booking()
        harness, provider = self._runtime(fake)
        retryable = DealerFailure(DealerErrorKind.TEMPORARY_FAILURE, retryable=True)

        result = await harness.handle(TurnRequest(
            current_input="retry",
            state=ConversationState(pending_action=test_drive_action(PendingActionState.FAILED, retryable)),
            now=NOW,
        ))

        self.assertEqual(provider.requests, [])
        fake.book_test_drive.assert_awaited_once()
        self.assertEqual(result.state.pending_action.state, PendingActionState.SUCCEEDED)

        fake = dealer()
        harness, _ = self._runtime(fake)
        not_retryable = DealerFailure(DealerErrorKind.SLOT_UNAVAILABLE)
        result = await harness.handle(TurnRequest(
            current_input="retry",
            state=ConversationState(pending_action=test_drive_action(PendingActionState.FAILED, not_retryable)),
            now=NOW,
        ))
        fake.book_test_drive.assert_not_awaited()
        self.assertEqual(result.blocks[0].to_dict()["payload"]["code"], "retry_not_allowed")

        fake = dealer()
        harness, _ = self._runtime(fake)
        result = await harness.handle(TurnRequest(
            current_input="retry",
            state=ConversationState(pending_action=test_drive_action(PendingActionState.FAILED)),
            now=NOW,
        ))
        fake.book_test_drive.assert_not_awaited()
        self.assertEqual(result.blocks[0].to_dict()["payload"]["code"], "retry_not_allowed")

    async def test_show_more_replays_structured_search_with_next_page(self) -> None:
        fake = dealer()
        fake.search_vehicles.return_value = Page((vehicle("veh-004"),), 2, 10, 11, 2)
        harness, provider = self._runtime(fake)
        state = ConversationState(
            workflow=WorkflowState(
                WorkflowDomain.VEHICLES, WorkflowStage.REFINING,
                {"last_vehicle_search": {"make": "BMW", "page": 1, "page_size": 10}},
            ),
            presentation_groups=(PresentationGroup(
                "group-1", "vehicle", (PresentedEntity("veh-003", 1),), NOW,
                PresentationProvenance.DEALER_API,
            ),),
        )

        result = await harness.handle(TurnRequest(current_input="show more", state=state, now=NOW))

        self.assertEqual(provider.requests, [])
        search = fake.search_vehicles.await_args.args[0]
        self.assertEqual(search.page, 2)
        self.assertEqual(result.state.presentation_groups[-1].entities[0].entity_id, "veh-004")

    async def test_unknown_ui_reference_is_rejected_before_dealer_call(self) -> None:
        fake = dealer()
        harness, provider = self._runtime(fake)

        result = await harness.handle(TurnRequest(
            current_input=None,
            action_reference=ActionReference("forged", "select_vehicle"),
            state=ConversationState(), now=NOW,
        ))

        self.assertEqual(provider.requests, [])
        fake.get_vehicle.assert_not_awaited()
        self.assertEqual(result.blocks[0].to_dict()["payload"]["code"], "invalid_action_reference")

    async def test_selecting_a_presented_test_drive_slot_prepares_it_without_model_call(self) -> None:
        fake = dealer()
        fake.list_test_drive_slots.return_value = (drive_slot(),)
        fake.get_vehicle_availability.return_value = availability()
        find_plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(
                ReadCommandName.FIND_TEST_DRIVE_SLOTS,
                {"vehicle_id": "veh-003"},
            ),),
            ResponseStrategy.SLOT_RESULTS,
        )
        finder, _ = runtime(fake, find_plan)
        state = ConversationState(customer=CustomerState(
            "Jamie", "Taylor", "jamie@example.com", "07700900123"
        ))
        found = await finder.handle(TurnRequest(
            current_input="Can I test drive this tomorrow?", state=state, now=NOW
        ))
        selector, provider = self._runtime(fake)

        selected = await selector.handle(TurnRequest(
            current_input="the first one", state=found.state, now=NOW
        ))

        self.assertEqual(provider.requests, [])
        self.assertEqual(selected.state.entities.selected_vehicle_id, "veh-003")
        self.assertEqual(
            selected.state.pending_action.action_type,
            PendingActionType.TEST_DRIVE_BOOKING,
        )

    async def test_server_issued_workflow_switch_supersedes_pending_action(self) -> None:
        fake = dealer()
        harness, provider = self._runtime(fake)
        action = test_drive_action()
        message = StructuredMessage(
            message_id="message-1", client_turn_id="turn-1",
            role=MessageRole.ASSISTANT, created_at=NOW,
            blocks=(MessageBlock(
                kind="actions",
                payload={"actions": [{
                    "action_id": "switch-1", "action_type": "switch_workflow",
                    "entity_id": "workshop", "label": "Workshop",
                }]},
                action_references=(ActionReference("switch-1", "switch_workflow"),),
            ),),
        )

        result = await harness.handle(TurnRequest(
            current_input=None,
            action_reference=ActionReference("switch-1", "switch_workflow"),
            state=ConversationState(pending_action=action),
            recent_messages=(message,), now=NOW,
        ))

        self.assertEqual(provider.requests, [])
        self.assertIsNone(result.state.pending_action)
        self.assertEqual(result.state.workflow.domain, WorkflowDomain.WORKSHOP)

    async def test_availability_action_finds_slots_without_another_model_call(self) -> None:
        fake = dealer()
        fake.get_vehicle_availability.return_value = availability()
        fake.list_test_drive_slots.return_value = (drive_slot(),)
        availability_plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(
                ReadCommandName.CHECK_VEHICLE_AVAILABILITY,
                {"vehicle_id": "veh-003"},
            ),),
            ResponseStrategy.AVAILABILITY_RESULT,
        )
        checker, _ = runtime(fake, availability_plan)
        checked = await checker.handle(TurnRequest(
            current_input="Is this available?", state=ConversationState(), now=NOW
        ))
        action_block = next(block for block in checked.blocks if block.kind == "actions")
        reference = action_block.action_references[0]
        message = StructuredMessage(
            message_id="message-2", client_turn_id="turn-2",
            role=MessageRole.ASSISTANT, created_at=NOW, blocks=checked.blocks,
        )
        finder, provider = self._runtime(fake)

        found = await finder.handle(TurnRequest(
            current_input=None, action_reference=reference, state=checked.state,
            recent_messages=(message,), now=NOW,
        ))

        self.assertEqual(provider.requests, [])
        self.assertIn("slot_choices", {block.kind for block in found.blocks})

    async def test_stale_ui_preparation_cannot_replace_a_live_pending_action(self) -> None:
        fake = dealer()
        harness, provider = self._runtime(fake)
        current = test_drive_action()
        message = StructuredMessage(
            message_id="message-3", client_turn_id="turn-3",
            role=MessageRole.ASSISTANT, created_at=NOW,
            blocks=(MessageBlock(
                kind="actions",
                payload={"actions": [{
                    "action_id": "interest-1", "action_type": "register_interest",
                    "entity_id": "veh-003", "label": "Register interest",
                }]},
                action_references=(ActionReference("interest-1", "register_interest"),),
            ),),
        )

        result = await harness.handle(TurnRequest(
            current_input=None,
            action_reference=ActionReference("interest-1", "register_interest"),
            state=ConversationState(pending_action=current),
            recent_messages=(message,), now=NOW,
        ))

        self.assertEqual(provider.requests, [])
        self.assertEqual(result.state.pending_action, current)
        self.assertEqual(result.blocks[0].to_dict()["payload"]["code"], "pending_action_exists")
        fake.get_vehicle_availability.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
