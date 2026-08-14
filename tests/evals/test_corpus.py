import json
from pathlib import Path
import tempfile
import unittest

from evals import schema


CORPUS_PATH = Path(__file__).parents[2] / "evals" / "corpus.json"


class CorpusContractTests(unittest.TestCase):
    def test_corpus_contains_exact_domain_distribution(self) -> None:
        corpus = schema.load_corpus(CORPUS_PATH)

        self.assertEqual(len(corpus.scenarios), 60)
        self.assertEqual(
            corpus.category_counts(),
            {
                "vehicle_discovery": 12,
                "sales_test_drive": 12,
                "workshop": 16,
                "dealership_contact": 8,
                "state_reference": 6,
                "scope_resilience": 6,
            },
        )

    def test_every_turn_has_customer_input_and_observable_answer_contract(self) -> None:
        corpus = schema.load_corpus(CORPUS_PATH)

        for scenario in corpus.scenarios:
            self.assertTrue(scenario.turns, scenario.id)
            for turn in scenario.turns:
                self.assertIn(turn.input.kind, {"message", "action"}, scenario.id)
                self.assertTrue(turn.input.value.strip(), scenario.id)
                self.assertGreaterEqual(turn.expectation.max_model_calls, 0, scenario.id)
                answer = turn.expectation.answer
                self.assertTrue(answer.strategies, scenario.id)
                self.assertTrue(
                    answer.required_block_types
                    or answer.required_facts
                    or answer.required_next_steps
                    or answer.exact_text,
                    scenario.id,
                )

    def test_all_scenarios_declare_fixtures_and_safety_expectations(self) -> None:
        corpus = schema.load_corpus(CORPUS_PATH)

        for scenario in corpus.scenarios:
            self.assertTrue(scenario.initial_state_fixture, scenario.id)
            self.assertTrue(scenario.dealer_fixture, scenario.id)
            for turn in scenario.turns:
                expectation = turn.expectation
                self.assertIsInstance(expectation.required_commands, tuple, scenario.id)
                self.assertIsInstance(expectation.allowed_commands, tuple, scenario.id)
                self.assertIsInstance(expectation.prohibited_calls, tuple, scenario.id)
                self.assertIsInstance(expectation.prohibited_mutations, tuple, scenario.id)
                self.assertIsInstance(expectation.expected_state, dict, scenario.id)
                self.assertIsInstance(expectation.side_effects, tuple, scenario.id)

    def test_seeded_edge_cases_are_explicitly_covered(self) -> None:
        corpus = schema.load_corpus(CORPUS_PATH)
        tags = {tag for scenario in corpus.scenarios for tag in scenario.tags}

        self.assertTrue(
            {
                "veh-007-reserved",
                "veh-013-sold",
                "veh-019-price-on-request",
                "stale-test-drive-slot",
                "stale-workshop-slot",
                "bolton-no-availability",
                "bank-holiday-hours",
                "bad-workshop-identity",
                "cancelled-workshop-booking",
                "idempotent-replay",
                "idempotency-conflict",
                "temporary-api-failure",
            }.issubset(tags)
        )

    def test_required_scope_questions_have_exact_routes(self) -> None:
        corpus = schema.load_corpus(CORPUS_PATH)
        turns = {
            turn.input.value: turn
            for scenario in corpus.scenarios
            for turn in scenario.turns
            if turn.input.kind == "message"
        }

        moon = turns["What size is the moon?"]
        self.assertEqual(moon.expectation.max_model_calls, 0)
        self.assertEqual(moon.expectation.prohibited_calls, ("*",))
        self.assertEqual(
            moon.expectation.answer.strategies, ("domain_redirect",)
        )
        world_cup = turns["Who won the World Cup?"]
        self.assertEqual(world_cup.expectation.max_model_calls, 0)
        self.assertEqual(world_cup.expectation.prohibited_calls, ("*",))
        family = turns["Is an SUV good for a family of five?"]
        self.assertEqual(
            family.expectation.answer.strategies, ("general_guidance",)
        )
        mixed = turns["How big is the moon, and will my telescope fit in this X3?"]
        self.assertIn("moon_size", mixed.expectation.answer.forbidden_claims)
        self.assertIn("get_vehicle_details", mixed.expectation.required_commands)

    def test_ui_actions_and_obvious_redirects_have_no_scripted_plan(self) -> None:
        corpus = schema.load_corpus(CORPUS_PATH)

        for scenario in corpus.scenarios:
            for turn in scenario.turns:
                if turn.input.kind == "action" or turn.expectation.max_model_calls == 0:
                    self.assertIsNone(turn.scripted_plan, f"{scenario.id}/{turn.id}")

    def test_planned_turns_contain_valid_mutation_free_plans(self) -> None:
        corpus = schema.load_corpus(CORPUS_PATH)

        for scenario in corpus.scenarios:
            for turn in scenario.turns:
                if turn.expectation.max_model_calls > 0:
                    self.assertIsNotNone(turn.scripted_plan, f"{scenario.id}/{turn.id}")
                    self.assertLessEqual(len(turn.scripted_plan.commands), 2)

    def test_loading_same_file_is_canonical_and_deterministic(self) -> None:
        first = schema.load_corpus(CORPUS_PATH)
        second = schema.load_corpus(CORPUS_PATH)

        self.assertEqual(first, second)
        self.assertEqual(first.canonical_json(), second.canonical_json())

    def test_malformed_expectation_fails_loudly(self) -> None:
        malformed = {
            "schema_version": 1,
            "corpus_version": "test",
            "fixture_version": "test",
            "scoring_version": "1",
            "scenarios": [
                {
                    "id": "broken",
                    "category": "vehicle_discovery",
                    "title": "Missing answer assertions",
                    "tags": [],
                    "initial": {"state_fixture": "empty", "page": {}},
                    "dealer_fixture": "seeded",
                    "turns": [
                        {
                            "id": "t1",
                            "input": {"kind": "message", "value": "Find a car"},
                            "scripted_plan": None,
                            "expect": {
                                "required_commands": [],
                                "allowed_commands": [],
                                "prohibited_calls": [],
                                "prohibited_mutations": [],
                                "expected_state": {},
                                "side_effects": [],
                                "max_model_calls": 1
                            }
                        }
                    ]
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(malformed), encoding="utf-8")

            with self.assertRaisesRegex(schema.CorpusValidationError, "answer"):
                schema.load_corpus(path)

    def test_scripted_plan_must_match_required_commands_and_answer_strategy(self) -> None:
        corpus_data = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
        scenario = corpus_data["scenarios"][0]
        scenario["turns"][0]["scripted_plan"]["commands"] = []
        scenario["turns"][0]["scripted_plan"]["response_strategy"] = "recovery"
        corpus_data["scenarios"] = [scenario]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mismatch.json"
            path.write_text(json.dumps(corpus_data), encoding="utf-8")

            with self.assertRaisesRegex(
                schema.CorpusValidationError,
                "scripted_plan",
            ):
                schema.load_corpus(path)

    def test_empty_expectation_collections_have_explicit_schema_defaults(self) -> None:
        minimal = {
            "schema_version": 1,
            "corpus_version": "test",
            "fixture_version": "test",
            "scoring_version": "1",
            "scenarios": [
                {
                    "id": "minimal",
                    "category": "scope_resilience",
                    "title": "Minimal deterministic response",
                    "tags": [],
                    "initial": {"state_fixture": "empty", "page": {}},
                    "dealer_fixture": "seeded",
                    "turns": [
                        {
                            "id": "t1",
                            "input": {"kind": "message", "value": "Trivia"},
                            "scripted_plan": None,
                            "expect": {
                                "max_model_calls": 0,
                                "answer": {
                                    "strategies": ["domain_redirect"],
                                    "exact_text": "Dealership redirect",
                                    "direct": True
                                }
                            }
                        }
                    ]
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "minimal.json"
            path.write_text(json.dumps(minimal), encoding="utf-8")

            corpus = schema.load_corpus(path)

        expectation = corpus.scenarios[0].turns[0].expectation
        self.assertEqual(expectation.required_commands, ())
        self.assertEqual(expectation.required_calls, ())
        self.assertEqual(expectation.prohibited_mutations, ())
        self.assertEqual(expectation.answer.required_facts, ())

    def test_non_finite_fixture_values_are_rejected(self) -> None:
        corpus_data = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
        corpus_data["scenarios"] = [corpus_data["scenarios"][0]]
        corpus_data["scenarios"][0]["initial"]["page"]["distance"] = float("nan")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "non-finite.json"
            path.write_text(json.dumps(corpus_data), encoding="utf-8")

            with self.assertRaisesRegex(
                schema.CorpusValidationError,
                "JSON values",
            ):
                schema.load_corpus(path)

    def test_boolean_corpus_schema_version_is_rejected(self) -> None:
        corpus_data = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
        corpus_data["schema_version"] = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "boolean-version.json"
            path.write_text(json.dumps(corpus_data), encoding="utf-8")

            with self.assertRaisesRegex(
                schema.CorpusValidationError,
                "schema version",
            ):
                schema.load_corpus(path)

    def test_dealership_hours_plans_identify_the_dealership(self) -> None:
        corpus = schema.load_corpus(CORPUS_PATH)

        for scenario in corpus.scenarios:
            for turn in scenario.turns:
                if turn.scripted_plan is None:
                    continue
                for command in turn.scripted_plan.commands:
                    if command.name.value == "get_dealership_hours":
                        self.assertTrue(
                            {"dealership_id", "dealership_query"}
                            & command.to_dict()["arguments"].keys(),
                            f"{scenario.id}/{turn.id}",
                        )

    def test_part_exchange_plans_identify_the_receiving_dealership(self) -> None:
        corpus = schema.load_corpus(CORPUS_PATH)

        for scenario in corpus.scenarios:
            for turn in scenario.turns:
                if turn.scripted_plan is None:
                    continue
                for command in turn.scripted_plan.commands:
                    if command.name.value == "prepare_part_exchange":
                        self.assertTrue(
                            {"dealership_id", "dealership_query"}
                            & command.to_dict()["arguments"].keys(),
                            f"{scenario.id}/{turn.id}",
                        )


if __name__ == "__main__":
    unittest.main()
