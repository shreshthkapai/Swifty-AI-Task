import unittest

import httpx

from webchat.adapters.northstar.client import NorthstarClient
from webchat.adapters.northstar.config import NorthstarConfig
from webchat.domain import DealerErrorKind


class NorthstarClientTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.config = NorthstarConfig(
            base_url="http://northstar.test",
            api_key="server-secret",
        )

    async def test_request_sends_exact_public_query_without_credentials(self) -> None:
        received: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            received.append(request)
            return httpx.Response(200, json={"items": []})

        http = httpx.AsyncClient(
            base_url=self.config.base_url,
            transport=httpx.MockTransport(handler),
        )
        client = NorthstarClient(self.config, http)

        result = await client.request(
            "GET",
            "/api/vehicles",
            params={"make": "BMW", "page": 2},
        )

        self.assertEqual(result, {"items": []})
        self.assertEqual(len(received), 1)
        self.assertEqual(str(received[0].url), "http://northstar.test/api/vehicles?make=BMW&page=2")
        self.assertEqual(received[0].headers["Accept"], "application/json")
        self.assertNotIn("X-API-Key", received[0].headers)
        await client.aclose()

    async def test_protected_create_sends_credentials_key_and_json(self) -> None:
        received: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            received.append(request)
            return httpx.Response(201, json={"id": "record-1"})

        http = httpx.AsyncClient(
            base_url=self.config.base_url,
            transport=httpx.MockTransport(handler),
        )
        client = NorthstarClient(self.config, http)

        await client.request(
            "POST",
            "/api/test-drive-bookings",
            json={"slotId": "slot-1"},
            protected=True,
            idempotency_key="action-123",
        )

        request = received[0]
        self.assertEqual(request.headers["X-API-Key"], "server-secret")
        self.assertEqual(request.headers["Idempotency-Key"], "action-123")
        self.assertEqual(request.headers["Content-Type"], "application/json")
        self.assertEqual(request.content, b'{"slotId":"slot-1"}')
        await client.aclose()

    async def test_client_owns_and_closes_injected_http_client(self) -> None:
        http = httpx.AsyncClient(
            base_url=self.config.base_url,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        )
        client = NorthstarClient(self.config, http)

        await client.aclose()

        self.assertTrue(http.is_closed)

    async def test_client_context_manager_closes_injected_http_client(self) -> None:
        http = httpx.AsyncClient(
            base_url=self.config.base_url,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        )

        async with NorthstarClient(self.config, http) as client:
            self.assertIsInstance(client, NorthstarClient)

        self.assertTrue(http.is_closed)

    async def test_absolute_or_non_api_path_is_rejected_before_transport(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={})

        http = httpx.AsyncClient(
            base_url=self.config.base_url,
            transport=httpx.MockTransport(handler),
        )
        client = NorthstarClient(self.config, http)

        for path in ("https://attacker.test/api/vehicles", "/health", "api/vehicles"):
            with self.subTest(path=path), self.assertRaises(Exception) as raised:
                await client.request("GET", path)
            self.assertEqual(raised.exception.kind, DealerErrorKind.INVALID_REQUEST)

        self.assertEqual(calls, 0)
        await client.aclose()

    async def test_transport_failure_becomes_temporary_failure_without_retry(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ConnectError("customer data must not escape", request=request)

        http = httpx.AsyncClient(
            base_url=self.config.base_url,
            transport=httpx.MockTransport(handler),
        )
        client = NorthstarClient(self.config, http)

        with self.assertRaises(Exception) as raised:
            await client.request("GET", "/api/vehicles")

        self.assertEqual(raised.exception.kind, DealerErrorKind.TEMPORARY_FAILURE)
        self.assertTrue(raised.exception.retryable)
        self.assertNotIn("customer data", str(raised.exception))
        self.assertEqual(calls, 1)
        await client.aclose()

    async def test_injected_http_client_must_target_configured_origin(self) -> None:
        http = httpx.AsyncClient(
            base_url="http://different.test",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        )

        with self.assertRaises(ValueError):
            NorthstarClient(self.config, http)

        await http.aclose()

    async def test_non_object_or_malformed_success_json_is_invalid_response(self) -> None:
        responses = (
            httpx.Response(200, json=[]),
            httpx.Response(200, content=b"not-json", headers={"Content-Type": "application/json"}),
        )

        for response in responses:
            with self.subTest(content=response.content):
                http = httpx.AsyncClient(
                    base_url=self.config.base_url,
                    transport=httpx.MockTransport(lambda request, value=response: value),
                )
                client = NorthstarClient(self.config, http)
                with self.assertRaises(Exception) as raised:
                    await client.request("GET", "/api/vehicles")
                self.assertEqual(raised.exception.kind, DealerErrorKind.INVALID_RESPONSE)
                await client.aclose()


if __name__ == "__main__":
    unittest.main()
