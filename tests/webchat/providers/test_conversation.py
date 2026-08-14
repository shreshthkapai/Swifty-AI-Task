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


def request(*, exchange=None) -> ConversationRequest:
    return ConversationRequest(
        context=CONTEXT,
        tools=conversation_tool_catalogue(),
        exchange=exchange,
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
        deltas = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAIConversationProvider(
                client,
                OpenAIProviderConfig(api_key="secret", model="gpt-test"),
                monotonic=lambda: next(times),
            )
            result = await provider.converse(
                request(),
                on_text_delta=lambda value: _append(deltas, value),
            )

        body = bodies[0]
        self.assertFalse(body["store"])
        self.assertEqual(body["model"], "gpt-test")
        self.assertEqual(body["tool_choice"], "auto")
        self.assertTrue(body["parallel_tool_calls"])
        self.assertEqual(body["input"], [{"role": "user", "content": CONTEXT}])
        self.assertGreater(len(body["tools"]), 10)
        for tool in body["tools"]:
            self.assertEqual(tool["type"], "function")
            self.assertTrue(tool["strict"])
            self.assertFalse(tool["parameters"]["additionalProperties"])
            self.assertEqual(
                set(tool["parameters"]["required"]),
                set(tool["parameters"]["properties"]),
            )
        self.assertEqual(result.text, "I can help with that.")
        self.assertEqual(result.latency_ms, 125)
        self.assertEqual(result.usage.total_tokens, 100)
        self.assertEqual(deltas, ["I can help with that."])

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

    async def test_continuation_replays_output_and_appends_matching_results_without_storage(self) -> None:
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
        self.assertEqual(body["tool_choice"], "none")
        self.assertEqual(body["input"][0], {"role": "user", "content": CONTEXT})
        self.assertEqual(body["input"][1:3], previous_output)
        tool_output = body["input"][3]
        self.assertEqual(tool_output["type"], "function_call_output")
        self.assertEqual(tool_output["call_id"], "call-1")
        self.assertEqual(json.loads(tool_output["output"])["status"], "ok")
        self.assertEqual(result.text, "I found two BMWs.")

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


async def _append(values, value):
    values.append(value)


if __name__ == "__main__":
    unittest.main()
