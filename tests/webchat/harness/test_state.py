import json
import unittest
from datetime import UTC, datetime, timedelta, timezone

from webchat.harness.actions import (
    PendingAction,
    PendingActionState,
    PendingActionType,
    PendingRequestType,
)
from webchat.harness.contracts import FrozenObject
from webchat.harness.state import (
    ActionReference,
    ConversationState,
    CustomerState,
    EntityContext,
    EntityReference,
    MessageBlock,
    MessageRole,
    PageContext,
    PresentationGroup,
    PresentedEntity,
    PresentationProvenance,
    StructuredMessage,
    VehiclePreferences,
    VerificationGrant,
    WorkflowDomain,
    WorkflowStage,
    WorkflowState,
)


class ConversationStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

    def make_group(self, index: int) -> PresentationGroup:
        return PresentationGroup(
            group_id=f"group-{index}",
            entity_type="vehicle",
            entities=(
                PresentedEntity(
                    entity_id=f"veh-{index}-a",
                    ordinal=1,
                    snapshot={"make": "BMW", "price_minor": 2_999_500},
                ),
                PresentedEntity(
                    entity_id=f"veh-{index}-b",
                    ordinal=2,
                    snapshot={"make": "MINI"},
                ),
            ),
            snapshot_at=self.now + timedelta(minutes=index),
            provenance=PresentationProvenance.DEALER_API,
        )

    def make_pending_action(self) -> PendingAction:
        return PendingAction.from_mapping(
            action_id="action-1",
            action_type=PendingActionType.TEST_DRIVE_BOOKING,
            request_type=PendingRequestType.TEST_DRIVE_BOOKING,
            request_payload={"slot_id": "td-slot-1"},
            state=PendingActionState.AWAITING_CONFIRMATION,
            idempotency_key="idem-1",
            created_at=self.now,
            expires_at=self.now + timedelta(minutes=10),
        )

    def test_state_round_trip_preserves_typed_sections(self) -> None:
        state = ConversationState(
            customer=CustomerState(first_name="Alex", phone="07123456789"),
            context=PageContext(
                current_url="http://localhost:4173/?vehicle=veh-019",
                page_vehicle_id="veh-019",
                search_filters={
                    "make": "BMW",
                    "body_style": "SUV",
                    "max_price_minor": 3_000_000,
                },
                observed_at=self.now,
            ),
            entities=EntityContext(
                selected_vehicle_id="veh-019",
                selected_dealer_id="dealer-manchester",
            ),
            preferences=VehiclePreferences(
                maximum_price_minor=3_000_000,
                currency="GBP",
                makes=("BMW",),
                transmission="automatic",
            ),
            workflow=WorkflowState(
                domain=WorkflowDomain.TEST_DRIVE,
                stage=WorkflowStage.SELECTING_SLOT,
                gathered_fields={"vehicle_id": "veh-019"},
                missing_fields=("phone",),
            ),
            pending_action=self.make_pending_action(),
            verification_grants=(
                VerificationGrant.issue("booking-1", now=self.now),
            ),
            presentation_groups=(self.make_group(1),),
        )

        restored = ConversationState.from_json(state.to_json())

        self.assertEqual(restored, state)
        self.assertEqual(restored.schema_version, 1)
        self.assertFalse(restored.context.is_authoritative)
        self.assertEqual(
            restored.context.search_filters_dict(),
            {"body_style": "SUV", "make": "BMW", "max_price_minor": 3_000_000},
        )
        self.assertEqual(restored.entities.selected_vehicle_id, "veh-019")
        self.assertFalse(restored.presentation_groups[0].is_authoritative)

    def test_state_canonical_serialization_is_byte_identical(self) -> None:
        first = ConversationState(
            workflow=WorkflowState(
                gathered_fields={"z": 1, "a": {"second": 2, "first": 1}}
            )
        )
        second = ConversationState(
            workflow=WorkflowState(
                gathered_fields={"a": {"first": 1, "second": 2}, "z": 1}
            )
        )

        self.assertEqual(first.to_json(), second.to_json())
        self.assertEqual(json.loads(first.to_json())["schema_version"], 1)

    def test_equivalent_datetime_offsets_have_one_canonical_serialization(self) -> None:
        utc_state = ConversationState(
            verification_grants=(VerificationGrant.issue("booking-1", now=self.now),)
        )
        offset_now = self.now.astimezone(timezone(timedelta(hours=2)))
        offset_state = ConversationState(
            verification_grants=(VerificationGrant.issue("booking-1", now=offset_now),)
        )

        self.assertEqual(utc_state.to_json(), offset_state.to_json())

    def test_presentation_history_retains_only_last_five_groups(self) -> None:
        state = ConversationState()

        for index in range(6):
            state = state.with_presentation_group(self.make_group(index))

        self.assertEqual(
            tuple(group.group_id for group in state.presentation_groups),
            ("group-1", "group-2", "group-3", "group-4", "group-5"),
        )
        self.assertEqual(
            state.presentation_groups[-1].resolve_ordinal(2).entity_id,
            "veh-5-b",
        )

    def test_presentation_group_rejects_unstable_order_or_authority(self) -> None:
        with self.assertRaisesRegex(ValueError, "contiguous"):
            PresentationGroup(
                group_id="group-1",
                entity_type="vehicle",
                entities=(PresentedEntity("veh-1", 2),),
                snapshot_at=self.now,
                provenance=PresentationProvenance.DEALER_API,
            )
        with self.assertRaisesRegex(ValueError, "non-authoritative"):
            PresentationGroup(
                group_id="group-1",
                entity_type="vehicle",
                entities=(PresentedEntity("veh-1", 1),),
                snapshot_at=self.now,
                provenance=PresentationProvenance.DEALER_API,
                is_authoritative=True,
            )

    def test_verification_grant_defaults_to_fifteen_minutes_and_expires(self) -> None:
        grant = VerificationGrant.issue("booking-1", now=self.now)

        self.assertEqual(grant.expires_at, self.now + timedelta(minutes=15))
        self.assertTrue(grant.is_active(self.now + timedelta(minutes=14, seconds=59)))
        self.assertFalse(grant.is_active(self.now + timedelta(minutes=15)))

    def test_active_grant_is_scoped_to_booking_and_explicit_time(self) -> None:
        state = ConversationState(
            verification_grants=(VerificationGrant.issue("booking-1", now=self.now),)
        )

        self.assertTrue(state.has_active_grant("booking-1", now=self.now))
        self.assertFalse(state.has_active_grant("booking-2", now=self.now))
        self.assertFalse(
            state.has_active_grant(
                "booking-1", now=self.now + timedelta(minutes=16)
            )
        )

    def test_unknown_state_schema_is_rejected(self) -> None:
        payload = ConversationState().to_dict()
        payload["schema_version"] = 99

        with self.assertRaisesRegex(ValueError, "unsupported ConversationState"):
            ConversationState.from_dict(payload)

    def test_workflow_stage_is_a_closed_contract(self) -> None:
        with self.assertRaisesRegex(ValueError, "WorkflowStage"):
            WorkflowState(stage="whatever-the-model-said")

    def test_browser_page_context_is_separate_from_selected_entities(self) -> None:
        state = ConversationState(
            context=PageContext(
                page_vehicle_id="veh-page",
                observed_at=self.now,
            ),
            entities=EntityContext(selected_vehicle_id="veh-selected"),
        )

        restored = ConversationState.from_json(state.to_json())

        self.assertFalse(restored.context.is_authoritative)
        self.assertEqual(restored.context.page_vehicle_id, "veh-page")
        self.assertEqual(restored.entities.selected_vehicle_id, "veh-selected")

    def test_browser_page_context_rejects_unknown_or_non_scalar_search_filters(self) -> None:
        with self.assertRaisesRegex(ValueError, "search_filters"):
            PageContext(search_filters={"customer_email": "alex@example.com"})
        with self.assertRaisesRegex(ValueError, "search_filters"):
            PageContext(search_filters={"make": ["BMW"]})

    def test_vehicle_preference_currency_rejects_non_ascii_codes(self) -> None:
        with self.assertRaisesRegex(ValueError, "ISO 4217"):
            VehiclePreferences(currency="ÉUR")

    def test_direct_state_payloads_must_remain_canonical(self) -> None:
        with self.assertRaisesRegex(ValueError, "canonical"):
            WorkflowState(
                gathered_fields=FrozenObject((("z", 1), ("a", 2))),
            )


class StructuredMessageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

    def test_message_round_trip_preserves_declarative_references(self) -> None:
        message = StructuredMessage(
            message_id="message-1",
            client_turn_id="turn-1",
            role=MessageRole.ASSISTANT,
            created_at=self.now,
            text="I found one matching vehicle.",
            blocks=(
                MessageBlock(
                    kind="vehicle_cards",
                    payload={"heading": "Matches"},
                    entity_references=(EntityReference("vehicle", "veh-019"),),
                    action_references=(
                        ActionReference("select-veh-019", "select_vehicle"),
                    ),
                ),
            ),
        )

        restored = StructuredMessage.from_json(message.to_json())

        self.assertEqual(restored, message)
        self.assertEqual(restored.schema_version, 1)

    def test_message_schema_is_independent_from_state_schema(self) -> None:
        payload = StructuredMessage(
            message_id="message-1",
            client_turn_id="turn-1",
            role=MessageRole.USER,
            created_at=self.now,
            text="Show me an X3",
        ).to_dict()
        payload["schema_version"] = 99

        with self.assertRaisesRegex(ValueError, "unsupported StructuredMessage"):
            StructuredMessage.from_dict(payload)

    def test_message_rejects_executable_or_non_json_payloads(self) -> None:
        with self.assertRaisesRegex(ValueError, "JSON"):
            MessageBlock(kind="actions", payload={"handler": lambda: None})


if __name__ == "__main__":
    unittest.main()
