import json
import unittest
from datetime import UTC, datetime

import httpx

from webchat.harness.context import ContextCompiler
from webchat.harness.state import ConversationState
from webchat.harness.tool_gate import ToolGate
from webchat.providers.base import (
    PlanningOutputError,
    PlanningOutputErrorKind,
    PlanningProviderError,
    PlanningRequest,
    ProviderErrorKind,
)
from webchat.providers.openai import OpenAIPlanningProvider, OpenAIProviderConfig


NOW = datetime(2026, 8, 13, 10, tzinfo=UTC)


def planning_request(*, workshop_only: bool = False) -> PlanningRequest:
    state = ConversationState()
    text = "Find me a BMW"
    if workshop_only:
        from webchat.harness.state import WorkflowDomain, WorkflowStage, WorkflowState

        state = ConversationState(
            workflow=WorkflowState(
                domain=WorkflowDomain.WORKSHOP,
                stage=WorkflowStage.DISCOVERY,
            )
        )
        text = "Saturday morning please"
    now = NOW
    selection = ToolGate().select(state=state, current_input=text, now=now)
    context = ContextCompiler().compile(
        current_input=text,
        now=now,
        state=state,
        tool_selection=selection,
    )
    return PlanningRequest(
        context=context.serialized,
        commands=selection.specifications,
    )


def response_payload(plan: dict | None = None) -> dict:
    plan = plan or {
        "schema_version": 1,
        "scope": "in_domain",
        "commands": [
            {
                "name": "search_vehicles",
                "arguments": {
                    "availability": None,
                    "body_style": None,
                    "currency": None,
                    "dealership_id": None,
                    "fuel_type": None,
                    "make": "BMW",
                    "max_mileage": None,
                    "max_price_minor": None,
                    "min_price_minor": None,
                    "min_year": None,
                    "model": None,
                    "page": None,
                    "page_size": None,
                    "query": None,
                    "refinement": None,
                    "sort": None,
                    "transmission": None,
                },
            }
        ],
        "response_strategy": "search_results",
        "clarification_question": None,
        "adjacent_advice": None,
    }
    return {
        "id": "resp-secret-provider-id",
        "object": "response",
        "status": "completed",
        "model": "gpt-test-snapshot",
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": json.dumps(plan)}
                ],
            }
        ],
        "usage": {"input_tokens": 90, "output_tokens": 15, "total_tokens": 105},
    }


class OpenAIProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_planner_guidance_uses_known_state_and_bounded_adjacent_next_steps(self) -> None:
        from webchat.providers.openai import _PLANNER_INSTRUCTIONS

        self.assertIn("already marked known", _PLANNER_INSTRUCTIONS)
        self.assertIn("harness adds the dealership next step", _PLANNER_INSTRUCTIONS)
        self.assertIn("Do not mention stock", _PLANNER_INSTRUCTIONS)
        self.assertIn("lower_max_price", _PLANNER_INSTRUCTIONS)
        self.assertIn("holiday exceptions", _PLANNER_INSTRUCTIONS)

    async def test_posts_stateless_strict_structured_request_and_parses_metrics(self) -> None:
        captured = []

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json=response_payload())

        times = iter((10.0, 10.125))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAIPlanningProvider(
                client,
                OpenAIProviderConfig(
                    api_key="test-api-key",
                    model="gpt-test",
                    base_url="https://api.openai.test/v1/",
                    timeout_seconds=7.5,
                ),
                monotonic=lambda: next(times),
            )
            result = await provider.plan(planning_request())

        self.assertEqual(len(captured), 1)
        request = captured[0]
        body = json.loads(request.content)
        self.assertEqual(str(request.url), "https://api.openai.test/v1/responses")
        self.assertEqual(request.headers["Authorization"], "Bearer test-api-key")
        self.assertFalse(body["store"])
        self.assertNotIn("previous_response_id", body)
        self.assertEqual(body["input"], planning_request().context)
        self.assertTrue(body["text"]["format"]["strict"])
        schema = body["text"]["format"]["schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(schema["properties"]["commands"]["maxItems"], 2)
        self.assertEqual(result.plan.commands[0].name.value, "search_vehicles")
        self.assertEqual(result.usage.total_tokens, 105)
        self.assertEqual(result.latency_ms, 125.0)
        self.assertEqual(result.provider, "openai")
        self.assertEqual(result.model, "gpt-test-snapshot")
        self.assertFalse(hasattr(result, "response_id"))

    async def test_dynamic_schema_contains_only_request_visible_commands(self) -> None:
        bodies = []

        async def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            plan = {
                "schema_version": 1,
                "scope": "in_domain",
                "commands": [
                    {
                        "name": "find_workshop_slots",
                        "arguments": {
                            "date_from": None,
                            "date_to": None,
                            "dealership_id": None,
                            "service_type_id": None,
                        },
                    }
                ],
                "response_strategy": "slot_results",
                "clarification_question": None,
                "adjacent_advice": None,
            }
            return httpx.Response(200, json=response_payload(plan))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAIPlanningProvider(
                client,
                OpenAIProviderConfig(api_key="key", model="gpt-test"),
            )
            await provider.plan(planning_request(workshop_only=True))

        variants = bodies[0]["text"]["format"]["schema"]["properties"]["commands"]["items"]["anyOf"]
        names = {item["properties"]["name"]["const"] for item in variants}
        self.assertIn("find_workshop_slots", names)
        self.assertNotIn("prepare_test_drive_booking", names)
        for variant in variants:
            self.assertTrue(variant["description"])
            self.assertFalse(variant["additionalProperties"])
            arguments = variant["properties"]["arguments"]
            self.assertFalse(arguments["additionalProperties"])
            self.assertEqual(set(arguments["required"]), set(arguments["properties"]))
        search = next(
            item for item in variants
            if item["properties"]["name"]["const"] == "search_vehicles"
        )
        availability = search["properties"]["arguments"]["properties"]["availability"]
        self.assertIn(None, availability["enum"])

    async def test_invalid_json_and_refusal_are_stable_provider_failures(self) -> None:
        payloads = (
            {
                **response_payload(),
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": "{"}]}
                ],
            },
            {
                **response_payload(),
                "output": [
                    {"type": "message", "content": [{"type": "refusal", "refusal": "No"}]}
                ],
            },
        )
        expected = (
            (PlanningOutputError, PlanningOutputErrorKind.INVALID_JSON),
            (PlanningProviderError, ProviderErrorKind.REFUSAL),
        )
        for payload, (error_type, kind) in zip(payloads, expected, strict=True):
            with self.subTest(kind=kind):
                async def handler(_: httpx.Request, payload=payload) -> httpx.Response:
                    return httpx.Response(200, json=payload)

                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    provider = OpenAIPlanningProvider(
                        client,
                        OpenAIProviderConfig(api_key="key", model="gpt-test"),
                    )
                    with self.assertRaises(error_type) as raised:
                        await provider.plan(planning_request())
                self.assertIs(raised.exception.kind, kind)

    async def test_well_formed_invalid_plan_is_a_safe_planner_failure(self) -> None:
        payload = response_payload(
            {
                "schema_version": 1,
                "scope": "in_domain",
                "commands": [],
                "response_strategy": "search_results",
                "clarification_question": None,
                "adjacent_advice": None,
            }
        )

        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAIPlanningProvider(
                client,
                OpenAIProviderConfig(api_key="key", model="gpt-test"),
            )
            with self.assertRaises(PlanningOutputError) as raised:
                await provider.plan(planning_request())

        self.assertIs(raised.exception.kind, PlanningOutputErrorKind.INVALID_PLAN)
        self.assertEqual(str(raised.exception), "invalid_plan")

    async def test_timeout_and_http_status_map_without_leaking_response_text(self) -> None:
        cases = (
            (httpx.ReadTimeout("slow"), ProviderErrorKind.TIMEOUT, True),
            (429, ProviderErrorKind.HTTP_ERROR, True),
            (500, ProviderErrorKind.HTTP_ERROR, True),
            (400, ProviderErrorKind.HTTP_ERROR, False),
        )
        for outcome, kind, retryable in cases:
            with self.subTest(outcome=outcome):
                async def handler(request: httpx.Request, outcome=outcome) -> httpx.Response:
                    if isinstance(outcome, Exception):
                        raise outcome
                    return httpx.Response(outcome, text="secret upstream detail")

                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    provider = OpenAIPlanningProvider(
                        client,
                        OpenAIProviderConfig(api_key="key", model="gpt-test"),
                    )
                    with self.assertRaises(PlanningProviderError) as raised:
                        await provider.plan(planning_request())
                self.assertIs(raised.exception.kind, kind)
                self.assertEqual(raised.exception.retryable, retryable)
                self.assertNotIn("secret", str(raised.exception))

    def test_configuration_is_explicit_validated_and_hides_the_key(self) -> None:
        config = OpenAIProviderConfig(api_key="super-secret", model="gpt-test")

        self.assertNotIn("super-secret", repr(config))
        for kwargs in (
            {"api_key": "", "model": "gpt-test"},
            {"api_key": "key", "model": ""},
            {"api_key": "key", "model": "gpt-test", "timeout_seconds": 0},
            {"api_key": "key", "model": "gpt-test", "max_output_tokens": True},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    OpenAIProviderConfig(**kwargs)


if __name__ == "__main__":
    unittest.main()
