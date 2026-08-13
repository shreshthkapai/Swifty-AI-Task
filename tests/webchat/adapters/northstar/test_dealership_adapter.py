import unittest
from collections.abc import Callable

import httpx

from webchat.adapters.northstar.adapter import NorthstarAdapter, operation_retry_count
from webchat.adapters.northstar.client import NorthstarClient
from webchat.adapters.northstar.config import NorthstarConfig
from webchat.domain import (
    ContactMethod,
    CustomerIdentity,
    DealerErrorKind,
    DealershipMessageRequest,
    Department,
)

from tests.webchat.adapters.northstar.support import (
    BUSINESS_INFORMATION_PAYLOAD,
    LOCATION_PAYLOAD,
    OPENING_HOURS_PAYLOAD,
    platform_error,
)


class MutableClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class NorthstarDealershipAdapterTestCase(unittest.IsolatedAsyncioTestCase):
    def make_adapter(
        self,
        handler: Callable[[httpx.Request], httpx.Response],
        *,
        clock: MutableClock | None = None,
        sleep: Callable[[float], object] | None = None,
    ) -> NorthstarAdapter:
        config = NorthstarConfig(
            base_url="http://northstar.test",
            api_key="server-secret",
        )
        http = httpx.AsyncClient(
            base_url=config.base_url,
            transport=httpx.MockTransport(handler),
        )
        adapter = NorthstarAdapter(
            NorthstarClient(config, http),
            monotonic=MutableClock() if clock is None else clock,
            **({} if sleep is None else {"sleep": sleep}),
        )
        self.addAsyncCleanup(adapter.aclose)
        return adapter

    async def test_dealership_list_populates_cache_for_direct_read(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"items": [LOCATION_PAYLOAD]})

        adapter = self.make_adapter(handler)

        locations = await adapter.list_dealerships()
        location = await adapter.get_dealership("northstar-manchester")

        self.assertEqual(locations, (location,))
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.path, "/api/dealerships")
        self.assertNotIn("X-API-Key", requests[0].headers)

    async def test_direct_dealership_cache_expires_after_configured_ttl(self) -> None:
        clock = MutableClock()
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=LOCATION_PAYLOAD)

        adapter = self.make_adapter(handler, clock=clock)

        first = await adapter.get_dealership("northstar-manchester")
        clock.now = 299.9
        cached = await adapter.get_dealership("northstar-manchester")
        clock.now = 300.1
        refreshed = await adapter.get_dealership("northstar-manchester")

        self.assertEqual(first, cached)
        self.assertEqual(cached, refreshed)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].url.path, "/api/dealerships/northstar-manchester")

    async def test_workshop_locations_share_dealership_cache(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"items": [LOCATION_PAYLOAD]})

        adapter = self.make_adapter(handler)

        locations = await adapter.list_workshop_locations()
        cached = await adapter.get_dealership("northstar-manchester")

        self.assertEqual(locations, (cached,))
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.path, "/api/workshop-locations")

    async def test_opening_hours_use_exact_path_and_domain_filter(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=OPENING_HOURS_PAYLOAD)

        adapter = self.make_adapter(handler)

        hours = await adapter.get_opening_hours(
            "northstar-manchester",
            Department.SERVICE,
        )

        self.assertEqual(tuple(item.department for item in hours.regular), (Department.SERVICE,))
        self.assertEqual(
            requests[0].url.path,
            "/api/dealerships/northstar-manchester/opening-hours",
        )

    async def test_general_opening_hours_are_rejected_without_http(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=OPENING_HOURS_PAYLOAD)

        adapter = self.make_adapter(handler)

        with self.assertRaises(Exception) as raised:
            await adapter.get_opening_hours("northstar-manchester", Department.GENERAL)

        self.assertEqual(raised.exception.kind, DealerErrorKind.INVALID_REQUEST)
        self.assertEqual(calls, 0)

    async def test_message_create_sends_exact_protected_idempotent_request(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                201,
                json={
                    "id": "msg-1",
                    "reference": "MSG-000001",
                    "dealershipId": "northstar-manchester",
                    "department": "sales",
                    "subject": "Vehicle question",
                    "message": "Please contact me about the X3.",
                    "firstName": "Jamie",
                    "lastName": "Taylor",
                    "email": "jamie@example.com",
                    "phone": "07700900123",
                    "preferredContactMethod": "phone",
                    "status": "received",
                    "createdAt": "2026-08-12T10:30:00+00:00",
                },
            )

        adapter = self.make_adapter(handler)
        request = DealershipMessageRequest(
            dealership_id="northstar-manchester",
            department=Department.SALES,
            subject="Vehicle question",
            message="Please contact me about the X3.",
            customer=CustomerIdentity("Jamie", "Taylor", "jamie@example.com", "07700900123"),
            preferred_contact_method=ContactMethod.PHONE,
        )

        result = await adapter.send_dealership_message(request, idempotency_key="action-1")

        sent = requests[0]
        self.assertEqual(result.id, "msg-1")
        self.assertEqual(sent.method, "POST")
        self.assertEqual(sent.url.path, "/api/dealership-messages")
        self.assertEqual(sent.headers["X-API-Key"], "server-secret")
        self.assertEqual(sent.headers["Idempotency-Key"], "action-1")
        self.assertEqual(
            sent.content,
            b'{"dealershipId":"northstar-manchester","department":"sales",'
            b'"subject":"Vehicle question","message":"Please contact me about the X3.",'
            b'"firstName":"Jamie","lastName":"Taylor","email":"jamie@example.com",'
            b'"phone":"07700900123","preferredContactMethod":"phone"}',
        )

    async def test_business_information_is_public_and_not_cached(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=BUSINESS_INFORMATION_PAYLOAD)

        adapter = self.make_adapter(handler)

        first = await adapter.get_business_information()
        second = await adapter.get_business_information()

        self.assertEqual(first, second)
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(item.url.path == "/api/business-information" for item in requests))
        self.assertTrue(all("X-API-Key" not in item.headers for item in requests))

    async def test_read_retries_only_temporary_failures_with_configured_delays(self) -> None:
        calls = 0
        delays: list[float] = []

        async def sleep(delay: float) -> None:
            delays.append(delay)

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls < 3:
                return httpx.Response(
                    500,
                    json=platform_error("INTERNAL_ERROR", retryable=True),
                )
            return httpx.Response(200, json={"items": [LOCATION_PAYLOAD]})

        adapter = self.make_adapter(handler, sleep=sleep)

        result = await adapter.list_dealerships()

        self.assertEqual(len(result), 1)
        self.assertEqual(calls, 3)
        self.assertEqual(delays, [0.1, 0.25])

    async def test_business_error_is_not_retried(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(404, json=platform_error("NOT_FOUND"))

        adapter = self.make_adapter(handler)

        with self.assertRaises(Exception) as raised:
            await adapter.get_dealership("missing")

        self.assertEqual(raised.exception.kind, DealerErrorKind.NOT_FOUND)
        self.assertEqual(calls, 1)

    async def test_retry_count_is_exposed_to_same_task_for_observability(self) -> None:
        calls = 0

        async def sleep(delay: float) -> None:
            return None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls < 3:
                return httpx.Response(
                    500,
                    json=platform_error("INTERNAL_ERROR", retryable=True),
                )
            return httpx.Response(200, json={"items": [LOCATION_PAYLOAD]})

        adapter = self.make_adapter(handler, sleep=sleep)
        await adapter.list_dealerships()

        self.assertEqual(operation_retry_count(), 2)

    async def test_dealership_identifier_is_encoded_as_one_path_segment(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(404, json=platform_error("NOT_FOUND"))

        adapter = self.make_adapter(handler)

        with self.assertRaises(Exception):
            await adapter.get_dealership("northstar/manchester")

        self.assertEqual(
            requests[0].url.raw_path,
            b"/api/dealerships/northstar%2Fmanchester",
        )


if __name__ == "__main__":
    unittest.main()
