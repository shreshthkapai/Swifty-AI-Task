import json
import unittest
from datetime import UTC, datetime, timedelta

from webchat.harness.actions import (
    PendingAction,
    PendingActionState,
    PendingActionType,
    PendingRequestType,
)
from webchat.harness.contracts import (
    PreparationCommandName,
    ReadCommandName,
    freeze_json_object,
)
from webchat.harness.state import (
    ConversationState,
    WorkflowDomain,
    WorkflowStage,
    WorkflowState,
)
from webchat.harness.tool_gate import (
    ExclusionReason,
    InclusionReason,
    ToolGate,
    command_catalogue,
    command_spec,
)


NOW = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)


def state_for(
    domain: WorkflowDomain,
    *,
    pending: PendingAction | None = None,
) -> ConversationState:
    return ConversationState(
        workflow=WorkflowState(domain=domain, stage=WorkflowStage.DISCOVERY),
        pending_action=pending,
    )


def pending_action(state: PendingActionState) -> PendingAction:
    return PendingAction(
        action_id="action-1",
        action_type=PendingActionType.WORKSHOP_BOOKING,
        request_type=PendingRequestType.WORKSHOP_BOOKING,
        request_payload=freeze_json_object(
            {"slot_id": "slot-1"},
            field="request_payload",
        ),
        state=state,
        idempotency_key="idem-1",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )


class ToolGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = ToolGate()

    def test_catalogue_covers_every_provider_visible_command_once(self) -> None:
        expected = {item.value for item in ReadCommandName} | {
            item.value for item in PreparationCommandName
        }

        catalogue = command_catalogue()

        self.assertEqual({item.name for item in catalogue}, expected)
        self.assertEqual(len(catalogue), len(expected))
        self.assertTrue(all(item.description for item in catalogue))
        self.assertTrue(
            all(item.argument_schema.get("additionalProperties") is False for item in catalogue)
        )

    def test_dealership_bound_commands_accept_customer_facing_reference(self) -> None:
        for name in (
            "search_vehicles",
            "find_test_drive_slots",
            "find_workshop_slots",
            "get_dealership_details",
            "get_dealership_hours",
            "prepare_callback",
            "prepare_dealership_message",
        ):
            with self.subTest(command=name):
                properties = command_spec(name).argument_schema["properties"]
                self.assertIn("dealership_query", properties)
                self.assertIn("customer-facing", command_spec(name).description)

    def test_workshop_amendment_can_express_an_ordinal_slot_intent(self) -> None:
        properties = command_spec("prepare_workshop_amendment").argument_schema[
            "properties"
        ]

        self.assertEqual(
            properties["slot_ordinal"],
            {"type": ["integer", "null"], "minimum": 1},
        )
        self.assertIn("date_from", properties)
        self.assertIn("date_to", properties)

    def test_vehicle_filter_schema_uses_supported_catalogue_values(self) -> None:
        properties = command_spec("search_vehicles").argument_schema["properties"]

        self.assertEqual(
            properties["body_style"]["enum"],
            ["SUV", "Hatchback", "Saloon", "Estate", None],
        )
        self.assertEqual(
            properties["fuel_type"]["enum"],
            ["Petrol", "Diesel", "Hybrid", "Electric", None],
        )
        self.assertNotIn("small", properties["body_style"]["enum"])
        self.assertEqual(properties["exclude_vehicle_ids"]["items"], {"type": "string"})
        self.assertEqual(properties["exclude_models"]["items"], {"type": "string"})
        self.assertEqual(properties["exclude_makes"]["items"], {"type": "string"})

    def test_no_active_workflow_keeps_all_dealership_entry_options_visible(self) -> None:
        selection = self.gate.select(
            state=ConversationState(),
            current_input="I need some help",
            now=NOW,
        )

        expected = {item.name for item in command_catalogue()}
        self.assertEqual(set(selection.included_names), expected)
        self.assertEqual(selection.excluded, ())
        self.assertTrue(
            all(
                item.reason is InclusionReason.NO_ACTIVE_WORKFLOW
                for item in selection.included
            )
        )

    def test_initial_vehicle_signal_hides_unrelated_preparations(self) -> None:
        selection = self.gate.select(
            state=ConversationState(),
            current_input="Show me electric SUVs under fifty thousand pounds",
            now=NOW,
        )

        self.assertIn("search_vehicles", selection.included_names)
        self.assertIn("get_vehicle_details", selection.included_names)
        self.assertIn("compare_vehicles", selection.included_names)
        self.assertIn("list_dealerships", selection.included_names)
        self.assertNotIn("prepare_workshop_booking", selection.included_names)
        self.assertNotIn("prepare_dealership_message", selection.included_names)
        self.assertEqual(
            selection.reason_for("get_vehicle_details"),
            InclusionReason.EXPLICIT_DOMAIN_SIGNAL,
        )

    def test_initial_mixed_signals_expose_union_of_relevant_domains(self) -> None:
        selection = self.gate.select(
            state=ConversationState(),
            current_input="Find an electric car and book my MOT service",
            now=NOW,
        )

        self.assertIn("compare_vehicles", selection.included_names)
        self.assertIn("prepare_workshop_booking", selection.included_names)
        self.assertNotIn("prepare_dealership_message", selection.included_names)
        self.assertEqual(
            selection.reason_for("prepare_workshop_booking"),
            InclusionReason.EXPLICIT_DOMAIN_SIGNAL,
        )

    def test_vehicle_worth_language_exposes_part_exchange(self) -> None:
        selection = self.gate.select(
            state=ConversationState(),
            current_input=(
                "What's my 2019 Kia Sportage worth in good condition at Manchester?"
            ),
            now=NOW,
        )

        self.assertIn("prepare_part_exchange", selection.included_names)
        self.assertEqual(
            selection.reason_for("prepare_part_exchange"),
            InclusionReason.EXPLICIT_DOMAIN_SIGNAL,
        )

    def test_active_workshop_flow_focuses_tools_and_keeps_safe_entry_reads(self) -> None:
        selection = self.gate.select(
            state=state_for(WorkflowDomain.WORKSHOP),
            current_input="Saturday morning please",
            now=NOW,
        )

        self.assertIn("find_workshop_slots", selection.included_names)
        self.assertIn("prepare_workshop_booking", selection.included_names)
        self.assertIn("prepare_callback", selection.included_names)
        self.assertIn("search_vehicles", selection.included_names)
        self.assertIn("list_dealerships", selection.included_names)
        self.assertNotIn("prepare_test_drive_booking", selection.included_names)
        self.assertEqual(
            selection.reason_for("prepare_test_drive_booking"),
            ExclusionReason.UNRELATED_DOMAIN,
        )

    def test_explicit_cross_domain_language_widens_visibility_without_changing_state(self) -> None:
        state = state_for(WorkflowDomain.WORKSHOP)

        selection = self.gate.select(
            state=state,
            current_input="Actually find me a BMW under thirty thousand for a test drive",
            now=NOW,
        )

        self.assertIn("get_vehicle_details", selection.included_names)
        self.assertIn("prepare_test_drive_booking", selection.included_names)
        self.assertEqual(state.workflow.domain, WorkflowDomain.WORKSHOP)
        self.assertEqual(
            selection.reason_for("get_vehicle_details"),
            InclusionReason.EXPLICIT_DOMAIN_SIGNAL,
        )

    def test_register_interest_signal_exposes_interest_preparation(self) -> None:
        selection = self.gate.select(
            state=state_for(WorkflowDomain.VEHICLES),
            current_input="Register my interest in that reserved car",
            now=NOW,
        )

        self.assertIn("prepare_vehicle_interest", selection.included_names)

    def test_clear_test_drive_intent_hides_competing_interest_preparation(self) -> None:
        selection = self.gate.select(
            state=ConversationState(),
            current_input="I want to test drive this sold one anyway",
            now=NOW,
        )

        self.assertIn("find_test_drive_slots", selection.included_names)
        self.assertIn("check_vehicle_availability", selection.included_names)
        self.assertNotIn("prepare_vehicle_interest", selection.included_names)

    def test_pending_action_removes_all_preparations_and_retains_controls(self) -> None:
        state = state_for(
            WorkflowDomain.WORKSHOP,
            pending=pending_action(PendingActionState.AWAITING_CONFIRMATION),
        )

        selection = self.gate.select(
            state=state,
            current_input="What other BMWs do you have?",
            now=NOW,
        )

        preparation_names = {item.value for item in PreparationCommandName}
        self.assertTrue(preparation_names.isdisjoint(selection.included_names))
        self.assertEqual(
            set(selection.action_controls),
            {"confirm_pending_action", "cancel_pending_action"},
        )
        self.assertEqual(
            selection.reason_for("prepare_workshop_booking"),
            ExclusionReason.PENDING_ACTION_COMPETITION,
        )
        self.assertIn("search_vehicles", selection.included_names)

    def test_failed_pending_action_exposes_retry_control(self) -> None:
        selection = self.gate.select(
            state=state_for(
                WorkflowDomain.WORKSHOP,
                pending=pending_action(PendingActionState.FAILED),
            ),
            current_input="What can I do?",
            now=NOW,
        )

        self.assertEqual(
            set(selection.action_controls),
            {"cancel_pending_action", "retry_failed_action"},
        )

    def test_executing_action_does_not_advertise_unsafe_cancellation(self) -> None:
        selection = self.gate.select(
            state=state_for(
                WorkflowDomain.WORKSHOP,
                pending=pending_action(PendingActionState.EXECUTING),
            ),
            current_input="What is happening?",
            now=NOW,
        )

        self.assertEqual(selection.action_controls, ())

    def test_diagnostics_are_canonical_and_account_for_every_command(self) -> None:
        selection = self.gate.select(
            state=state_for(WorkflowDomain.VEHICLES),
            current_input="something cheaper",
            now=NOW,
        )

        serialized = selection.to_json()
        decoded = json.loads(serialized)

        self.assertEqual(serialized, selection.to_json())
        self.assertEqual(decoded["tool_gate_policy_version"], 2)
        self.assertEqual(
            len(decoded["included"]) + len(decoded["excluded"]),
            len(command_catalogue()),
        )
        self.assertEqual(
            [item["name"] for item in decoded["included"]],
            sorted(selection.included_names),
        )


if __name__ == "__main__":
    unittest.main()
