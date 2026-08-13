import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from webchat.harness.actions import (
    PendingAction,
    PendingActionState,
    PendingActionType,
    PendingRequestType,
)
from webchat.harness.contracts import freeze_json_object
from webchat.harness.scope import (
    DeterministicRouteKind,
    ScopeGate,
)
from webchat.harness.state import (
    ActionReference,
    ConversationState,
    PresentationGroup,
    PresentationProvenance,
    PresentedEntity,
    WorkflowDomain,
    WorkflowStage,
    WorkflowState,
)


NOW = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)


def pending_action(state: PendingActionState) -> PendingAction:
    return PendingAction(
        action_id="action-1",
        action_type=PendingActionType.TEST_DRIVE_BOOKING,
        request_type=PendingRequestType.TEST_DRIVE_BOOKING,
        request_payload=freeze_json_object(
            {"slot_id": "slot-1"},
            field="request_payload",
        ),
        state=state,
        idempotency_key="idem-1",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )


class ScopeGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = ScopeGate()

    def test_server_issued_ui_action_routes_without_text_classification(self) -> None:
        reference = ActionReference(
            action_id="vehicle-2",
            action_type="select_vehicle",
        )

        route = self.gate.route(
            text=None,
            state=ConversationState(),
            action_reference=reference,
            now=NOW,
        )

        self.assertEqual(route.kind, DeterministicRouteKind.UI_ACTION)
        self.assertEqual(route.action_reference, reference)

    def test_confirmation_requires_a_live_awaiting_action(self) -> None:
        active_state = ConversationState(
            pending_action=pending_action(PendingActionState.AWAITING_CONFIRMATION)
        )

        route = self.gate.route(text="Yes, confirm", state=active_state, now=NOW)
        no_action_route = self.gate.route(
            text="Yes, confirm", state=ConversationState(), now=NOW
        )

        self.assertEqual(route.kind, DeterministicRouteKind.CONFIRM_PENDING_ACTION)
        self.assertIsNone(no_action_route)

    def test_cancel_routes_only_when_an_action_is_pending(self) -> None:
        state = ConversationState(
            pending_action=pending_action(PendingActionState.AWAITING_CONFIRMATION)
        )

        for text in ("cancel", "no", "no thanks"):
            with self.subTest(text=text):
                route = self.gate.route(text=text, state=state, now=NOW)
                self.assertEqual(
                    route.kind,
                    DeterministicRouteKind.CANCEL_PENDING_ACTION,
                )

    def test_retry_requires_a_failed_pending_action(self) -> None:
        failed = ConversationState(
            pending_action=pending_action(PendingActionState.FAILED)
        )
        awaiting = ConversationState(
            pending_action=pending_action(PendingActionState.AWAITING_CONFIRMATION)
        )

        retry_route = self.gate.route(text="retry", state=failed, now=NOW)
        awaiting_route = self.gate.route(text="retry", state=awaiting, now=NOW)

        self.assertEqual(retry_route.kind, DeterministicRouteKind.RETRY_FAILED_ACTION)
        self.assertIsNone(awaiting_route)

    def test_exact_pagination_event_routes_deterministically(self) -> None:
        state = ConversationState(
            presentation_groups=(
                PresentationGroup(
                    group_id="vehicles-1",
                    entity_type="vehicle",
                    entities=(PresentedEntity("vehicle-1", 1),),
                    snapshot_at=NOW,
                    provenance=PresentationProvenance.DEALER_API,
                ),
            )
        )

        route = self.gate.route(text="show more", state=state, now=NOW)
        without_results = self.gate.route(
            text="show more",
            state=ConversationState(),
            now=NOW,
        )

        self.assertEqual(route.kind, DeterministicRouteKind.SHOW_MORE_RESULTS)
        self.assertIsNone(without_results)

    def test_ordinal_selection_routes_only_against_presented_entities(self) -> None:
        state = ConversationState(
            presentation_groups=(
                PresentationGroup(
                    group_id="vehicles-1",
                    entity_type="vehicle",
                    entities=(
                        PresentedEntity("vehicle-1", 1),
                        PresentedEntity("vehicle-2", 2),
                    ),
                    snapshot_at=NOW,
                    provenance=PresentationProvenance.DEALER_API,
                ),
            )
        )

        route = self.gate.route(text="the second one", state=state, now=NOW)

        self.assertEqual(route.kind, DeterministicRouteKind.SELECT_PRESENTED_ENTITY)
        self.assertEqual(route.ordinal, 2)
        self.assertIsNone(self.gate.route(text="the third one", state=state, now=NOW))

    def test_repeat_confirmation_of_succeeded_action_stays_zero_model(self) -> None:
        state = ConversationState(
            pending_action=replace(
                pending_action(PendingActionState.AWAITING_CONFIRMATION),
                state=PendingActionState.SUCCEEDED,
            )
        )

        route = self.gate.route(text="confirm", state=state, now=NOW)

        self.assertEqual(route.kind, DeterministicRouteKind.CONFIRM_PENDING_ACTION)

    def test_start_over_routes_only_when_there_is_workflow_state_to_clear(self) -> None:
        active = ConversationState(
            workflow=WorkflowState(WorkflowDomain.VEHICLES, WorkflowStage.REFINING)
        )

        route = self.gate.route(text="start over", state=active, now=NOW)

        self.assertEqual(route.kind, DeterministicRouteKind.START_OVER)
        self.assertIsNone(
            self.gate.route(text="start over", state=ConversationState(), now=NOW)
        )

    def test_standalone_obvious_trivia_redirects(self) -> None:
        for text in (
            "What size is the moon?",
            "Who won the World Cup?",
            "How do I bake a chocolate cake?",
        ):
            with self.subTest(text=text):
                route = self.gate.route(text=text, state=ConversationState(), now=NOW)
                self.assertEqual(route.kind, DeterministicRouteKind.DOMAIN_REDIRECT)

    def test_adjacent_ambiguous_and_mixed_requests_reach_the_planner(self) -> None:
        messages = (
            "Is an SUV good for a family of five?",
            "I'm moving to Manchester and need something big enough for two kids.",
            "How big is the moon, and will my telescope fit in this X3?",
            "Who won the World Cup, and do you have BMW SUVs?",
        )

        for text in messages:
            with self.subTest(text=text):
                self.assertIsNone(
                    self.gate.route(text=text, state=ConversationState(), now=NOW)
                )

        contextual = ConversationState(
            presentation_groups=(
                PresentationGroup(
                    group_id="vehicles-1", entity_type="vehicle",
                    entities=(PresentedEntity("veh-003", 1),), snapshot_at=NOW,
                    provenance=PresentationProvenance.DEALER_API,
                ),
            )
        )
        self.assertIsNone(
            self.gate.route(
                text="How big is the moon, and will my telescope fit in it?",
                state=contextual,
                now=NOW,
            )
        )

    def test_blank_or_broad_action_language_reaches_the_planner(self) -> None:
        for text in (None, "", "do it", "yes, I am looking for an SUV"):
            with self.subTest(text=text):
                self.assertIsNone(
                    self.gate.route(text=text, state=ConversationState(), now=NOW)
                )

    def test_expired_action_is_not_confirmed(self) -> None:
        state = ConversationState(
            pending_action=pending_action(PendingActionState.AWAITING_CONFIRMATION)
        )

        route = self.gate.route(
            text="confirm",
            state=state,
            now=NOW + timedelta(minutes=11),
        )

        self.assertIsNone(route)


if __name__ == "__main__":
    unittest.main()
