from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import unittest

from evals.run import (
    DetailedEvaluationReport,
    EvaluatedTurn,
    EvaluationLane,
    EvaluationReport,
    FailureCategory,
    LaneKind,
    ObservedAnswer,
    ObservedTurn,
    assert_lane_parity,
    _arguments_satisfy,
    run_evaluation,
    run_detailed_evaluation,
    score_turn,
)
from evals.schema import load_corpus
from tests.webchat.fakes.conversation import NoOpConversationDriver
from webchat.harness.contracts import (
    PreparationCommand,
    PreparationCommandName,
    ReadCommand,
    ReadCommandName,
)


CORPUS_PATH = Path(__file__).parents[2] / "evals" / "corpus.json"


class RunnerContractTests(unittest.IsolatedAsyncioTestCase):
    def test_free_text_wording_is_not_inferred_as_exact_from_scripted_plan(self) -> None:
        self.assertTrue(
            _arguments_satisfy(
                "prepare_dealership_message",
                {
                    "dealership_query": "Stockport",
                    "department": "service",
                    "message": "I will be ten minutes late.",
                },
                {
                    "dealership_query": "Stockport",
                    "department": "service",
                    "message": "I'll be ten minutes late.",
                    "subject": "Running late",
                },
            )
        )
        self.assertFalse(
            _arguments_satisfy(
                "prepare_dealership_message",
                {"dealership_query": "Stockport", "department": "service"},
                {"dealership_query": "Bolton", "department": "service"},
            )
        )
    async def test_detailed_run_retains_each_input_observation_and_score(self) -> None:
        corpus = load_corpus(CORPUS_PATH)
        one = type(corpus)(
            corpus_version=corpus.corpus_version,
            fixture_version=corpus.fixture_version,
            scoring_version=corpus.scoring_version,
            scenarios=(corpus.scenarios[0],),
        )

        report = await run_detailed_evaluation(
            one,
            NoOpConversationDriver(),
            lane=LaneKind.SCRIPTED,
        )

        self.assertIsInstance(report, DetailedEvaluationReport)
        self.assertEqual(len(report.records), len(one.scenarios[0].turns))
        record = report.records[0]
        self.assertIsInstance(record, EvaluatedTurn)
        self.assertEqual(record.scenario_id, one.scenarios[0].id)
        self.assertEqual(record.category, one.scenarios[0].category)
        self.assertEqual(record.input, one.scenarios[0].turns[0].input)
        self.assertIsNone(record.observed.answer)
        self.assertFalse(record.score.passed)
        self.assertEqual(record.score.divergence.path, "answer.missing")
        self.assertEqual(report.failed_turns, len(report.records))
        self.assertEqual(report.category_counts(), {one.scenarios[0].category: {"passed": 0, "failed": len(report.records)}})

    async def test_no_op_chatbot_fails_every_corpus_turn(self) -> None:
        corpus = load_corpus(CORPUS_PATH)

        report = await run_evaluation(corpus, NoOpConversationDriver())

        expected_turns = sum(len(scenario.turns) for scenario in corpus.scenarios)
        self.assertEqual(report.total_turns, expected_turns)
        self.assertEqual(report.passed_turns, 0)
        self.assertEqual(report.failed_turns, expected_turns)
        self.assertEqual(
            report.results[0].divergence.category,
            FailureCategory.ANSWER_QUALITY,
        )
        self.assertEqual(report.results[0].divergence.path, "answer.missing")

    def test_valid_observation_passes_semantic_answer_assertions(self) -> None:
        corpus = load_corpus(CORPUS_PATH)
        scenario = next(item for item in corpus.scenarios if item.id == "scope-01")
        turn = scenario.turns[0]
        observation = ObservedTurn(
            answer=ObservedAnswer(
                strategy="domain_redirect",
                text=(
                    "I can help with vehicles, test drives, sales, servicing, and "
                    "Northstar dealership information."
                ),
                direct=True,
            )
        )

        result = score_turn(scenario.id, turn, observation)

        self.assertTrue(result.passed)
        self.assertIsNone(result.divergence)

    def test_prohibited_mutation_is_classified_at_policy_layer(self) -> None:
        corpus = load_corpus(CORPUS_PATH)
        scenario = next(item for item in corpus.scenarios if item.id == "scope-05")
        turn = scenario.turns[0]
        observation = ObservedTurn(
            commands=(
                PreparationCommand(
                    PreparationCommandName.PREPARE_TEST_DRIVE_BOOKING
                ),
            ),
            mutations=("test_drive_booking",),
            model_calls=1,
            answer=ObservedAnswer(
                strategy="action_prepared",
                block_types=("confirmation",),
                next_steps=("confirm_or_cancel",),
                direct=True,
            ),
        )

        result = score_turn(scenario.id, turn, observation)

        self.assertFalse(result.passed)
        self.assertEqual(result.divergence.category, FailureCategory.POLICY_MISSING)
        self.assertEqual(result.divergence.path, "mutations.prohibited")

    def test_provider_transport_failure_is_not_model_reasoning(self) -> None:
        corpus = load_corpus(CORPUS_PATH)
        turn = corpus.scenarios[0].turns[0]

        result = score_turn(
            corpus.scenarios[0].id,
            turn,
            ObservedTurn(provider_failure="timeout"),
        )

        self.assertEqual(result.divergence.category, FailureCategory.PROVIDER_FAILURE)
        self.assertEqual(result.divergence.path, "provider")

    def test_planner_output_failure_is_not_provider_transport(self) -> None:
        corpus = load_corpus(CORPUS_PATH)
        turn = corpus.scenarios[0].turns[0]

        result = score_turn(
            corpus.scenarios[0].id,
            turn,
            ObservedTurn(planner_failure="invalid_plan"),
        )

        self.assertEqual(result.divergence.category, FailureCategory.PLANNER_FAILURE)
        self.assertEqual(result.divergence.path, "planner")

    def test_missing_required_adapter_call_is_scored_separately_from_command(self) -> None:
        corpus = load_corpus(CORPUS_PATH)
        scenario = next(item for item in corpus.scenarios if item.id == "vehicle-01")
        turn = replace(
            scenario.turns[0],
            expectation=replace(
                scenario.turns[0].expectation,
                required_calls=("search_vehicles",),
            ),
        )
        observation = ObservedTurn(
            commands=(
                ReadCommand.from_mapping(
                    ReadCommandName.SEARCH_VEHICLES,
                    {"body_style": "SUV"},
                ),
            ),
            model_calls=1,
            answer=ObservedAnswer(
                strategy="search_results",
                block_types=("vehicle_cards",),
                facts=("result_count",),
                next_steps=("refine_or_select",),
                direct=True,
            ),
        )

        result = score_turn(scenario.id, turn, observation)

        self.assertFalse(result.passed)
        self.assertEqual(result.divergence.category, FailureCategory.ADAPTER_ERROR)
        self.assertEqual(result.divergence.path, "calls.required")

    def test_first_divergence_is_structured_and_stable(self) -> None:
        corpus = load_corpus(CORPUS_PATH)
        scenario = next(item for item in corpus.scenarios if item.id == "vehicle-01")
        turn = scenario.turns[0]
        observation = ObservedTurn(
            commands=(ReadCommand(ReadCommandName.GET_DEALERSHIP_HOURS),),
            model_calls=1,
            answer=ObservedAnswer(
                strategy="search_results",
                block_types=("vehicle_cards",),
                facts=("result_count",),
                next_steps=("refine_or_select",),
                direct=True,
            ),
        )

        result = score_turn(scenario.id, turn, observation)

        self.assertEqual(result.divergence.category, FailureCategory.MODEL_REASONING)
        self.assertEqual(result.divergence.path, "commands.required")
        self.assertEqual(result.divergence.expected, ["search_vehicles"])
        self.assertEqual(result.divergence.actual, ["get_dealership_hours"])

    def test_wrong_command_arguments_fail_with_first_divergence_evidence(self) -> None:
        corpus = load_corpus(CORPUS_PATH)
        scenario = next(item for item in corpus.scenarios if item.id == "vehicle-02")
        turn = scenario.turns[0]
        observation = ObservedTurn(
            commands=(
                ReadCommand.from_mapping(
                    ReadCommandName.SEARCH_VEHICLES,
                    {
                        "make": "BMW",
                        "transmission": "Automatic",
                        "max_price_minor": 3_500_000,
                    },
                ),
            ),
            state={
                "preferences": {
                    "make": "BMW",
                    "transmission": "Automatic",
                    "max_price_minor": 3_000_000,
                }
            },
            model_calls=1,
            answer=ObservedAnswer(
                strategy="search_results",
                block_types=("vehicle_cards",),
                facts=("constraints_applied",),
                next_steps=("select_vehicle",),
                direct=True,
            ),
        )

        result = score_turn(scenario.id, turn, observation)

        self.assertFalse(result.passed)
        self.assertEqual(result.divergence.category, FailureCategory.MODEL_REASONING)
        self.assertEqual(result.divergence.path, "commands[0].arguments")
        self.assertEqual(
            result.divergence.expected,
            {
                "make": "BMW",
                "max_price_minor": 3_000_000,
                "transmission": "Automatic",
            },
        )
        self.assertEqual(result.divergence.actual["max_price_minor"], 3_500_000)

    def test_semantic_command_arguments_ignore_null_noise_and_filter_case(self) -> None:
        corpus = load_corpus(CORPUS_PATH)
        scenario = next(item for item in corpus.scenarios if item.id == "vehicle-02")
        turn = scenario.turns[0]
        observation = ObservedTurn(
            commands=(
                ReadCommand.from_mapping(
                    ReadCommandName.SEARCH_VEHICLES,
                    {
                        "availability": None,
                        "body_style": None,
                        "fuel_type": None,
                        "make": "bmw",
                        "max_price_minor": 3_000_000,
                        "model": None,
                        "sort": None,
                        "transmission": "automatic",
                    },
                ),
            ),
            state={
                "preferences": {
                    "make": "BMW",
                    "transmission": "Automatic",
                    "max_price_minor": 3_000_000,
                }
            },
            model_calls=1,
            answer=ObservedAnswer(
                strategy="search_results",
                block_types=("vehicle_cards",),
                facts=("constraints_applied",),
                next_steps=("select_vehicle",),
                direct=True,
            ),
        )

        result = score_turn(scenario.id, turn, observation)

        self.assertTrue(result.passed)
        self.assertIsNone(result.divergence)

    def test_lane_parity_allows_only_planning_source_to_differ(self) -> None:
        common = {
            "corpus_version": "2026-08-13.1",
            "fixture_version": "northstar-seed-v1",
            "scoring_version": "1",
            "clock": datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
            "runtime_policy_version": "runtime-v1",
        }
        scripted = EvaluationLane(kind=LaneKind.SCRIPTED, **common)
        live = EvaluationLane(kind=LaneKind.LIVE_PROVIDER, **common)

        assert_lane_parity(scripted, live)

        changed = EvaluationLane(
            kind=LaneKind.LIVE_PROVIDER,
            **{**common, "fixture_version": "different"},
        )
        with self.assertRaisesRegex(ValueError, "parity"):
            assert_lane_parity(scripted, changed)

    def test_evaluation_lane_requires_a_closed_lane_kind(self) -> None:
        with self.assertRaisesRegex(ValueError, "LaneKind"):
            EvaluationLane(
                kind="scripted",
                corpus_version="2026-08-13.1",
                fixture_version="northstar-seed-v1",
                scoring_version="1",
                clock=datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
                runtime_policy_version="runtime-v1",
            )

    def test_turn_scores_and_reports_retain_usage_and_latency_metrics(self) -> None:
        corpus = load_corpus(CORPUS_PATH)
        scenario = next(item for item in corpus.scenarios if item.id == "scope-01")
        turn = scenario.turns[0]
        observation = ObservedTurn(
            input_tokens=120,
            output_tokens=30,
            latency_ms=18.5,
            answer=ObservedAnswer(
                strategy="domain_redirect",
                text=(
                    "I can help with vehicles, test drives, sales, servicing, and "
                    "Northstar dealership information."
                ),
                direct=True,
            ),
        )

        score = score_turn(scenario.id, turn, observation)

        self.assertEqual(score.input_tokens, 120)
        self.assertEqual(score.output_tokens, 30)
        self.assertEqual(score.latency_ms, 18.5)
        report = EvaluationReport(
            corpus_version=corpus.corpus_version,
            scoring_version=corpus.scoring_version,
            results=(score,),
        )
        self.assertEqual(report.total_model_calls, 0)
        self.assertEqual(report.total_input_tokens, 120)
        self.assertEqual(report.total_output_tokens, 30)
        self.assertEqual(report.total_latency_ms, 18.5)

    def test_observed_metrics_cannot_be_negative(self) -> None:
        for field, value in (
            ("model_calls", -1),
            ("input_tokens", -1),
            ("output_tokens", -1),
            ("latency_ms", -0.1),
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, field):
                    ObservedTurn(**{field: value})


if __name__ == "__main__":
    unittest.main()
