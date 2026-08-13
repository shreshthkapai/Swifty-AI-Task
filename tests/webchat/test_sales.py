from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone
import unittest

from webchat.domain.common import BookingStatus, CustomerIdentity, Department, Money, ReceivedStatus
from webchat.domain.sales import (
    CallbackRequest,
    CallbackStatus,
    EnquiryType,
    InterestStatus,
    PartExchangeCondition,
    PartExchangeRequest,
    PartExchangeStatus,
    PartExchangeValuation,
    PartExchangeVehicle,
    SalesEnquiryRequest,
    TestDriveSlot,
    TestDriveSlotSearch,
    VehicleInterest,
    VehicleInterestRequest,
)


NOW = datetime(2026, 8, 12, 9, 0, tzinfo=timezone.utc)


def customer() -> CustomerIdentity:
    return CustomerIdentity("Ada", "Lovelace", "ada@example.com", "07123456789")


def part_exchange_request() -> PartExchangeRequest:
    return PartExchangeRequest(
        dealership_id="dealer-1",
        vehicle=PartExchangeVehicle("AB12 CDE", 42_000, PartExchangeCondition.GOOD),
        customer=customer(),
    )


class SalesRequestTestCase(unittest.TestCase):
    def test_sales_request_is_normalized_immutable_and_has_no_execution_key(self):
        request = SalesEnquiryRequest(
            dealership_id=" dealer-1 ",
            enquiry_type=EnquiryType.FINANCE,
            customer=customer(),
            message=" Finance options please ",
            vehicle_id=" vehicle-1 ",
        )
        self.assertEqual(request.dealership_id, "dealer-1")
        self.assertEqual(request.message, "Finance options please")
        self.assertNotIn("idempotency", request.__dataclass_fields__)
        with self.assertRaises(FrozenInstanceError):
            request.message = "changed"

    def test_required_business_ids_reject_blank_values(self):
        with self.assertRaises(ValueError):
            VehicleInterestRequest(" ", customer())
        with self.assertRaises(ValueError):
            CallbackRequest(" ", Department.SALES, customer(), "Discuss a vehicle")


class TestDriveSlotTestCase(unittest.TestCase):
    def test_slot_requires_aware_start_time(self):
        with self.assertRaises(ValueError):
            TestDriveSlot("slot-1", "dealer-1", "vehicle-1", datetime(2026, 8, 12, 10), "Dealer", "Car")

    def test_search_rejects_reversed_dates(self):
        with self.assertRaises(ValueError):
            TestDriveSlotSearch(date_from=date(2026, 8, 13), date_to=date(2026, 8, 12))


class InterestTestCase(unittest.TestCase):
    def test_interest_retains_dealership_and_aware_timestamp(self):
        request = VehicleInterestRequest("vehicle-1", customer(), "Keep me updated")
        interest = VehicleInterest(
            id="interest-1",
            reference="WAIT-1",
            request=request,
            dealership_id="dealer-1",
            status=InterestStatus.REGISTERED,
            created_at=NOW,
        )
        self.assertEqual(interest.dealership_id, "dealer-1")
        with self.assertRaises(ValueError):
            VehicleInterest(
                "interest-1",
                "WAIT-1",
                request,
                "dealer-1",
                InterestStatus.REGISTERED,
                NOW.replace(tzinfo=None),
            )


class PartExchangeTestCase(unittest.TestCase):
    def test_vehicle_rejects_negative_or_boolean_mileage(self):
        for mileage in (-1, True):
            with self.subTest(mileage=mileage), self.assertRaises(ValueError):
                PartExchangeVehicle("AB12 CDE", mileage, PartExchangeCondition.GOOD)

    def test_valuation_requires_ordered_matching_currency_range(self):
        base = {
            "id": "px-1",
            "reference": "PX-1",
            "request": part_exchange_request(),
            "status": PartExchangeStatus.ESTIMATED,
            "created_at": NOW,
            "qualification": "Indicative only",
        }
        for prices in (
            {"estimate_low": Money(10_000_00, "GBP"), "estimate_high": Money(9_000_00, "GBP")},
            {"estimate_low": Money(9_000_00, "GBP"), "estimate_high": Money(10_000_00, "EUR")},
        ):
            with self.subTest(prices=prices), self.assertRaises(ValueError):
                PartExchangeValuation(**base, **prices)


class StatusTestCase(unittest.TestCase):
    def test_status_values_are_stable(self):
        self.assertEqual(EnquiryType.PART_EXCHANGE, "part_exchange")
        self.assertEqual(ReceivedStatus.RECEIVED, "received")
        self.assertEqual(BookingStatus.CONFIRMED, "confirmed")
        self.assertEqual(InterestStatus.REGISTERED, "registered")
        self.assertEqual(CallbackStatus.REQUESTED, "requested")
        self.assertEqual(PartExchangeStatus.ESTIMATED, "estimated")


if __name__ == "__main__":
    unittest.main()
