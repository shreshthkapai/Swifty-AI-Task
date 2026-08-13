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
from webchat.harness.contracts import ResponseStrategy
from webchat.harness.runtime import TurnResult
from webchat.harness.state import ConversationState, MessageBlock


class ObservationTests(unittest.TestCase):
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
            planning_strategy=ResponseStrategy.SLOT_RESULTS,
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
            planning_strategy=ResponseStrategy.SEARCH_RESULTS,
            current_input="Show me a family car",
        )

        self.assertNotIn("family_vehicle_considerations", observed.answer.facts)


if __name__ == "__main__":
    unittest.main()
