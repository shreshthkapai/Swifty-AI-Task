import unittest

from tests.conversations.support import corpus


class MultiTurnCorpusTests(unittest.TestCase):
    def test_vehicle_journey_uses_public_customer_turns(self) -> None:
        scenario = next(
            item for item in corpus().scenarios if item.id == "state-01"
        )

        self.assertEqual(
            [turn.input.value for turn in scenario.turns],
            [
                "Show me automatic BMWs under £40k",
                "the second one",
                "Actually cheaper",
                "Can I test drive it Saturday?",
                "select",
                "confirm",
            ],
        )
        self.assertEqual(scenario.turns[1].expectation.max_model_calls, 0)
        self.assertEqual(scenario.turns[-1].expectation.side_effects, ("test_drive_booking:1",))

    def test_workshop_recovery_journey_verifies_before_writes(self) -> None:
        scenario = next(
            item for item in corpus().scenarios if item.id == "workshop-16"
        )

        self.assertEqual(len(scenario.turns), 6)
        self.assertEqual(
            scenario.turns[0].expectation.answer.required_facts,
            ("booking_not_found",),
        )
        self.assertEqual(
            scenario.turns[1].expectation.expected_state["verification_grant"],
            "wsb-seeded-001",
        )
        self.assertEqual(
            scenario.turns[3].expectation.side_effects,
            ("workshop_amendment:1",),
        )
        self.assertEqual(
            scenario.turns[5].expectation.side_effects,
            ("workshop_cancellation:1",),
        )

    def test_every_expected_write_follows_an_explicit_action(self) -> None:
        for scenario in corpus().scenarios:
            for turn in scenario.turns:
                if turn.expectation.side_effects:
                    self.assertEqual(turn.input.kind, "action", scenario.id)
                    self.assertEqual(turn.input.value, "confirm", scenario.id)
                    self.assertEqual(turn.expectation.max_model_calls, 0, scenario.id)

    def test_preparation_cannot_write_and_valid_preparation_is_pending(self) -> None:
        mutation_by_command = {
            "prepare_test_drive_booking": "test_drive_booking",
            "prepare_sales_enquiry": "sales_enquiry",
            "prepare_vehicle_interest": "vehicle_interest",
            "prepare_callback": "callback",
            "prepare_part_exchange": "part_exchange_valuation",
            "prepare_workshop_booking": "workshop_booking",
            "prepare_workshop_amendment": "workshop_amendment",
            "prepare_workshop_cancellation": "workshop_cancellation",
            "prepare_dealership_message": "dealership_message",
        }
        for scenario in corpus().scenarios:
            for turn in scenario.turns:
                plan = turn.scripted_plan
                if plan is None or plan.response_strategy.value != "action_prepared":
                    continue
                preparation = next(
                    command
                    for command in plan.commands
                    if command.name.value.startswith("prepare_")
                )
                mutation = mutation_by_command[preparation.name.value]
                self.assertIn(
                    mutation,
                    turn.expectation.prohibited_mutations,
                    f"{scenario.id}/{turn.id}",
                )
                self.assertEqual(
                    turn.expectation.side_effects,
                    (),
                    f"{scenario.id}/{turn.id}",
                )
                rejected_locally = bool(
                    {
                        "invalid_phone",
                        "missing_customer_fields",
                        "status:cancelled",
                        "verification_required",
                    }
                    & set(turn.expectation.answer.required_facts)
                )
                self.assertEqual(
                    turn.expectation.expected_state.get("pending_action"),
                    None if rejected_locally else mutation,
                    f"{scenario.id}/{turn.id}",
                )


if __name__ == "__main__":
    unittest.main()
