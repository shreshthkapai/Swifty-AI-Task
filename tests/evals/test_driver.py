from datetime import UTC, datetime
import unittest

from evals.driver import ProviderConversationDriver, ScriptedConversationDriver
from evals.fixtures import FixtureRegistry
from evals.schema import load_corpus
from webchat.harness.conversation import (
    ConversationResult,
    ConversationToolCall,
    ConversationUsage,
)
from webchat.providers.base import (
    ConversationProviderError,
    ProviderErrorKind,
)


NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


class ScriptedConversationDriverTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_error_is_captured_separately(self) -> None:
        scenario = load_corpus("evals/corpus.json").scenarios[0]

        class Provider:
            async def converse(self, request, *, on_text_delta=None):
                raise ConversationProviderError(
                    ProviderErrorKind.INVALID_RESPONSE,
                    retryable=False,
                )

        driver = ProviderConversationDriver(FixtureRegistry(now=NOW), Provider)

        observed = await driver.execute_turn(scenario, scenario.turns[0])

        self.assertEqual(observed.provider_failure, "invalid_response")

    async def test_provider_lane_observes_actual_semantic_tool_calls(self) -> None:
        scenario = load_corpus("evals/corpus.json").scenarios[0]

        class Provider:
            async def converse(self, request, *, on_text_delta=None):
                if request.exchange is not None:
                    return ConversationResult(
                        text="I found matching vehicles.",
                        usage=ConversationUsage(100, 20, 120),
                        latency_ms=2,
                        time_to_first_token_ms=1,
                        provider="test-provider",
                        model="test-model",
                    )
                return ConversationResult(
                    tool_calls=(ConversationToolCall(
                        "call-1",
                        "search_vehicles",
                        {"make": "BMW", "transmission": "Automatic", "max_price_minor": 4_000_000},
                    ),),
                    continuation="test-continuation",
                    usage=ConversationUsage(11, 7, 18),
                    latency_ms=1.5,
                    provider="test-provider",
                    model="test-model",
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
