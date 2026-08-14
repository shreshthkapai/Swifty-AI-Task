from datetime import UTC, datetime
import unittest

from evals.fixtures import DealerSnapshot
from evals.observe import (
    OBSERVABLE_BLOCK_TYPES,
    OBSERVABLE_FACTS,
    OBSERVABLE_NEXT_STEPS,
    OBSERVABLE_NOTICE_KEYS,
    observe_turn,
)
from evals.schema import load_corpus
from evals.schema import ResponseStrategy
from webchat.harness.contracts import ReadCommand, ReadCommandName
from webchat.harness.evidence import (
    EvidenceAuthority,
    EvidenceFreshness,
    EvidenceItem,
)
from webchat.harness.turn import TurnResult
from webchat.harness.state import ConversationState, MessageBlock


class ObservationTests(unittest.TestCase):
    def test_no_vehicle_result_notice_drives_zero_result_semantics(self) -> None:
        result = TurnResult(
            state=ConversationState(),
            blocks=(MessageBlock("notice", {
                "schema_version": 1,
                "code": "no_vehicle_results",
                "text": "No exact stock match. Would you like to relax the budget?",
            }),),
        )

        observed = observe_turn(
            result,
            before_state=ConversationState(),
            before_dealer=DealerSnapshot((), ()),
            after_dealer=DealerSnapshot((), ()),
            commands=(),
            response_strategy=ResponseStrategy.RECOVERY,
        )

        self.assertIn("zero_results", observed.answer.facts)
        self.assertIn("relax_constraints", observed.answer.next_steps)

    def test_zero_vehicle_result_is_derived_from_structured_evidence_not_wording(self) -> None:
        result = TurnResult(
            state=ConversationState(),
            blocks=(MessageBlock("text", {
                "schema_version": 1,
                "text": "None of the available stock satisfies all of those preferences.",
            }),),
            evidence=(EvidenceItem(
                source_operation="search_vehicles",
                entity_id="vehicle_search",
                field_name="result_count",
                value=0,
                authority=EvidenceAuthority.DEALER,
                freshness=EvidenceFreshness.LIVE,
                observed_at=datetime(2026, 8, 14, 10, tzinfo=UTC),
            ),),
        )

        observed = observe_turn(
            result,
            before_state=ConversationState(),
            before_dealer=DealerSnapshot((), ()),
            after_dealer=DealerSnapshot((), ()),
            commands=(ReadCommand.from_mapping(
                ReadCommandName.SEARCH_VEHICLES,
                {"body_style": "SUV"},
            ),),
            response_strategy=ResponseStrategy.SEARCH_RESULTS,
        )

        self.assertIn("zero_results", observed.answer.facts)
        self.assertIn("relax_constraints", observed.answer.next_steps)

    def test_price_conflict_clarification_is_not_mislabeled_customer_details(self) -> None:
        result = TurnResult(
            state=ConversationState(),
            blocks=(MessageBlock("notice", {
                "schema_version": 1,
                "code": "missing_information",
                "text": "Your budget conflicts: under £30,000 or at least £50,000?",
            }),),
        )

        observed = observe_turn(
            result,
            before_state=ConversationState(),
            before_dealer=DealerSnapshot((), ()),
            after_dealer=DealerSnapshot((), ()),
            commands=(),
            response_strategy=ResponseStrategy.MISSING_INFORMATION,
        )

        self.assertIn("clarify_price_direction", observed.answer.next_steps)
        self.assertNotIn("provide_customer_details", observed.answer.next_steps)

    def test_price_limit_conflict_wording_is_classified_semantically(self) -> None:
        result = TurnResult(
            state=ConversationState(),
            blocks=(MessageBlock("notice", {
                "schema_version": 1,
                "code": "missing_information",
                "text": (
                    "Your price limits conflict: should I search under Â£30,000 "
                    "or from Â£50,000 upwards?"
                ),
            }),),
        )

        observed = observe_turn(
            result,
            before_state=ConversationState(),
            before_dealer=DealerSnapshot((), ()),
            after_dealer=DealerSnapshot((), ()),
            commands=(),
            response_strategy=ResponseStrategy.MISSING_INFORMATION,
        )

        self.assertIn("clarify_price_direction", observed.answer.next_steps)
        self.assertNotIn("provide_customer_details", observed.answer.next_steps)

    def test_notice_and_choices_are_derived_from_blocks(self) -> None:
        result = TurnResult(
            state=ConversationState(),
            blocks=(
                MessageBlock("notice", {"schema_version": 1, "code": "slot_unavailable", "text": "That slot is no longer available."}),
                MessageBlock("slot_choices", {
                    "schema_version": 1,
                    "items": [{"id": "slot-new", "starts_at": "2026-08-14T12:00:00+00:00"}],
                    "actions": [{"action_id": "a-1", "action_type": "select_test_drive_slot", "entity_id": "slot-new"}],
                }),
            ),
        )

        observed = observe_turn(
            result,
            before_state=ConversationState(),
            before_dealer=DealerSnapshot((), ()),
            after_dealer=DealerSnapshot((), ()),
            commands=(),
            response_strategy=ResponseStrategy.SLOT_RESULTS,
        )

        self.assertEqual(observed.answer.block_types, ("notice", "slot_choices"))
        self.assertIn("slot_unavailable", observed.answer.facts)
        self.assertIn("select_new_slot", observed.answer.next_steps)
        self.assertIn("slot-new", observed.answer.entity_ids)

    def test_declared_vocabulary_covers_every_semantic_corpus_label(self) -> None:
        corpus = load_corpus("evals/corpus.json")
        answers = [turn.expectation.answer for scenario in corpus.scenarios for turn in scenario.turns]

        self.assertLessEqual({item for answer in answers for item in answer.required_block_types}, OBSERVABLE_BLOCK_TYPES)
        self.assertLessEqual({item for answer in answers for item in answer.required_facts}, OBSERVABLE_FACTS)
        self.assertLessEqual({item for answer in answers for item in answer.required_notice_keys}, OBSERVABLE_NOTICE_KEYS)
        self.assertLessEqual({item for answer in answers for item in answer.required_next_steps}, OBSERVABLE_NEXT_STEPS)

    def test_family_guidance_is_credited_only_when_present_in_output(self) -> None:
        result = TurnResult(
            state=ConversationState(),
            blocks=(MessageBlock("vehicle_cards", {"schema_version": 1, "vehicles": [], "actions": []}),),
        )

        observed = observe_turn(
            result,
            before_state=ConversationState(),
            before_dealer=DealerSnapshot((), ()),
            after_dealer=DealerSnapshot((), ()),
            commands=(),
            response_strategy=ResponseStrategy.SEARCH_RESULTS,
            current_input="Show me a family car",
        )

        self.assertNotIn("family_vehicle_considerations", observed.answer.facts)

    def test_family_guidance_accepts_natural_wording_and_stock_offer(self) -> None:
        result = TurnResult(
            state=ConversationState(),
            blocks=(MessageBlock("text", {
                "schema_version": 1,
                "text": (
                    "Check rear seats, ISOFIX points and boot space. "
                    "Would you like me to find family SUVs in our current stock?"
                ),
            }),),
        )

        observed = observe_turn(
            result,
            before_state=ConversationState(),
            before_dealer=DealerSnapshot((), ()),
            after_dealer=DealerSnapshot((), ()),
            commands=(),
            response_strategy=ResponseStrategy.GENERAL_GUIDANCE,
        )

        self.assertIn("family_vehicle_considerations", observed.answer.facts)
        self.assertIn("offer_stock_search", observed.answer.next_steps)

    def test_family_guidance_accepts_second_row_child_anchor_and_cargo_wording(self) -> None:
        result = TurnResult(
            state=ConversationState(),
            blocks=(MessageBlock("text", {
                "schema_version": 1,
                "text": (
                    "Compare comfortable second-row seating, enough cargo space, "
                    "and appropriate child-seat anchors."
                ),
            }),),
        )

        observed = observe_turn(
            result,
            before_state=ConversationState(),
            before_dealer=DealerSnapshot((), ()),
            after_dealer=DealerSnapshot((), ()),
            commands=(),
            response_strategy=ResponseStrategy.GENERAL_GUIDANCE,
            current_input="Is an SUV good for a family of five?",
        )

        self.assertIn("family_vehicle_considerations", observed.answer.facts)
        self.assertIn("compare_space", observed.answer.next_steps)

    def test_family_guidance_accepts_customer_space_needs_and_boot_practicality(self) -> None:
        result = TurnResult(
            state=ConversationState(),
            blocks=(MessageBlock("text", {
                "schema_version": 1,
                "text": (
                    "Prioritise the space you actually need, easy child-seat access "
                    "and boot practicality."
                ),
            }),),
        )

        observed = observe_turn(
            result,
            before_state=ConversationState(),
            before_dealer=DealerSnapshot((), ()),
            after_dealer=DealerSnapshot((), ()),
            commands=(),
            response_strategy=ResponseStrategy.GENERAL_GUIDANCE,
        )

        self.assertIn("family_vehicle_considerations", observed.answer.facts)
        self.assertIn("compare_space", observed.answer.next_steps)


if __name__ == "__main__":
    unittest.main()
