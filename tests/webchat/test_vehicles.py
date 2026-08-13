from datetime import date, datetime, timezone
from decimal import Decimal
from dataclasses import FrozenInstanceError
import unittest

from webchat.domain.common import Money
from webchat.domain.vehicles import (
    AvailabilitySlot,
    OfferSearch,
    Vehicle,
    VehicleAvailability,
    VehicleAvailabilityStatus,
    VehicleDetails,
    VehicleOffer,
    VehicleSearch,
    VehicleSort,
)


def make_vehicle(**overrides: object) -> Vehicle:
    values: dict[str, object] = {
        "id": "vehicle-1",
        "dealership_id": "dealer-1",
        "dealership_name": "Northstar London",
        "dealership_town": "London",
        "make": "Aster",
        "model": "Nova",
        "variant": "Touring",
        "year": 2024,
        "price": Money(29_995_00, "GBP"),
        "monthly_price": Money(399_00, "GBP"),
        "mileage": 12_000,
        "fuel_type": "Petrol",
        "transmission": "Automatic",
        "colour": "Blue",
        "body_style": "SUV",
        "availability": VehicleAvailabilityStatus.AVAILABLE,
        "registration": "AB24 CDE",
        "description": "An excellent family car.",
        "images": ("/assets/vehicle.jpg",),
        "updated_at": datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return Vehicle(**values)  # type: ignore[arg-type]


class VehicleSearchTestCase(unittest.TestCase):
    def test_search_rejects_mixed_price_currencies(self):
        with self.assertRaises(ValueError):
            VehicleSearch(
                min_price=Money(1_000_00, "GBP"),
                max_price=Money(2_000_00, "EUR"),
            )

    def test_search_rejects_reversed_price_range(self):
        with self.assertRaises(ValueError):
            VehicleSearch(
                min_price=Money(20_000_00, "GBP"),
                max_price=Money(10_000_00, "GBP"),
            )

    def test_search_rejects_negative_numeric_filters_and_invalid_pagination(self):
        invalid_values = (
            {"max_mileage": -1},
            {"min_year": 0},
            {"page": 0},
            {"page_size": 0},
        )
        for values in invalid_values:
            with self.subTest(values=values), self.assertRaises(ValueError):
                VehicleSearch(**values)

    def test_search_exposes_stable_sort_values(self):
        self.assertEqual(VehicleSort.NEWEST, "newest")
        self.assertEqual(VehicleSort.PRICE_ASC, "price_asc")
        self.assertEqual(VehicleSort.PRICE_DESC, "price_desc")
        self.assertEqual(VehicleSort.MILEAGE_ASC, "mileage_asc")


class VehicleTestCase(unittest.TestCase):
    def test_vehicle_supports_price_on_request(self):
        vehicle = make_vehicle(price=None)
        self.assertIsNone(vehicle.price)
        self.assertEqual(vehicle.images, ("/assets/vehicle.jpg",))

    def test_vehicle_rejects_invalid_year_mileage_and_mixed_prices(self):
        invalid_values = (
            {"year": 0},
            {"mileage": -1},
            {"monthly_price": Money(399_00, "EUR")},
        )
        for values in invalid_values:
            with self.subTest(values=values), self.assertRaises(ValueError):
                make_vehicle(**values)

    def test_vehicle_requires_tuple_images_and_aware_updated_at(self):
        with self.assertRaises(ValueError):
            make_vehicle(images=["/assets/vehicle.jpg"])
        with self.assertRaises(ValueError):
            make_vehicle(updated_at=datetime(2026, 8, 11, 9, 30))

    def test_vehicle_strips_required_text_and_is_immutable(self):
        vehicle = make_vehicle(make="  Aster  ")
        self.assertEqual(vehicle.make, "Aster")
        with self.assertRaises(FrozenInstanceError):
            vehicle.year = 2025


class VehicleDetailsTestCase(unittest.TestCase):
    def test_details_requires_tuple_of_non_empty_highlights(self):
        with self.assertRaises(ValueError):
            VehicleDetails(make_vehicle(), ["Warranty included"])  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            VehicleDetails(make_vehicle(), (" ",))


class VehicleAvailabilityTestCase(unittest.TestCase):
    def test_availability_requires_boolean_permissions(self):
        slot = AvailabilitySlot(
            id="slot-1",
            starts_at=datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc),
        )
        availability = VehicleAvailability(
            vehicle_id="vehicle-1",
            status=VehicleAvailabilityStatus.AVAILABLE,
            can_enquire=True,
            can_book_test_drive=True,
            can_register_interest=False,
            next_test_drive_slot=slot,
        )
        self.assertTrue(availability.can_book_test_drive)

        with self.assertRaises(ValueError):
            VehicleAvailability(
                vehicle_id="vehicle-1",
                status=VehicleAvailabilityStatus.AVAILABLE,
                can_enquire="yes",  # type: ignore[arg-type]
                can_book_test_drive=False,
                can_register_interest=False,
                next_test_drive_slot=None,
            )

    def test_availability_slot_requires_an_aware_datetime(self):
        with self.assertRaises(ValueError):
            AvailabilitySlot("slot-1", datetime(2026, 8, 12, 10, 0))

    def test_availability_preserves_adapter_permissions(self):
        slot = AvailabilitySlot(
            id="slot-1",
            starts_at=datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc),
        )
        availability = VehicleAvailability(
            vehicle_id="vehicle-1",
            status=VehicleAvailabilityStatus.RESERVED,
            can_enquire=False,
            can_book_test_drive=True,
            can_register_interest=False,
            next_test_drive_slot=slot,
        )
        self.assertTrue(availability.can_book_test_drive)
        self.assertFalse(availability.can_register_interest)

    def test_availability_exposes_stable_status_values(self):
        self.assertEqual(VehicleAvailabilityStatus.AVAILABLE, "available")
        self.assertEqual(VehicleAvailabilityStatus.RESERVED, "reserved")
        self.assertEqual(VehicleAvailabilityStatus.SOLD, "sold")


class OfferTestCase(unittest.TestCase):
    def test_offer_rejects_invalid_numeric_values_and_mixed_currencies(self):
        values: dict[str, object] = {
            "id": "offer-1",
            "make": "Aster",
            "model": "Nova",
            "title": "Nova PCP offer",
            "product_type": "PCP",
            "monthly_price": Money(299_00, "GBP"),
            "upfront_price": Money(2_999_00, "GBP"),
            "apr_percent": Decimal("4.9"),
            "term_months": 48,
            "annual_mileage": 8_000,
            "expires_on": date(2026, 12, 31),
            "description": "Representative finance example.",
            "image_url": None,
        }
        for overrides in (
            {"term_months": 0},
            {"annual_mileage": -1},
            {"apr_percent": Decimal("-0.1")},
            {"upfront_price": Money(2_999_00, "EUR")},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                VehicleOffer(**(values | overrides))  # type: ignore[arg-type]

    def test_offer_and_search_normalize_required_and_optional_text(self):
        offer = VehicleOffer(
            "offer-1", " Aster ", "Nova", "Offer", "PCP", Money(299_00, "GBP"),
            Money(2_999_00, "GBP"), None, 48, 8_000, date(2026, 12, 31), "Details", " /assets/offer.jpg ",
        )
        self.assertEqual(offer.make, "Aster")
        self.assertEqual(offer.image_url, "/assets/offer.jpg")
        self.assertEqual(OfferSearch(make=" Aster ", product_type=" PCP ").make, "Aster")


if __name__ == "__main__":
    unittest.main()
