import json
import unittest

import httpx

from webchat.harness.conversation import (
    ConversationRequest,
    ConversationToolCall,
    ConversationToolResult,
    ToolExchange,
)
from webchat.harness.conversation_tools import conversation_tool_catalogue
from webchat.providers.base import ConversationProviderError, ProviderErrorKind
from webchat.providers.openai import (
    OpenAIConversationProvider,
    OpenAIProviderConfig,
)


CONTEXT = '{"current_input":"Show me electric cars"}'


def request(*, exchange=None, force_text=False) -> ConversationRequest:
    return ConversationRequest(
        context=CONTEXT,
        tools=conversation_tool_catalogue(),
        exchange=exchange,
        force_text=force_text,
    )


def payload(output, *, model="gpt-test", usage=None):
    return {
        "id": "resp-do-not-persist",
        "object": "response",
        "status": "completed",
        "model": model,
        "output": output,
        "usage": usage
        or {"input_tokens": 80, "output_tokens": 20, "total_tokens": 100},
    }


def message(text):
    return {
        "id": "msg-1",
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def function_call(call_id="call-1", name="search_vehicles", arguments=None):
    return {
        "id": "fc-1",
        "type": "function_call",
        "status": "completed",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(arguments or {"fuel_type": "Electric"}),
    }


class OpenAIConversationProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_answer_uses_one_stateless_function_enabled_response(self) -> None:
        bodies = []

        async def handler(http_request):
            bodies.append(json.loads(http_request.content))
            return httpx.Response(200, json=payload([message("I can help with that.")]))

        times = iter((10.0, 10.125))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAIConversationProvider(
                client,
                OpenAIProviderConfig(api_key="secret", model="gpt-test"),
                monotonic=lambda: next(times),
            )
            result = await provider.converse(request())

        body = bodies[0]
        self.assertFalse(body["store"])
        self.assertEqual(body["model"], "gpt-test")
        self.assertEqual(body["tool_choice"], "auto")
        self.assertTrue(body["parallel_tool_calls"])
        self.assertEqual(body["input"], [{"role": "user", "content": CONTEXT}])
        self.assertGreater(len(body["tools"]), 10)
        for tool in body["tools"]:
            self.assertEqual(tool["type"], "function")
            self.assertIn("parameters", tool)
        self.assertEqual(result.text, "I can help with that.")
        self.assertEqual(result.latency_ms, 125)
        self.assertEqual(result.usage.total_tokens, 100)

    async def test_streaming_emits_real_text_deltas_and_returns_completed_response(self) -> None:
        completed = payload([message("I can help with that.")])
        events = (
            'event: response.output_text.delta\n'
            'data: {"type":"response.output_text.delta","delta":"I can "}\n\n'
            'event: response.output_text.delta\n'
            'data: {"type":"response.output_text.delta","delta":"help with that."}\n\n'
            'event: response.completed\n'
            f'data: {json.dumps({"type": "response.completed", "response": completed})}\n\n'
        )

        async def handler(_):
            return httpx.Response(
                200,
                text=events,
                headers={"content-type": "text/event-stream"},
            )

        times = iter((10.0, 10.025, 10.100))
        deltas = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await OpenAIConversationProvider(
                client,
                OpenAIProviderConfig(api_key="key", model="gpt-test"),
                monotonic=lambda: next(times),
            ).converse(request(), on_text_delta=lambda value: _append(deltas, value))

        self.assertEqual(deltas, ["I can ", "help with that."])
        self.assertEqual(result.text, "I can help with that.")
        self.assertAlmostEqual(result.time_to_first_token_ms, 25)
        self.assertAlmostEqual(result.latency_ms, 100)

    async def test_tool_calls_preserve_all_output_items_as_opaque_continuation(self) -> None:
        reasoning = {
            "id": "rs-1",
            "type": "reasoning",
            "summary": [],
            "encrypted_content": "opaque-reasoning",
        }
        output = [
            reasoning,
            function_call("call-1", "search_vehicles", {"make": "BMW"}),
            function_call("call-2", "get_dealership_hours", {"dealership_query": "Manchester"}),
        ]

        async def handler(_):
            return httpx.Response(200, json=payload(output))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await OpenAIConversationProvider(
                client,
                OpenAIProviderConfig(api_key="key", model="gpt-test"),
            ).converse(request())

        self.assertEqual(tuple(item.call_id for item in result.tool_calls), ("call-1", "call-2"))
        self.assertEqual(result.tool_calls[0].arguments_dict(), {"make": "BMW"})
        self.assertEqual(json.loads(result.continuation), output)

    async def test_continuation_replays_output_and_allows_further_tool_calls(self) -> None:
        call = ConversationToolCall("call-1", "search_vehicles", {"make": "BMW"})
        previous_output = [
            {"id": "rs-1", "type": "reasoning", "encrypted_content": "opaque", "summary": []},
            function_call("call-1", "search_vehicles", {"make": "BMW"}),
        ]
        exchange = ToolExchange(
            calls=(call,),
            results=(
                ConversationToolResult(
                    "call-1",
                    "search_vehicles",
                    {"status": "ok", "facts": [{"field": "count", "value": 2}]},
                ),
            ),
            continuation=json.dumps(previous_output, separators=(",", ":")),
        )
        bodies = []

        async def handler(http_request):
            bodies.append(json.loads(http_request.content))
            return httpx.Response(200, json=payload([message("I found two BMWs.")]))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await OpenAIConversationProvider(
                client,
                OpenAIProviderConfig(api_key="key", model="gpt-test"),
            ).converse(request(exchange=exchange))

        body = bodies[0]
        self.assertFalse(body["store"])
        self.assertNotIn("previous_response_id", body)
        self.assertEqual(body["tool_choice"], "auto")
        self.assertTrue(body["parallel_tool_calls"])
        self.assertEqual(body["input"][0], {"role": "user", "content": CONTEXT})
        self.assertEqual(body["input"][1:3], previous_output)
        tool_output = body["input"][3]
        self.assertEqual(tool_output["type"], "function_call_output")
        self.assertEqual(tool_output["call_id"], "call-1")
        self.assertEqual(json.loads(tool_output["output"])["status"], "ok")
        self.assertEqual(result.text, "I found two BMWs.")

    async def test_forced_text_continuation_disables_tool_calls(self) -> None:
        call = ConversationToolCall("call-1", "search_vehicles", {"make": "BMW"})
        previous_output = [function_call("call-1", "search_vehicles", {"make": "BMW"})]
        exchange = ToolExchange(
            calls=(call,),
            results=(
                ConversationToolResult("call-1", "search_vehicles", {"status": "ok"}),
            ),
            continuation=json.dumps(previous_output, separators=(",", ":")),
        )
        bodies = []

        async def handler(http_request):
            bodies.append(json.loads(http_request.content))
            return httpx.Response(200, json=payload([message("Final text.")]))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await OpenAIConversationProvider(
                client,
                OpenAIProviderConfig(api_key="key", model="gpt-test"),
            ).converse(request(exchange=exchange, force_text=True))

        self.assertEqual(bodies[0]["tool_choice"], "none")
        self.assertFalse(bodies[0]["parallel_tool_calls"])

    async def test_text_alongside_tool_calls_returns_both(self) -> None:
        output = [
            message("Let me look that up for you."),
            function_call("call-1", "search_vehicles", {"make": "BMW"}),
        ]

        async def handler(_):
            return httpx.Response(200, json=payload(output))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await OpenAIConversationProvider(
                client,
                OpenAIProviderConfig(api_key="key", model="gpt-test"),
            ).converse(request())

        self.assertEqual(result.text, "Let me look that up for you.")
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].name, "search_vehicles")
        self.assertIsNotNone(result.continuation)

    async def test_refusal_malformed_arguments_and_transport_fail_closed(self) -> None:
        cases = (
            (
                payload([{
                    "id": "msg-1",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "refusal", "refusal": "No"}],
                }]),
                ProviderErrorKind.REFUSAL,
            ),
            (
                payload([function_call(arguments={"make": "BMW"}) | {"arguments": "{"}]),
                ProviderErrorKind.INVALID_RESPONSE,
            ),
            (httpx.ReadTimeout("slow"), ProviderErrorKind.TIMEOUT),
        )
        for upstream, expected in cases:
            with self.subTest(expected=expected):
                async def handler(_, upstream=upstream):
                    if isinstance(upstream, Exception):
                        raise upstream
                    return httpx.Response(200, json=upstream)

                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    provider = OpenAIConversationProvider(
                        client,
                        OpenAIProviderConfig(api_key="key", model="gpt-test"),
                    )
                    with self.assertRaises(ConversationProviderError) as raised:
                        await provider.converse(request())
                self.assertIs(raised.exception.kind, expected)

    async def test_chained_exchange_replays_full_history(self) -> None:
        first_output = [function_call("call-1", "search_vehicles", {"make": "BMW"})]
        second_output = [function_call("call-2", "get_vehicle_details", {"vehicle_id": "veh-003"})]
        first_exchange = ToolExchange(
            calls=(ConversationToolCall("call-1", "search_vehicles", {"make": "BMW"}),),
            results=(ConversationToolResult("call-1", "search_vehicles", {"status": "ok"}),),
            continuation=json.dumps(first_output, separators=(",", ":")),
        )
        chained = ToolExchange(
            calls=(ConversationToolCall("call-2", "get_vehicle_details", {"vehicle_id": "veh-003"}),),
            results=(ConversationToolResult("call-2", "get_vehicle_details", {"status": "ok"}),),
            continuation=json.dumps(second_output, separators=(",", ":")),
            prior=first_exchange,
        )
        bodies = []

        async def handler(http_request):
            bodies.append(json.loads(http_request.content))
            return httpx.Response(200, json=payload([message("Details.")]))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await OpenAIConversationProvider(
                client,
                OpenAIProviderConfig(api_key="key", model="gpt-test"),
            ).converse(request(exchange=chained, force_text=True))

        body = bodies[0]
        # Input should contain: user, first_output, first_tool_result, second_output, second_tool_result
        self.assertEqual(body["input"][0], {"role": "user", "content": CONTEXT})
        self.assertEqual(body["input"][1], first_output[0])
        self.assertEqual(body["input"][2]["type"], "function_call_output")
        self.assertEqual(body["input"][2]["call_id"], "call-1")
        self.assertEqual(body["input"][3], second_output[0])
        self.assertEqual(body["input"][4]["type"], "function_call_output")
        self.assertEqual(body["input"][4]["call_id"], "call-2")

    async def test_malformed_continuation_is_a_stable_provider_failure(self) -> None:
        call = ConversationToolCall("call-1", "search_vehicles", {"make": "BMW"})
        exchange = ToolExchange(
            calls=(call,),
            results=(ConversationToolResult("call-1", "search_vehicles", {"status": "ok"}),),
            continuation="not-json",
        )

        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None)) as client:
            provider = OpenAIConversationProvider(
                client,
                OpenAIProviderConfig(api_key="key", model="gpt-test"),
            )
            with self.assertRaises(ConversationProviderError) as raised:
                await provider.converse(request(exchange=exchange))

        self.assertIs(raised.exception.kind, ProviderErrorKind.INVALID_RESPONSE)


async def _append(values, value):
    values.append(value)


if __name__ == "__main__":
    unittest.main()
