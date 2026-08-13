import unittest
from collections.abc import Callable
from datetime import date

import httpx

from webchat.adapters.northstar.adapter import NorthstarAdapter
from webchat.adapters.northstar.client import NorthstarClient
from webchat.adapters.northstar.config import NorthstarConfig
from webchat.domain import (
    BookingStatus,
    CustomerIdentity,
    DealerErrorKind,
    WorkshopBookingAmendment,
    WorkshopBookingLookup,
    WorkshopBookingRequest,
    WorkshopCancellationRequest,
    WorkshopSlotSearch,
)

from tests.webchat.adapters.northstar.support import (
    LOCATION_PAYLOAD,
    WORKSHOP_BOOKING_PAYLOAD,
    WORKSHOP_LOOKUP_PAYLOAD,
    WORKSHOP_SERVICE_PAYLOAD,
    WORKSHOP_SLOT_PAYLOAD,
    platform_error,
)


class NorthstarWorkshopAdapterTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.customer = CustomerIdentity(
            "Jamie",
            "Taylor",
            "jamie@example.com",
            "07700900123",
        )

    def make_adapter(
        self,
        handler: Callable[[httpx.Request], httpx.Response],
        *,
        sleep: Callable[[float], object] | None = None,
    ) -> NorthstarAdapter:
        config = NorthstarConfig("http://northstar.test", "server-secret")
        http = httpx.AsyncClient(
            base_url=config.base_url,
            transport=httpx.MockTransport(handler),
        )
        adapter = NorthstarAdapter(
            NorthstarClient(config, http),
            **({} if sleep is None else {"sleep": sleep}),
        )
        self.addAsyncCleanup(adapter.aclose)
        return adapter

    async def test_service_types_and_slots_are_public_reads(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/api/service-types":
                return httpx.Response(200, json={"items": [WORKSHOP_SERVICE_PAYLOAD]})
            return httpx.Response(200, json={"items": [WORKSHOP_SLOT_PAYLOAD]})

        adapter = self.make_adapter(handler)
        services = await adapter.list_service_types()
        slots = await adapter.list_workshop_slots(
            WorkshopSlotSearch(
                dealership_id="northstar-manchester",
                service_type_id="service-mot",
                date_from=date(2026, 8, 14),
            )
        )

        self.assertEqual(services[0].id, "service-mot")
        self.assertEqual(slots[0].id, "ws-slot-1")
        self.assertEqual(
            tuple(item.url.path for item in requests),
            ("/api/service-types", "/api/workshop-availability"),
        )
        self.assertEqual(
            dict(requests[1].url.params),
            {
                "dealershipId": "northstar-manchester",
                "serviceTypeId": "service-mot",
                "dateFrom": "2026-08-14",
            },
        )
        self.assertTrue(all("X-API-Key" not in item.headers for item in requests))

    async def test_workshop_booking_is_protected_and_idempotent(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(201, json=WORKSHOP_BOOKING_PAYLOAD)

        adapter = self.make_adapter(handler)
        result = await adapter.book_workshop(
            WorkshopBookingRequest(
                "ws-slot-1",
                self.customer,
                "AB12 CDE",
                50000,
                "Check the brakes.",
            ),
            idempotency_key="workshop-action",
        )

        request = requests[0]
        self.assertEqual(result.id, "wsb-1")
        self.assertEqual(request.url.path, "/api/workshop-bookings")
        self.assertEqual(request.headers["X-API-Key"], "server-secret")
        self.assertEqual(request.headers["Idempotency-Key"], "workshop-action")

    async def test_lookup_verifies_identity_then_enriches_from_dealership(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/api/workshop-bookings/lookup":
                return httpx.Response(200, json=WORKSHOP_LOOKUP_PAYLOAD)
            return httpx.Response(200, json=LOCATION_PAYLOAD)

        adapter = self.make_adapter(handler)
        details = await adapter.lookup_workshop_booking(
            WorkshopBookingLookup(
                "WORK-000001",
                "Taylor",
                "AB12 CDE",
                "07700900123",
            )
        )

        self.assertEqual(details.booking.id, "wsb-1")
        self.assertEqual(
            tuple(item.url.path for item in requests),
            (
                "/api/workshop-bookings/lookup",
                "/api/dealerships/northstar-manchester",
            ),
        )
        self.assertEqual(requests[0].method, "POST")
        self.assertEqual(requests[0].headers["X-API-Key"], "server-secret")
        self.assertNotIn("Idempotency-Key", requests[0].headers)

    async def test_lookup_enrichment_reuses_location_cache(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/api/dealerships":
                return httpx.Response(200, json={"items": [LOCATION_PAYLOAD]})
            return httpx.Response(200, json=WORKSHOP_LOOKUP_PAYLOAD)

        adapter = self.make_adapter(handler)
        await adapter.list_dealerships()
        await adapter.lookup_workshop_booking(
            WorkshopBookingLookup("WORK-000001", "Taylor", "AB12 CDE", "07700900123")
        )

        self.assertEqual(
            tuple(item.url.path for item in requests),
            ("/api/dealerships", "/api/workshop-bookings/lookup"),
        )

    async def test_raw_booking_read_is_protected(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=WORKSHOP_BOOKING_PAYLOAD)

        adapter = self.make_adapter(handler)
        result = await adapter.get_workshop_booking("wsb-1")

        self.assertEqual(result.id, "wsb-1")
        self.assertEqual(requests[0].url.path, "/api/workshop-bookings/wsb-1")
        self.assertEqual(requests[0].headers["X-API-Key"], "server-secret")

    async def test_amendment_makes_one_patch_and_returns_result(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    **WORKSHOP_BOOKING_PAYLOAD,
                    "mileage": 51000,
                    "notes": None,
                    "updatedAt": "2026-08-12T11:00:00+00:00",
                },
            )

        adapter = self.make_adapter(handler)
        result = await adapter.amend_workshop_booking(
            WorkshopBookingAmendment("wsb-1", mileage=51000, notes="")
        )

        self.assertEqual(result.mileage, 51000)
        self.assertIsNone(result.notes)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].method, "PATCH")
        self.assertEqual(requests[0].content, b'{"mileage":51000,"notes":""}')

    async def test_ambiguous_amendment_reconciles_without_second_patch(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "PATCH":
                raise httpx.ReadTimeout("ambiguous", request=request)
            return httpx.Response(
                200,
                json={
                    **WORKSHOP_BOOKING_PAYLOAD,
                    "slotId": "ws-slot-2",
                    "dealershipId": "northstar-stockport",
                    "serviceTypeId": "service-full",
                    "mileage": 51000,
                    "updatedAt": "2026-08-12T11:00:00+00:00",
                },
            )

        adapter = self.make_adapter(handler)
        result = await adapter.amend_workshop_booking(
            WorkshopBookingAmendment("wsb-1", slot_id="ws-slot-2", mileage=51000)
        )

        self.assertEqual(result.slot_id, "ws-slot-2")
        self.assertEqual(tuple(item.method for item in requests), ("PATCH", "GET"))

    async def test_ambiguous_amendment_mismatch_raises_without_second_patch(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "PATCH":
                raise httpx.ReadTimeout("ambiguous", request=request)
            return httpx.Response(200, json=WORKSHOP_BOOKING_PAYLOAD)

        adapter = self.make_adapter(handler)

        with self.assertRaises(Exception) as raised:
            await adapter.amend_workshop_booking(
                WorkshopBookingAmendment("wsb-1", mileage=51000)
            )

        self.assertEqual(raised.exception.kind, DealerErrorKind.TEMPORARY_FAILURE)
        self.assertEqual(tuple(item.method for item in requests), ("PATCH", "GET"))

    async def test_cancellation_retries_because_delete_is_repeat_safe(self) -> None:
        requests: list[httpx.Request] = []
        delays: list[float] = []

        async def sleep(delay: float) -> None:
            delays.append(delay)

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(
                    500,
                    json=platform_error("INTERNAL_ERROR", retryable=True),
                )
            return httpx.Response(
                200,
                json={
                    **WORKSHOP_BOOKING_PAYLOAD,
                    "status": "cancelled",
                    "updatedAt": "2026-08-12T11:00:00+00:00",
                    "cancelledAt": "2026-08-12T11:00:00+00:00",
                },
            )

        adapter = self.make_adapter(handler, sleep=sleep)
        result = await adapter.cancel_workshop_booking(WorkshopCancellationRequest("wsb-1"))

        self.assertIs(result.status, BookingStatus.CANCELLED)
        self.assertEqual(tuple(item.method for item in requests), ("DELETE", "DELETE"))
        self.assertEqual(delays, [0.1])


if __name__ == "__main__":
    unittest.main()
