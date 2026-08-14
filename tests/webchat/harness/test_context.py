import json
import unittest
from datetime import UTC, datetime, timedelta, timezone

from webchat.domain.errors import DealerErrorKind, DealerFailure, FieldViolation
from webchat.harness.context import (
    CompilerOmissionReason,
    ContextCompiler,
)
from webchat.harness.state import (
    ConversationState,
    CustomerState,
    EntityContext,
    MessageRole,
    PageContext,
    PresentationGroup,
    PresentationProvenance,
    PresentedEntity,
    StructuredMessage,
    VerificationGrant,
    VehiclePreferences,
    WorkflowDomain,
    WorkflowStage,
    WorkflowState,
)
from webchat.harness.tool_gate import ToolGate


NOW = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)


def message(number: int, text: str) -> StructuredMessage:
    return StructuredMessage(
        message_id=f"message-{number}",
        client_turn_id=f"turn-{number}",
        role=MessageRole.USER if number % 2 else MessageRole.ASSISTANT,
        created_at=NOW - timedelta(minutes=10 - number),
        text=text,
    )


def presentation(number: int) -> PresentationGroup:
    return PresentationGroup(
        group_id=f"vehicles-{number}",
        entity_type="vehicle",
        entities=(
            PresentedEntity(
                entity_id=f"veh-{number}-1",
                ordinal=1,
                snapshot={"label": "BMW X3", "price_minor": 2_999_500},
            ),
            PresentedEntity(
                entity_id=f"veh-{number}-2",
                ordinal=2,
                snapshot={"label": "MINI Countryman", "price_minor": 2_500_000},
            ),
        ),
        snapshot_at=NOW - timedelta(minutes=number),
        provenance=PresentationProvenance.DEALER_API,
    )


def populated_state() -> ConversationState:
    return ConversationState(
        customer=CustomerState(
            first_name="Alice",
            last_name="Jones",
            email="old@example.com",
            phone="07123456789",
            registration="AB12 CDE",
        ),
        context=PageContext(
            current_url="http://localhost:4173/?vehicle=veh-1",
            page_vehicle_id="veh-1",
            observed_at=NOW - timedelta(seconds=5),
        ),
        entities=EntityContext(
            selected_vehicle_id="veh-1",
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
            gathered_fields={"date": "2026-08-15"},
            missing_fields=("slot_id",),
        ),
        presentation_groups=(presentation(2), presentation(1)),
    )


