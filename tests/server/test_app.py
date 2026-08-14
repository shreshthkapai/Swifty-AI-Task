import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx
from starlette.testclient import TestClient

from server.app import create_app
from server.observability import JsonEventLogger
from webchat.domain.errors import DealerError, DealerErrorKind, DealerFailure
from webchat.providers.base import PlanningProviderError, ProviderErrorKind

from .support import NOW, FakeRuntime, app_config, services


class ChatApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "chat.sqlite3"
        self.config = app_config(self.database_path)
        self.services, self.runtime = services(self.database_path)
        self.log_lines: list[str] = []
        self.app = create_app(
            config=self.config,
            services=self.services,
            logger=JsonEventLogger(sink=self.log_lines.append, clock=lambda: NOW),
            clock=lambda: NOW,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://webchat.test",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.temporary_directory.cleanup()

    async def test_health_is_public_and_does_not_create_a_session(self) -> None:
        response = await self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertNotIn("northstar_chat", response.cookies)

    async def test_session_cookie_and_transcript_survive_refresh(self) -> None:
        first = await self.client.get("/api/chat/session")
        cookie = first.headers["set-cookie"].lower()

        self.assertEqual(first.status_code, 200)
        self.assertIn("northstar_chat=", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        self.assertIn("path=/", cookie)
        self.assertIn(f"max-age={self.config.retention_seconds}", cookie)
        self.assertNotIn("secure", cookie)
        self.assertEqual(first.json()["messages"], [])

        turn = await self.client.post(
            "/api/chat/turns",
            json={
                "client_turn_id": "turn-1",
                "text": "Can I test drive this?",
                "page_observation": {
                    "current_url": "/?vehicle=veh-003#vehicles",
                    "page_vehicle_id": "veh-003",
                    "search_filters": {
                        "make": "BMW",
                        "body_style": "SUV",
                        "max_price_minor": 3_500_000,
                    },
                },
            },
        )
        restored = await self.client.get("/api/chat/session")

        self.assertEqual(turn.status_code, 200)
        self.assertFalse(turn.json()["duplicate"])
        self.assertEqual(turn.json()["revision"], 1)
        self.assertEqual(len(turn.json()["messages"]), 2)
        self.assertEqual(len(restored.json()["messages"]), 2)
        self.assertIn("northstar_chat=", restored.headers["set-cookie"].lower())
        self.assertIn(
            f"max-age={self.config.retention_seconds}",
            restored.headers["set-cookie"].lower(),
        )
        request = self.runtime.requests[0]
        self.assertEqual(request.current_input, "Can I test drive this?")
        self.assertEqual(request.page_observation.current_url, "/#vehicles")
        self.assertEqual(request.page_observation.page_vehicle_id, "veh-003")
        self.assertEqual(
            request.page_observation.search_filters_dict(),
            {"body_style": "SUV", "make": "BMW", "max_price_minor": 3_500_000},
        )
        self.assertEqual(request.page_observation.observed_at, NOW)
        self.assertFalse(request.page_observation.is_authoritative)

    async def test_duplicate_turn_returns_committed_result_without_runtime_call(self) -> None:
        body = {"client_turn_id": "turn-duplicate", "text": "Show me BMW SUVs"}

        first = await self.client.post("/api/chat/turns", json=body)
        second = await self.client.post("/api/chat/turns", json=body)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["duplicate"])
        self.assertEqual(second.json()["revision"], 1)
        self.assertEqual(second.json()["messages"], first.json()["messages"])
        self.assertEqual(len(self.runtime.requests), 1)

    async def test_action_reference_is_exclusive_with_text(self) -> None:
        await self.client.post("/api/chat/turns", json={
            "client_turn_id": "turn-seed", "text": "Show cars"
        })
        response = await self.client.post(
            "/api/chat/turns",
            json={
                "client_turn_id": "turn-action",
                "action": {"action_id": "server-action", "action_type": "show_more"},
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.runtime.requests[-1].current_input)
        self.assertEqual(self.runtime.requests[-1].action_reference.action_type, "show_more")
        submission = response.json()["messages"][0]["blocks"][0]["payload"]
        self.assertEqual(submission["action_id"], "server-action")

        invalid = await self.client.post(
            "/api/chat/turns",
            json={
                "client_turn_id": "turn-invalid",
                "text": "also text",
                "action": {"action_id": "server-action", "action_type": "show_more"},
            },
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()["error"]["code"], "invalid_request")

    async def test_validation_rejects_unknown_fields_actions_origins_and_large_bodies(self) -> None:
        invalid_payloads = (
            {"client_turn_id": "turn-1", "text": "hello", "extra": True},
            {"client_turn_id": "turn-2", "text": "x" * 201},
            {"client_turn_id": "turn-3", "action": {"action_id": "a", "action_type": "launch_missile"}},
            {"client_turn_id": "turn-4", "text": "hello", "page_observation": {"current_url": "https://evil.example/"}},
            {"client_turn_id": "turn-5", "text": "hello", "page_observation": {"search_filters": {"email": "alex@example.com"}}},
            {"client_turn_id": "turn-6", "text": "hello", "page_observation": {"search_filters": {"max_price_minor": "cheap"}}},
            {"client_turn_id": "contains spaces", "text": "hello"},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = await self.client.post("/api/chat/turns", json=payload)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"]["code"], "invalid_request")

        oversized = await self.client.post(
            "/api/chat/turns",
            content=b"{" + b"x" * self.config.max_body_bytes + b"}",
            headers={"content-type": "application/json"},
        )
        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(oversized.json()["error"]["code"], "request_too_large")

        wrong_media_type = await self.client.post(
            "/api/chat/turns",
            content=b'{"client_turn_id":"turn-plain","text":"hello"}',
            headers={"content-type": "text/plain"},
        )
        self.assertEqual(wrong_media_type.status_code, 415)
        self.assertEqual(
            wrong_media_type.json()["error"]["code"], "unsupported_media_type"
        )
        self.assertEqual(self.runtime.requests, [])

    async def test_cors_allows_only_configured_credentialed_origin(self) -> None:
        allowed = await self.client.options(
            "/api/chat/turns",
            headers={
                "origin": "http://localhost:4173",
                "access-control-request-method": "POST",
                "access-control-request-headers": "content-type",
            },
        )
        denied = await self.client.get(
            "/api/chat/session", headers={"origin": "https://evil.example"}
        )

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(
            allowed.headers["access-control-allow-origin"], "http://localhost:4173"
        )
        self.assertEqual(allowed.headers["access-control-allow-credentials"], "true")
        self.assertNotIn("access-control-allow-origin", denied.headers)

    async def test_concurrent_turns_for_one_cookie_are_serialized(self) -> None:
        await self.client.get("/api/chat/session")
        self.runtime.delay_seconds = 0.02

        responses = await asyncio.gather(
            self.client.post("/api/chat/turns", json={"client_turn_id": "turn-a", "text": "first"}),
            self.client.post("/api/chat/turns", json={"client_turn_id": "turn-b", "text": "second"}),
        )

        self.assertEqual({item.status_code for item in responses}, {200})
        self.assertEqual({item.json()["revision"] for item in responses}, {1, 2})
        self.assertEqual(self.runtime.max_active, 1)

    async def test_session_delete_waits_for_an_in_flight_turn(self) -> None:
        await self.client.get("/api/chat/session")
        self.runtime.delay_seconds = 0.03

        turn_task = asyncio.create_task(
            self.client.post(
                "/api/chat/turns",
                json={"client_turn_id": "turn-before-delete", "text": "book it"},
            )
        )
        await asyncio.sleep(0.005)
        delete_task = asyncio.create_task(self.client.delete("/api/chat/session"))
        turn, deleted = await asyncio.gather(turn_task, delete_task)

        self.assertEqual(turn.status_code, 200)
        self.assertEqual(deleted.status_code, 204)
        fresh = await self.client.get("/api/chat/session")
        self.assertEqual(fresh.json()["revision"], 0)
        self.assertEqual(fresh.json()["messages"], [])

    async def test_provider_and_dealer_failures_return_safe_stable_envelopes(self) -> None:
        failures = (
            (
                PlanningProviderError(ProviderErrorKind.TIMEOUT, retryable=True),
                "planner_unavailable",
            ),
            (
                DealerError(
                    DealerFailure(
                        DealerErrorKind.TEMPORARY_FAILURE,
                        retryable=True,
                        resource="secret-resource-detail",
                    )
                ),
                "dealer_unavailable",
            ),
        )
        for index, (failure, code) in enumerate(failures):
            with self.subTest(code=code):
                self.runtime.failure = failure
                response = await self.client.post(
                    "/api/chat/turns",
                    json={"client_turn_id": f"turn-failure-{index}", "text": "help"},
                )
                serialized = response.text
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["error"]["code"], code)
                self.assertNotIn("secret-resource-detail", serialized)
                events = [json.loads(line) for line in self.log_lines]
                turn_event = [
                    item for item in events if item["event"] == "chat_turn"
                ][-1]
                self.assertEqual(turn_event["turn_id"], f"turn-failure-{index}")
        session = await self.client.get("/api/chat/session")
        self.assertEqual(session.json()["revision"], 0)

    async def test_unexpected_turn_failure_is_safe_logged_and_not_committed(self) -> None:
        self.runtime.failure = RuntimeError("provider-secret jamie@example.com")

        response = await self.client.post(
            "/api/chat/turns",
            json={"client_turn_id": "turn-unexpected", "text": "help"},
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"]["code"], "internal_error")
        serialized = "\n".join(self.log_lines)
        self.assertNotIn("provider-secret", serialized)
        self.assertNotIn("jamie@example.com", serialized)
        events = [json.loads(line) for line in self.log_lines]
        turn = next(item for item in events if item["event"] == "chat_turn")
        self.assertEqual(turn["error_kind"], "internal_error")
        session = await self.client.get("/api/chat/session")
        self.assertEqual(session.json()["revision"], 0)

    async def test_restart_restores_cookie_session_from_sqlite(self) -> None:
        await self.client.post(
            "/api/chat/turns",
            json={"client_turn_id": "turn-before-restart", "text": "remember this"},
        )
        cookie_value = self.client.cookies["northstar_chat"]
        restarted_services, _ = services(self.database_path)
        restarted = create_app(
            config=self.config,
            services=restarted_services,
            clock=lambda: NOW,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted),
            base_url="http://webchat.test",
            cookies={"northstar_chat": cookie_value},
        ) as client:
            response = await client.get("/api/chat/session")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["revision"], 1)
        self.assertEqual(len(response.json()["messages"]), 2)

    async def test_delete_removes_state_and_expires_cookie(self) -> None:
        await self.client.post(
            "/api/chat/turns",
            json={"client_turn_id": "turn-delete", "text": "temporary"},
        )
        response = await self.client.delete("/api/chat/session")

        self.assertEqual(response.status_code, 204)
        self.assertIn("max-age=0", response.headers["set-cookie"].lower())
        fresh = await self.client.get("/api/chat/session")
        self.assertEqual(fresh.json()["revision"], 0)
        self.assertEqual(fresh.json()["messages"], [])

    async def test_turn_log_is_metadata_only_and_contains_metrics(self) -> None:
        await self.client.post(
            "/api/chat/turns",
            json={
                "client_turn_id": "turn-private",
                "text": "My email is jamie@example.com and phone is 07700900123",
            },
        )

        serialized = "\n".join(self.log_lines)
        events = [json.loads(line) for line in self.log_lines]
        turn_event = next(item for item in events if item["event"] == "chat_turn")
        self.assertEqual(turn_event["turn_id"], "turn-private")
        self.assertEqual(turn_event["model_calls"], 1)
        self.assertEqual(turn_event["input_tokens"], 10)
        self.assertEqual(turn_event["output_tokens"], 5)
        self.assertEqual(turn_event["outcome"], "ok")
        self.assertNotIn("jamie@example.com", serialized)
        self.assertNotIn("07700900123", serialized)
        self.assertNotIn("provider-secret", serialized)
        self.assertNotIn("dealer-secret", serialized)


class ApplicationStartupTests(unittest.TestCase):
    def test_factory_composes_and_closes_real_services_during_lifespan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = app_config(Path(directory) / "chat.sqlite3")
            application = create_app(config=config)

            with TestClient(application) as client:
                response = client.get("/health")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"status": "ok"})
