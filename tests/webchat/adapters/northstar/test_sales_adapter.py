import unittest
from collections.abc import Callable
from datetime import date

import httpx

from webchat.adapters.northstar.adapter import NorthstarAdapter
from webchat.adapters.northstar.client import NorthstarClient
from webchat.adapters.northstar.config import NorthstarConfig
from webchat.domain import (
    CallbackRequest,
    CustomerIdentity,
    DealerErrorKind,
    Department,
    EnquiryType,
    PartExchangeCondition,
    PartExchangeRequest,
    PartExchangeVehicle,
    SalesEnquiryRequest,
    TestDriveBookingRequest,
    TestDriveSlotSearch,
    VehicleInterestRequest,
)

from tests.webchat.adapters.northstar.support import (
    CALLBACK_PAYLOAD,
    PART_EXCHANGE_PAYLOAD,
    SALES_ENQUIRY_PAYLOAD,
    TEST_DRIVE_BOOKING_PAYLOAD,
    TEST_DRIVE_SLOT_PAYLOAD,
    VEHICLE_INTEREST_PAYLOAD,
    platform_error,
)


class NorthstarSalesAdapterTestCase(unittest.IsolatedAsyncioTestCase):
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

    async def test_test_drive_slots_send_exact_public_filters(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"items": [TEST_DRIVE_SLOT_PAYLOAD]})

        adapter = self.make_adapter(handler)
        result = await adapter.list_test_drive_slots(
            TestDriveSlotSearch(
                vehicle_id="veh-003",
                dealership_id="northstar-manchester",
                date_from=date(2026, 8, 13),
                date_to=date(2026, 8, 20),
            )
        )

        self.assertEqual(result[0].id, "td-slot-003-1")
        self.assertEqual(requests[0].url.path, "/api/test-drive-slots")
        self.assertEqual(
            dict(requests[0].url.params),
            {
                "vehicleId": "veh-003",
                "dealershipId": "northstar-manchester",
                "dateFrom": "2026-08-13",
                "dateTo": "2026-08-20",
            },
        )
        self.assertNotIn("X-API-Key", requests[0].headers)

    async def test_all_five_sales_creates_are_protected_and_idempotent(self) -> None:
        responses = {
            "/api/sales-enquiries": SALES_ENQUIRY_PAYLOAD,
            "/api/test-drive-bookings": TEST_DRIVE_BOOKING_PAYLOAD,
            "/api/vehicle-interests": VEHICLE_INTEREST_PAYLOAD,
            "/api/callback-requests": CALLBACK_PAYLOAD,
            "/api/part-exchange-valuations": PART_EXCHANGE_PAYLOAD,
        }
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(201, json=responses[request.url.path])

        adapter = self.make_adapter(handler)
        operations = (
            adapter.create_sales_enquiry(
                SalesEnquiryRequest(
                    "northstar-manchester",
                    EnquiryType.PART_EXCHANGE,
                    self.customer,
                    "Please contact me about this vehicle.",
                    "veh-003",
                ),
                idempotency_key="key-enquiry",
            ),
            adapter.book_test_drive(
                TestDriveBookingRequest("td-slot-003-1", self.customer, "Morning preferred."),
                idempotency_key="key-test-drive",
            ),
            adapter.register_vehicle_interest(
                VehicleInterestRequest("veh-020", self.customer),
                idempotency_key="key-interest",
            ),
            adapter.request_callback(
                CallbackRequest(
                    "northstar-manchester",
                    Department.SALES,
                    self.customer,
                    "Discuss vehicle finance.",
                    "Tomorrow afternoon",
                    "veh-003",
                ),
                idempotency_key="key-callback",
            ),
            adapter.value_part_exchange(
                PartExchangeRequest(
                    "northstar-manchester",
                    PartExchangeVehicle("AB12 CDE", 50000, PartExchangeCondition.GOOD),
                    self.customer,
                ),
                idempotency_key="key-part-exchange",
            ),
        )

        results = [await operation for operation in operations]

        self.assertEqual(
            tuple(result.id for result in results),
            ("enq-1", "tdb-1", "int-1", "call-1", "px-1"),
        )
        self.assertEqual(tuple(request.url.path for request in requests), tuple(responses))
        self.assertEqual(
            tuple(request.headers["Idempotency-Key"] for request in requests),
            (
                "key-enquiry",
                "key-test-drive",
                "key-interest",
                "key-callback",
                "key-part-exchange",
            ),
        )
        self.assertTrue(all(request.headers["X-API-Key"] == "server-secret" for request in requests))

    async def test_create_retry_reuses_identical_body_and_key(self) -> None:
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
            return httpx.Response(201, json=TEST_DRIVE_BOOKING_PAYLOAD)

        adapter = self.make_adapter(handler, sleep=sleep)
        result = await adapter.book_test_drive(
            TestDriveBookingRequest("td-slot-003-1", self.customer, "Morning preferred."),
            idempotency_key="stable-action-key",
        )

        self.assertEqual(result.id, "tdb-1")
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].content, requests[1].content)
        self.assertEqual(requests[0].headers["Idempotency-Key"], "stable-action-key")
        self.assertEqual(requests[1].headers["Idempotency-Key"], "stable-action-key")
        self.assertEqual(delays, [0.1])

    async def test_vehicle_state_conflict_is_not_retried(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(409, json=platform_error("VEHICLE_RESERVED"))

        adapter = self.make_adapter(handler)

        with self.assertRaises(Exception) as raised:
            await adapter.book_test_drive(
                TestDriveBookingRequest("td-slot-1", self.customer),
                idempotency_key="stable-action-key",
            )

        self.assertEqual(raised.exception.kind, DealerErrorKind.VEHICLE_RESERVED)
        self.assertEqual(calls, 1)

    async def test_general_callback_is_rejected_before_transport(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(201, json=CALLBACK_PAYLOAD)

        adapter = self.make_adapter(handler)

        with self.assertRaises(Exception) as raised:
            await adapter.request_callback(
                CallbackRequest(
                    "northstar-manchester",
                    Department.GENERAL,
                    self.customer,
                    "Please contact me.",
                ),
                idempotency_key="key-callback",
            )

        self.assertEqual(raised.exception.kind, DealerErrorKind.INVALID_REQUEST)
        self.assertEqual(calls, 0)

    async def test_validation_fields_are_exposed_as_domain_paths(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                422,
                json={
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "Validation failed.",
                        "fieldErrors": {"phone": "Enter a UK phone number."},
                        "retryable": False,
                    }
                },
            )

        adapter = self.make_adapter(handler)

        with self.assertRaises(Exception) as raised:
            await adapter.create_sales_enquiry(
                SalesEnquiryRequest(
                    "northstar-manchester",
                    EnquiryType.GENERAL,
                    self.customer,
                    "Please contact me.",
                ),
                idempotency_key="key-enquiry",
            )

        self.assertEqual(raised.exception.kind, DealerErrorKind.VALIDATION)
        self.assertEqual(raised.exception.field_violations[0].field, "customer.phone")


if __name__ == "__main__":
    unittest.main()
