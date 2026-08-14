import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from webchat.adapters.northstar.config import NorthstarConfig
from webchat.adapters.northstar.mapping.vehicles import (
    availability_from_payload,
    offer_from_payload,
    offer_search_to_params,
    offers_from_payload,
    vehicle_details_from_payload,
    vehicle_search_to_params,
    vehicles_from_payload,
)
from webchat.domain import (
    DealerErrorKind,
    Money,
    OfferSearch,
    VehicleAvailabilityStatus,
    VehicleSearch,
    VehicleSort,
)

from tests.webchat.adapters.northstar.support import OFFER_PAYLOAD, VEHICLE_PAYLOAD


class VehicleMappingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.config = NorthstarConfig(
            base_url="http://northstar.test",
            api_key="server-key",
        )

    def test_search_maps_every_filter_and_northstar_sort_name(self) -> None:
        params = vehicle_search_to_params(
            VehicleSearch(
                query="blue X3",
                make="BMW",
                model="X3",
                fuel_type="Diesel",
                transmission="Automatic",
                body_style="SUV",
                availability=VehicleAvailabilityStatus.AVAILABLE,
                dealership_id="northstar-manchester",
                min_price=Money(3000000, "GBP"),
                max_price=Money(5000000, "GBP"),
                max_mileage=25000,
                min_year=2022,
                sort=VehicleSort.PRICE_ASC,
                page=2,
                page_size=12,
            ),
            self.config,
        )

        self.assertEqual(
            params,
            {
                "q": "blue X3",
                "make": "BMW",
                "model": "X3",
                "fuelType": "Diesel",
                "transmission": "Automatic",
                "bodyStyle": "SUV",
                "availability": "available",
                "dealershipId": "northstar-manchester",
                "minPricePence": 3000000,
                "maxPricePence": 5000000,
                "maxMileage": 25000,
                "minYear": 2022,
                "sort": "priceAsc",
                "page": 2,
                "pageSize": 12,
            },
        )

    def test_non_gbp_search_is_rejected_as_invalid_request(self) -> None:
        with self.assertRaises(Exception) as raised:
            vehicle_search_to_params(
                VehicleSearch(min_price=Money(100, "USD")),
                self.config,
            )

        self.assertEqual(raised.exception.kind, DealerErrorKind.INVALID_REQUEST)

    def test_search_result_maps_page_vehicle_money_and_absolute_assets(self) -> None:
        result = vehicles_from_payload(
            {
                "items": [VEHICLE_PAYLOAD],
                "pagination": {
                    "page": 1,
                    "pageSize": 12,
                    "totalItems": 1,
                    "totalPages": 1,
                },
            },
            self.config,
        )

        vehicle = result.items[0]
        self.assertEqual(result.page_size, 12)
        self.assertEqual(vehicle.price, Money(4299500, "GBP"))
        self.assertEqual(vehicle.monthly_price, Money(64900, "GBP"))
        self.assertEqual(vehicle.images, ("http://northstar.test/assets/vehicles/veh-003.jpg",))
        self.assertEqual(vehicle.updated_at, datetime(2026, 8, 12, 10, 30, tzinfo=timezone.utc))

    def test_assets_use_browser_facing_origin_when_api_runs_inside_docker(self) -> None:
        config = NorthstarConfig(
            base_url="http://dealership-platform:4010",
            public_base_url="http://localhost:4010",
            api_key="server-key",
        )

        result = vehicles_from_payload(
            {
                "items": [VEHICLE_PAYLOAD],
                "pagination": {
                    "page": 1,
                    "pageSize": 12,
                    "totalItems": 1,
                    "totalPages": 1,
                },
            },
            config,
        )

        self.assertEqual(
            result.items[0].images,
            ("http://localhost:4010/assets/vehicles/veh-003.jpg",),
        )

    def test_null_vehicle_prices_remain_absent(self) -> None:
        payload = {**VEHICLE_PAYLOAD, "pricePence": None, "monthlyPricePence": None}
        result = vehicles_from_payload(
            {
                "items": [payload],
                "pagination": {"page": 1, "pageSize": 1, "totalItems": 1, "totalPages": 1},
            },
            self.config,
        )

        self.assertIsNone(result.items[0].price)
        self.assertIsNone(result.items[0].monthly_price)

    def test_vehicle_detail_requires_highlights(self) -> None:
        details = vehicle_details_from_payload(
            {**VEHICLE_PAYLOAD, "highlights": ["120-point inspection", "Six-month warranty"]},
            self.config,
        )

        self.assertEqual(details.vehicle.id, "veh-003")
        self.assertEqual(details.highlights, ("120-point inspection", "Six-month warranty"))

    def test_live_availability_maps_nullable_next_slot(self) -> None:
        availability = availability_from_payload(
            {
                "vehicleId": "veh-003",
                "availability": "available",
                "canEnquire": True,
                "canBookTestDrive": True,
                "canRegisterInterest": False,
                "nextTestDriveSlot": {
                    "id": "td-slot-003-1",
                    "startsAt": "2026-08-13T09:00:00+00:00",
                },
            },
            self.config,
        )

        self.assertIs(availability.status, VehicleAvailabilityStatus.AVAILABLE)
        self.assertEqual(availability.next_test_drive_slot.id, "td-slot-003-1")
        self.assertEqual(
            availability.next_test_drive_slot.starts_at.isoformat(),
            "2026-08-13T10:00:00+01:00",
        )

        reserved = availability_from_payload(
            {
                "vehicleId": "veh-020",
                "availability": "reserved",
                "canEnquire": True,
                "canBookTestDrive": False,
                "canRegisterInterest": True,
                "nextTestDriveSlot": None,
            },
            self.config,
        )
        self.assertIsNone(reserved.next_test_drive_slot)

    def test_offer_maps_decimal_date_money_and_asset(self) -> None:
        offer = offer_from_payload(OFFER_PAYLOAD, self.config)

        self.assertEqual(offer.apr_percent, Decimal("4.9"))
        self.assertEqual(offer.expires_on, date(2026, 12, 31))
        self.assertEqual(offer.monthly_price, Money(49900, "GBP"))
        self.assertEqual(offer.upfront_price, Money(299900, "GBP"))
        self.assertEqual(offer.image_url, "http://northstar.test/assets/vehicles/veh-004.jpg")

    def test_offer_list_and_query_preserve_empty_results(self) -> None:
        self.assertEqual(
            offer_search_to_params(OfferSearch(make="BMW", product_type="PCP")),
            {"make": "BMW", "productType": "PCP"},
        )
        self.assertEqual(offers_from_payload({"items": []}, self.config), ())

    def test_external_assets_and_malformed_enums_fail_closed(self) -> None:
        for payload in (
            {**VEHICLE_PAYLOAD, "images": ["https://attacker.test/vehicle.jpg"]},
            {**VEHICLE_PAYLOAD, "availability": "maybe"},
            {**VEHICLE_PAYLOAD, "year": True},
        ):
            with self.subTest(payload=payload), self.assertRaises(Exception) as raised:
                vehicles_from_payload(
                    {
                        "items": [payload],
                        "pagination": {"page": 1, "pageSize": 1, "totalItems": 1, "totalPages": 1},
                    },
                    self.config,
                )
            self.assertEqual(raised.exception.kind, DealerErrorKind.INVALID_RESPONSE)


if __name__ == "__main__":
    unittest.main()
