import unittest

from webchat.harness.conversation import (
    ConversationRequest,
    ConversationResult,
    ConversationToolCall,
    ConversationToolResult,
    ConversationUsage,
    ToolExchange,
)
from webchat.harness.tool_gate import command_catalogue


USAGE = ConversationUsage(input_tokens=10, output_tokens=4, total_tokens=14)


class ConversationContractTests(unittest.TestCase):
    def test_initial_request_accepts_canonical_context_and_semantic_tools(self) -> None:
        request = ConversationRequest(
            context='{"current_input":"Show me electric cars"}',
            tools=command_catalogue(),
        )

        self.assertEqual(request.current_input, "Show me electric cars")
        self.assertGreater(len(request.tools), 10)
        self.assertIsNone(request.exchange)

    def test_request_rejects_noncanonical_or_provider_owned_context(self) -> None:
        for context in (
            '{"current_input": "hello"}',
            '{"provider_response_id":"resp-secret","current_input":"hello"}',
        ):
            with self.subTest(context=context):
                with self.assertRaises(ValueError):
                    ConversationRequest(context=context, tools=command_catalogue())

    def test_result_is_either_natural_text_or_tool_calls(self) -> None:
        direct = ConversationResult(
            text="The selected car has covered 22,250 miles.",
            usage=USAGE,
            latency_ms=125,
            time_to_first_token_ms=80,
            provider="test",
            model="test-model",
        )
        tool_call = ConversationToolCall(
            call_id="call-1",
            name="search_vehicles",
            arguments={"fuel_type": "Electric"},
        )
        tools = ConversationResult(
            tool_calls=(tool_call,),
            usage=USAGE,
            latency_ms=90,
            provider="test",
            model="test-model",
        )

        self.assertIn("22,250", direct.text)
        self.assertEqual(tools.tool_calls, (tool_call,))
        for kwargs in (
            {"text": "answer", "tool_calls": (tool_call,)},
            {"text": None, "tool_calls": ()},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    ConversationResult(
                        **kwargs,
                        usage=USAGE,
                        latency_ms=1,
                        provider="test",
                        model="test-model",
                    )

    def test_tool_arguments_are_canonical_immutable_json(self) -> None:
        source = {"filters": ["Electric", "SUV"], "max_price_minor": 5_000_000}
        call = ConversationToolCall("call-1", "search_vehicles", source)
        source["max_price_minor"] = 1
        source["filters"].append("changed")

        self.assertEqual(
            call.arguments_dict(),
            {"filters": ["Electric", "SUV"], "max_price_minor": 5_000_000},
        )
        with self.assertRaises(ValueError):
            ConversationToolCall("call-2", "search_vehicles", {"bad": object()})

    def test_exchange_requires_one_result_for_every_unique_call(self) -> None:
        first = ConversationToolCall("call-1", "search_vehicles", {"make": "BMW"})
        second = ConversationToolCall("call-2", "get_dealership_hours", {})
        exchange = ToolExchange(
            calls=(first, second),
            results=(
                ConversationToolResult("call-1", "search_vehicles", {"count": 2}),
                ConversationToolResult("call-2", "get_dealership_hours", {"open": True}),
            ),
            continuation='[{"type":"reasoning"}]',
        )

        self.assertEqual(tuple(item.call_id for item in exchange.results), ("call-1", "call-2"))
        self.assertEqual(exchange.continuation, '[{"type":"reasoning"}]')
        with self.assertRaises(ValueError):
            ToolExchange(
                calls=(first, first),
                results=(ConversationToolResult("call-1", "search_vehicles", {}),),
            )
        with self.assertRaises(ValueError):
            ToolExchange(
                calls=(
                    first,
                    ConversationToolCall("call-1", "get_vehicle_details", {}),
                ),
                results=(
                    ConversationToolResult("call-1", "search_vehicles", {}),
                    ConversationToolResult("call-1", "get_vehicle_details", {}),
                ),
            )
        with self.assertRaises(ValueError):
            ToolExchange(
                calls=(first,),
                results=(ConversationToolResult("different", "search_vehicles", {}),),
            )
        with self.assertRaises(ValueError):
            ToolExchange(
                calls=(first,),
                results=(ConversationToolResult("call-1", "get_vehicle_details", {}),),
            )

    def test_continuation_request_exposes_same_customer_input(self) -> None:
        call = ConversationToolCall("call-1", "get_vehicle_details", {"vehicle_id": "veh-1"})
        request = ConversationRequest(
            context='{"current_input":"What is its mileage?"}',
            tools=command_catalogue(),
            exchange=ToolExchange(
                calls=(call,),
                results=(
                    ConversationToolResult(
                        "call-1",
                        "get_vehicle_details",
                        {"vehicle": {"id": "veh-1", "mileage": 22250}},
                    ),
                ),
            ),
        )

        self.assertEqual(request.current_input, "What is its mileage?")

    def test_boolean_schema_and_latency_values_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ConversationRequest(
                context='{"current_input":"hello"}',
                tools=command_catalogue(),
                schema_version=True,
            )
        with self.assertRaises(ValueError):
            ConversationResult(
                text="hello",
                usage=USAGE,
                latency_ms=True,
                time_to_first_token_ms=0,
                provider="test",
                model="test-model",
            )


if __name__ == "__main__":
    unittest.main()