class ContextCompilerTests(unittest.TestCase):
    def compile(self, **overrides):
        state = overrides.pop("state", populated_state())
        current_input = overrides.pop(
            "current_input", "My new email is new@example.com; can I test drive the second one?"
        )
        now = overrides.pop("now", NOW)
        selection = overrides.pop(
            "tool_selection",
            ToolGate().select(state=state, current_input=current_input, now=now),
        )
        return ContextCompiler(**overrides.pop("compiler_options", {})).compile(
            current_input=current_input,
            now=now,
            state=state,
            tool_selection=selection,
            recent_messages=overrides.pop("recent_messages", ()),
            prior_failures=overrides.pop("prior_failures", ()),
            **overrides,
        )

    def test_identical_inputs_and_instants_compile_to_identical_bytes(self) -> None:
        first = self.compile(now=NOW)
        equivalent = self.compile(now=NOW.astimezone(timezone(timedelta(hours=1))))

        self.assertEqual(first.serialized, equivalent.serialized)
        self.assertEqual(first.diagnostics.to_json(), equivalent.diagnostics.to_json())

    def test_current_input_keeps_new_pii_while_historical_pii_is_redacted(self) -> None:
        compiled = self.compile(
            recent_messages=(
                message(
                    1,
                    "Alice Jones used old@example.com, 07123456789, +1 202 555 0123 and AB12 CDE.",
                ),
            )
        )

        self.assertIn("new@example.com", compiled.serialized)
        self.assertNotIn("old@example.com", compiled.serialized)
        self.assertNotIn("07123456789", compiled.serialized)
        self.assertNotIn("+1 202 555 0123", compiled.serialized)
        self.assertNotIn("AB12 CDE", compiled.serialized)
        self.assertNotIn("Alice Jones", compiled.serialized)
        customer = next(
            section["content"]
            for section in json.loads(compiled.serialized)["sections"]
            if section["name"] == "customer_presence"
        )
        self.assertEqual(customer["known_fields"], [
            "email",
            "first_name",
            "last_name",
            "phone",
            "registration",
        ])

    def test_persisted_workflow_pii_is_represented_as_presence_not_values(self) -> None:
        state = populated_state()
        state = ConversationState(
            customer=state.customer,
            workflow=WorkflowState(
                domain=WorkflowDomain.TEST_DRIVE,
                stage=WorkflowStage.COLLECTING_DETAILS,
                gathered_fields={
                    "email": "old@example.com",
                    "phone": "07123456789",
                    "date": "2026-08-15",
                    "notes": "Alternative unknown@example.net",
                },
            ),
        )

        compiled = self.compile(
            state=state,
            current_input="Saturday works",
        )
        workflow = next(
            section["content"]
            for section in json.loads(compiled.serialized)["sections"]
            if section["name"] == "workflow"
        )

        self.assertNotIn("old@example.com", compiled.serialized)
        self.assertNotIn("07123456789", compiled.serialized)
        self.assertNotIn("unknown@example.net", compiled.serialized)
        self.assertEqual(workflow["gathered_fields"]["date"], "2026-08-15")
        self.assertNotIn("email", workflow["gathered_fields"])
        self.assertNotIn("phone", workflow["gathered_fields"])
        self.assertEqual(workflow["sensitive_fields_known"], ["email", "phone"])

    def test_presentation_order_ordinals_provenance_and_snapshot_authority_are_explicit(self) -> None:
        compiled = self.compile()
        sections = json.loads(compiled.serialized)["sections"]
        presentations = [
            section for section in sections if section["name"].startswith("presentation:")
        ]

        self.assertEqual(
            [section["name"] for section in presentations],
            ["presentation:vehicles-1", "presentation:vehicles-2"],
        )
        self.assertEqual(
            [entity["ordinal"] for entity in presentations[0]["content"]["entities"]],
            [1, 2],
        )
        self.assertEqual(presentations[0]["content"]["provenance"], "dealer_api")
        self.assertFalse(presentations[0]["content"]["is_authoritative"])
        self.assertEqual(presentations[0]["authority"], "historical_snapshot")

    def test_history_limit_is_bounded_and_reported(self) -> None:
        compiled = self.compile(
            recent_messages=tuple(message(index, f"message {index}") for index in range(1, 6)),
            compiler_options={"max_history_messages": 2},
        )
        history = next(
            section["content"]
            for section in json.loads(compiled.serialized)["sections"]
            if section["name"] == "recent_history"
        )

        self.assertEqual(
            [item["message_id"] for item in history["messages"]],
            ["message-4", "message-5"],
        )
        self.assertTrue(
            any(
                item.reason is CompilerOmissionReason.HISTORY_LIMIT
                for item in compiled.diagnostics.omitted
            )
        )

    def test_low_priority_sections_are_omitted_under_both_budgets(self) -> None:
        compiled = self.compile(
            recent_messages=tuple(message(index, "x" * 300) for index in range(1, 6)),
            compiler_options={"max_chars": 2_500, "max_estimated_tokens": 625},
        )

        self.assertLessEqual(len(compiled.serialized), 2_500)
        self.assertLessEqual(compiled.diagnostics.estimated_tokens, 625)
        self.assertTrue(
            any(
                item.reason is CompilerOmissionReason.BUDGET_EXCEEDED
                for item in compiled.diagnostics.omitted
            )
        )

    def test_page_context_is_labelled_as_refreshable_observation(self) -> None:
        compiled = self.compile(
            page_observation=PageContext(
                current_url="/#vehicles",
                page_vehicle_id="veh-1",
                search_filters={"make": "BMW", "body_style": "SUV"},
                observed_at=NOW,
            )
        )
        page = next(
            section
            for section in json.loads(compiled.serialized)["sections"]
            if section["name"] == "page_observation"
        )

        self.assertEqual(page["authority"], "refreshable_observation")
        self.assertFalse(page["content"]["is_authoritative"])
        self.assertEqual(
            page["content"]["search_filters"],
            {"body_style": "SUV", "make": "BMW"},
        )

    def test_failures_are_normalized_without_free_form_messages(self) -> None:
        failure = DealerFailure(
            kind=DealerErrorKind.VALIDATION,
            field_violations=(
                FieldViolation(
                    field="phone",
                    code="invalid",
                    message="Alice entered 07123456789",
                ),
            ),
        )

        compiled = self.compile(prior_failures=(failure,))

        self.assertIn('"kind":"validation"', compiled.serialized)
        self.assertIn('"code":"invalid"', compiled.serialized)
        self.assertNotIn("Alice entered", compiled.serialized)
        self.assertNotIn("07123456789", compiled.serialized)

    def test_only_active_verification_grants_are_compiled_with_expiry(self) -> None:
        state = ConversationState(
            workflow=WorkflowState(
                domain=WorkflowDomain.WORKSHOP,
                stage=WorkflowStage.DISCOVERY,
            ),
            verification_grants=(
                VerificationGrant(
                    booking_id="booking-active",
                    granted_at=NOW - timedelta(minutes=5),
                    expires_at=NOW + timedelta(minutes=10),
                ),
                VerificationGrant(
                    booking_id="booking-expired",
                    granted_at=NOW - timedelta(minutes=20),
                    expires_at=NOW - timedelta(minutes=5),
                ),
            ),
        )

        compiled = self.compile(
            state=state,
            current_input="Can I cancel that booking?",
        )
        authorization = next(
            section["content"]
            for section in json.loads(compiled.serialized)["sections"]
            if section["name"] == "authorization"
        )

        self.assertEqual(
            authorization["active_workshop_booking_grants"],
            [
                {
                    "booking_id": "booking-active",
                    "expires_at": "2026-08-13T10:10:00Z",
                }
            ],
        )
        self.assertNotIn("booking-expired", compiled.serialized)

    def test_historical_snapshots_page_and_failure_resources_are_pii_sanitized(self) -> None:
        state = ConversationState(
            customer=CustomerState(email="old@example.com", phone="07123456789"),
            context=PageContext(
                current_url="http://localhost:4173/?email=old@example.com",
                observed_at=NOW,
            ),
            presentation_groups=(
                PresentationGroup(
                    group_id="workshop-1",
                    entity_type="workshop_booking",
                    entities=(
                        PresentedEntity(
                            entity_id="booking-1",
                            ordinal=1,
                            snapshot={"contact": "old@example.com"},
                        ),
                    ),
                    snapshot_at=NOW,
                    provenance=PresentationProvenance.DEALER_API,
                ),
            ),
        )
        failure = DealerFailure(
            kind=DealerErrorKind.NOT_FOUND,
            resource="old@example.com",
        )

        compiled = self.compile(
            state=state,
            current_input="What happened?",
            prior_failures=(failure,),
        )

        self.assertNotIn("old@example.com", compiled.serialized)

    def test_diagnostics_record_policy_versions_order_and_exact_size(self) -> None:
        compiled = self.compile()
        diagnostics = json.loads(compiled.diagnostics.to_json())

        self.assertEqual(diagnostics["compiler_policy_version"], 1)
        self.assertEqual(diagnostics["tool_gate_policy_version"], 2)
        self.assertEqual(diagnostics["compiled_chars"], len(compiled.serialized))
        self.assertEqual(
            diagnostics["selected_order"],
            [item.name for item in compiled.diagnostics.selected],
        )

    def test_empty_optional_sections_are_reported_as_omitted(self) -> None:
        compiled = self.compile(
            state=ConversationState(),
            current_input="Help me choose a car",
        )

        empty_names = {
            item.name
            for item in compiled.diagnostics.omitted
            if item.reason is CompilerOmissionReason.EMPTY
        }

        self.assertEqual(
            empty_names,
            {
                "authorization",
                "page_observation",
                "pending_action",
                "preferences",
                "presentation_groups",
                "prior_failures",
                "recent_history",
            },
        )

    def test_vehicle_preferences_are_omitted_when_unrelated_to_active_turn(self) -> None:
        state = ConversationState(
            preferences=VehiclePreferences(makes=("BMW",)),
            workflow=WorkflowState(
                domain=WorkflowDomain.WORKSHOP,
                stage=WorkflowStage.SELECTING_SLOT,
            ),
        )

        compiled = self.compile(
            state=state,
            current_input="Saturday morning please",
        )

        self.assertNotIn('"name":"preferences"', compiled.serialized)
        preference_omission = next(
            item for item in compiled.diagnostics.omitted if item.name == "preferences"
        )
        self.assertIs(preference_omission.reason, CompilerOmissionReason.NOT_RELEVANT)


if __name__ == "__main__":
    unittest.main()
