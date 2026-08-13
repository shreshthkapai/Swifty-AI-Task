import unittest
from collections.abc import Callable

import httpx

from webchat.adapters.northstar.adapter import NorthstarAdapter
from webchat.adapters.northstar.client import NorthstarClient
from webchat.adapters.northstar.config import NorthstarConfig
from webchat.domain import (
    DealerErrorKind,
    Money,
    OfferSearch,
    VehicleAvailabilityStatus,
    VehicleSearch,
    VehicleSort,
)

from tests.webchat.adapters.northstar.support import OFFER_PAYLOAD, VEHICLE_PAYLOAD


class NorthstarVehicleAdapterTestCase(unittest.IsolatedAsyncioTestCase):
    def make_adapter(
        self,
        handler: Callable[[httpx.Request], httpx.Response],
    ) -> NorthstarAdapter:
        config = NorthstarConfig(
            base_url="http://northstar.test",
            api_key="server-secret",
        )
        http = httpx.AsyncClient(
            base_url=config.base_url,
            transport=httpx.MockTransport(handler),
        )
        adapter = NorthstarAdapter(NorthstarClient(config, http))
        self.addAsyncCleanup(adapter.aclose)
        return adapter

    async def test_search_vehicles_sends_exact_public_query(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "items": [VEHICLE_PAYLOAD],
                    "pagination": {"page": 2, "pageSize": 5, "totalItems": 6, "totalPages": 2},
                },
            )

        adapter = self.make_adapter(handler)
        result = await adapter.search_vehicles(
            VehicleSearch(
                make="BMW",
                availability=VehicleAvailabilityStatus.AVAILABLE,
                max_price=Money(5000000, "GBP"),
                sort=VehicleSort.PRICE_DESC,
                page=2,
                page_size=5,
            )
        )

        request = requests[0]
        self.assertEqual(result.items[0].id, "veh-003")
        self.assertEqual(request.url.path, "/api/vehicles")
        self.assertEqual(
            dict(request.url.params),
            {
                "make": "BMW",
                "availability": "available",
                "maxPricePence": "5000000",
                "sort": "priceDesc",
                "page": "2",
                "pageSize": "5",
            },
        )
        self.assertNotIn("X-API-Key", request.headers)

    async def test_non_gbp_search_never_reaches_transport(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={})

        adapter = self.make_adapter(handler)

        with self.assertRaises(Exception) as raised:
            await adapter.search_vehicles(VehicleSearch(min_price=Money(100, "USD")))

        self.assertEqual(raised.exception.kind, DealerErrorKind.INVALID_REQUEST)
        self.assertEqual(calls, 0)

    async def test_get_vehicle_uses_detail_path(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={**VEHICLE_PAYLOAD, "highlights": ["Warranty"]})

        adapter = self.make_adapter(handler)
        result = await adapter.get_vehicle("veh-003")

        self.assertEqual(result.highlights, ("Warranty",))
        self.assertEqual(requests[0].url.path, "/api/vehicles/veh-003")

    async def test_get_vehicle_availability_uses_live_endpoint(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "vehicleId": "veh-003",
                    "availability": "available",
                    "canEnquire": True,
                    "canBookTestDrive": True,
                    "canRegisterInterest": False,
                    "nextTestDriveSlot": {
                        "id": "td-slot-1",
                        "startsAt": "2026-08-13T09:00:00+00:00",
                    },
                },
            )

        adapter = self.make_adapter(handler)
        result = await adapter.get_vehicle_availability("veh-003")

        self.assertTrue(result.can_book_test_drive)
        self.assertEqual(requests[0].url.path, "/api/vehicles/veh-003/availability")

    async def test_list_offers_sends_exact_filters(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"items": [OFFER_PAYLOAD]})

        adapter = self.make_adapter(handler)
        result = await adapter.list_offers(OfferSearch(make="BMW", product_type="PCP"))

        self.assertEqual(result[0].id, "offer-001")
        self.assertEqual(requests[0].url.path, "/api/offers")
        self.assertEqual(dict(requests[0].url.params), {"make": "BMW", "productType": "PCP"})

    async def test_get_offer_uses_offer_path(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=OFFER_PAYLOAD)

        adapter = self.make_adapter(handler)
        result = await adapter.get_offer("offer-001")

        self.assertEqual(result.id, "offer-001")
        self.assertEqual(requests[0].url.path, "/api/offers/offer-001")


if __name__ == "__main__":
    unittest.main()
