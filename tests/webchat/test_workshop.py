from datetime import date, datetime, timezone
from decimal import Decimal
import unittest

from webchat.domain.common import Address, BookingStatus, CustomerIdentity, Money
from webchat.domain.dealerships import DealerLocation
from webchat.domain.workshop import (
    WorkshopBooking,
    WorkshopBookingAmendment,
    WorkshopBookingDetails,
    WorkshopBookingLookup,
    WorkshopBookingRequest,
    WorkshopCancellationRequest,
    WorkshopService,
    WorkshopSlot,
    WorkshopSlotSearch,
)


NOW = datetime(2026, 8, 12, 9, 0, tzinfo=timezone.utc)


def customer() -> CustomerIdentity:
    return CustomerIdentity("Ada", "Lovelace", "ada@example.com", "07123456789")


def dealer() -> DealerLocation:
    return DealerLocation(
        "dealer-1", "Manchester", Address(("1 High Street",), "Manchester", "M1 1AA", "GB"),
        "01610000000", "dealer@example.com", Decimal("53.48"), Decimal("-2.24"), ("Aster",),
    )


def booking(**overrides: object) -> WorkshopBooking:
    values: dict[str, object] = {
        "id": "booking-1",
        "reference": "WORK-1",
        "slot_id": "slot-1",
        "dealership_id": "dealer-1",
        "service_type_id": "service-1",
        "customer": customer(),
        "registration": "AB12 CDE",
        "mileage": 42_000,
        "notes": None,
        "status": BookingStatus.CONFIRMED,
        "created_at": NOW,
        "updated_at": NOW,
        "cancelled_at": None,
    }
    values.update(overrides)
    return WorkshopBooking(**values)  # type: ignore[arg-type]


class WorkshopCatalogueTestCase(unittest.TestCase):
    def test_service_and_slot_validate_duration_price_and_time(self):
        with self.assertRaises(ValueError):
            WorkshopService("service-1", "MOT", "Annual MOT", 0, Money(5_000, "GBP"))
        with self.assertRaises(ValueError):
            WorkshopSlot("slot-1", "dealer-1", "service-1", NOW.replace(tzinfo=None), "Dealer", "MOT", 60, None)

    def test_slot_search_rejects_reversed_dates(self):
        with self.assertRaises(ValueError):
            WorkshopSlotSearch(date_from=date(2026, 8, 13), date_to=date(2026, 8, 12))


class WorkshopRequestTestCase(unittest.TestCase):
    def test_booking_request_rejects_negative_mileage(self):
        with self.assertRaises(ValueError):
            WorkshopBookingRequest("slot-1", customer(), "AB12 CDE", -1)

    def test_lookup_preserves_all_verification_values(self):
        lookup = WorkshopBookingLookup(" work-1 ", " Lovelace ", " AB12 CDE ", " 07123456789 ")
        self.assertEqual(lookup.reference, "work-1")
        self.assertEqual(lookup.registration, "AB12 CDE")

    def test_amendment_requires_a_change_but_empty_notes_can_clear(self):
        with self.assertRaises(ValueError):
            WorkshopBookingAmendment("booking-1")
        amendment = WorkshopBookingAmendment("booking-1", notes="")
        self.assertEqual(amendment.notes, "")

    def test_cancellation_requires_booking_id(self):
        with self.assertRaises(ValueError):
            WorkshopCancellationRequest(" ")


class WorkshopBookingTestCase(unittest.TestCase):
    def test_booking_requires_aware_timestamps_and_consistent_cancellation(self):
        with self.assertRaises(ValueError):
            booking(updated_at=NOW.replace(tzinfo=None))
        with self.assertRaises(ValueError):
            booking(status=BookingStatus.CANCELLED, cancelled_at=None)
        cancelled = booking(status=BookingStatus.CANCELLED, cancelled_at=NOW)
        self.assertEqual(cancelled.status, BookingStatus.CANCELLED)

    def test_details_compose_complete_booking_and_dealer(self):
        details = WorkshopBookingDetails(booking(), NOW, "MOT", dealer())
        self.assertEqual(details.dealership.id, "dealer-1")
        self.assertEqual(details.service_type_name, "MOT")


if __name__ == "__main__":
    unittest.main()
