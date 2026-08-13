import unittest

from tests.conversations.support import corpus


class CustomerQuestionCorpusTests(unittest.TestCase):
    def test_all_sixty_scenarios_require_direct_observable_answers(self) -> None:
        scenarios = corpus().scenarios

        self.assertEqual(len(scenarios), 60)
        for scenario in scenarios:
            for turn in scenario.turns:
                self.assertTrue(turn.expectation.answer.direct, scenario.id)
                self.assertTrue(turn.expectation.answer.strategies, scenario.id)

    def test_question_set_contains_natural_language_stressors(self) -> None:
        tags = {tag for scenario in corpus().scenarios for tag in scenario.tags}

        self.assertTrue(
            {
                "straightforward",
                "multi-constraint",
                "colloquial",
                "typo",
                "ambiguity",
                "correction",
                "ordinal",
                "pronoun",
                "compound",
                "adversarial",
                "interruption",
                "double-confirmation",
                "refresh-restoration",
            }.issubset(tags)
        )

    def test_business_truth_edge_cases_have_forbidden_claims(self) -> None:
        by_id = {scenario.id: scenario for scenario in corpus().scenarios}

        self.assertIn(
            "numeric_price",
            by_id["vehicle-06"].turns[0].expectation.answer.forbidden_claims,
        )
        self.assertIn(
            "test_drive_available",
            by_id["sales-02"].turns[0].expectation.answer.forbidden_claims,
        )
        self.assertIn(
            "booking_details",
            by_id["workshop-09"].turns[0].expectation.answer.forbidden_claims,
        )


if __name__ == "__main__":
    unittest.main()
