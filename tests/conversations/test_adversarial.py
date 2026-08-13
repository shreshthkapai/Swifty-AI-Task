import unittest

from tests.conversations.support import corpus


class AdversarialCorpusTests(unittest.TestCase):
    def test_obvious_trivia_is_zero_call_but_mixed_request_is_retained(self) -> None:
        by_id = {scenario.id: scenario for scenario in corpus().scenarios}

        for scenario_id in ("scope-01", "scope-02"):
            turn = by_id[scenario_id].turns[0]
            self.assertEqual(turn.expectation.max_model_calls, 0)
            self.assertEqual(turn.expectation.prohibited_calls, ("*",))
            self.assertIsNone(turn.scripted_plan)

        mixed = by_id["scope-04"].turns[0]
        self.assertEqual(mixed.expectation.required_commands, ("get_vehicle_details",))
        self.assertIn("moon_size", mixed.expectation.answer.forbidden_claims)

    def test_confirmation_and_verification_bypass_attempts_cannot_write(self) -> None:
        by_id = {scenario.id: scenario for scenario in corpus().scenarios}

        confirmation = by_id["scope-05"].turns[0].expectation
        self.assertIn("test_drive_booking", confirmation.prohibited_mutations)
        self.assertIn("confirmation_bypassed", confirmation.answer.forbidden_claims)

        verification = by_id["workshop-11"].turns[0].expectation
        self.assertIn("workshop_amendment", verification.prohibited_mutations)
        self.assertIn("verification_required", verification.answer.required_facts)

    def test_reserved_sold_and_stale_entities_have_distinct_recovery(self) -> None:
        by_id = {scenario.id: scenario for scenario in corpus().scenarios}

        reserved = by_id["sales-02"].turns[0].expectation.answer
        self.assertEqual(
            reserved.required_next_steps,
            ("register_interest", "sales_enquiry"),
        )
        sold = by_id["sales-03"].turns[0].expectation.answer
        self.assertEqual(sold.required_next_steps, ("sales_enquiry",))
        stale = by_id["sales-07"].turns[0].expectation.answer
        self.assertIn("refreshed_slots", stale.required_facts)


if __name__ == "__main__":
    unittest.main()
