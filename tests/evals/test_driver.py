from datetime import UTC, datetime
import unittest

from evals.driver import ProviderConversationDriver, ScriptedConversationDriver
from evals.fixtures import FixtureRegistry
from evals.schema import Corpus, load_corpus
from webchat.providers.base import PlanningOutputError, PlanningOutputErrorKind


NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


class ScriptedConversationDriverTests(unittest.IsolatedAsyncioTestCase):
    async def test_planner_output_error_is_captured_separately(self) -> None:
        scenario = load_corpus("evals/corpus.json").scenarios[0]

        class Provider:
            async def plan(self, request):
                raise PlanningOutputError(PlanningOutputErrorKind.INVALID_PLAN)

        driver = ProviderConversationDriver(FixtureRegistry(now=NOW), Provider)

        observed = await driver.execute_turn(scenario, scenario.turns[0])

        self.assertEqual(observed.planner_failure, "invalid_plan")
        self.assertIsNone(observed.provider_failure)
    async def test_provider_lane_observes_the_providers_actual_plan(self) -> None:
        scenario = load_corpus("evals/corpus.json").scenarios[0]

        class Provider:
            async def plan(self, request):
                from webchat.providers.base import PlanningResult, ProviderUsage

                return PlanningResult(
                    scenario.turns[0].scripted_plan,
                    ProviderUsage(11, 7, 18),
                    2.5,
                    "test-provider",
                    "test-model",
                )

        driver = ProviderConversationDriver(FixtureRegistry(now=NOW), Provider)

        observed = await driver.execute_turn(scenario, scenario.turns[0])

        self.assertEqual([item.name.value for item in observed.commands], ["search_vehicles"])
        self.assertEqual(observed.input_tokens, 111)
        self.assertEqual(observed.output_tokens, 27)
        self.assertEqual(observed.latency_ms, 3.5)

    async def test_free_text_uses_scripted_plan_through_real_runtime(self) -> None:
        scenario = load_corpus("evals/corpus.json").scenarios[0]
        driver = ScriptedConversationDriver(FixtureRegistry(now=NOW))

        observed = await driver.execute_turn(scenario, scenario.turns[0])

        self.assertEqual(observed.model_calls, 2)
        self.assertEqual([item.name.value for item in observed.commands], ["search_vehicles"])
        self.assertIn("search_vehicles", observed.external_calls)

    async def test_new_scenario_gets_an_isolated_dealer_session(self) -> None:
        corpus = load_corpus("evals/corpus.json")
        first, second = corpus.scenarios[:2]
        driver = ScriptedConversationDriver(FixtureRegistry(now=NOW))

        one = await driver.execute_turn(first, first.turns[0])
        two = await driver.execute_turn(second, second.turns[0])

        self.assertEqual(one.external_calls, ("search_vehicles",))
        self.assertEqual(two.external_calls, ("search_vehicles",))

    async def test_action_turn_bypasses_provider(self) -> None:
        corpus = load_corpus("evals/corpus.json")
        scenario = next(item for item in corpus.scenarios if item.id == "state-06")
        driver = ScriptedConversationDriver(FixtureRegistry(now=NOW))

        observed = await driver.execute_turn(scenario, scenario.turns[0])

        self.assertEqual(observed.model_calls, 0)
        self.assertEqual(observed.commands, ())
        self.assertEqual(observed.state["pending_action"], None)

    async def test_local_validation_reports_rendered_outcome_not_planned_preparation(self) -> None:
        corpus = load_corpus("evals/corpus.json")
        scenario = next(item for item in corpus.scenarios if item.id == "sales-05")
        driver = ScriptedConversationDriver(FixtureRegistry(now=NOW))

        observed = await driver.execute_turn(scenario, scenario.turns[0])

        self.assertEqual(observed.answer.strategy, "missing_information")
        self.assertIsNone(observed.state["pending_action"])


if __name__ == "__main__":
    unittest.main()
