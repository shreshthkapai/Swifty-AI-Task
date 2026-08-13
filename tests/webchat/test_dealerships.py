from dataclasses import FrozenInstanceError
from datetime import date, datetime, time, timezone
from decimal import Decimal
import unittest

from webchat.domain.common import Address, CustomerIdentity, Department, ReceivedStatus
from webchat.domain.dealerships import (
    BusinessInformation,
    ContactMethod,
    DealerLocation,
    DealershipMessage,
    DealershipMessageRequest,
    HolidayOpening,
    OpeningHours,
    OpeningPeriod,
)


def address() -> Address:
    return Address(("1 High Street",), "Manchester", "M1 1AA", "GB")


def customer() -> CustomerIdentity:
    return CustomerIdentity("Ada", "Lovelace", "ada@example.com", "07123456789")


class DealerLocationTestCase(unittest.TestCase):
    def test_location_preserves_decimal_coordinates_and_tuple_brands(self):
        location = DealerLocation(
            "dealer-1", "Northstar Manchester", address(), "01610000000",
            "dealer@example.com", Decimal("53.4808"), Decimal("-2.2426"), ("Aster", "Boreal"),
        )
        self.assertEqual(location.latitude, Decimal("53.4808"))
        with self.assertRaises(FrozenInstanceError):
            location.name = "changed"
        with self.assertRaises(ValueError):
            DealerLocation(
                "dealer-1", "Dealer", address(), "01610000000", "dealer@example.com",
                Decimal("91"), Decimal("0"), (),
            )

    def test_location_rejects_mutable_brands(self):
        with self.assertRaises(ValueError):
            DealerLocation(
                "dealer-1", "Dealer", address(), "01610000000", "dealer@example.com",
                Decimal("53"), Decimal("-2"), ["Aster"],  # type: ignore[arg-type]
            )


class OpeningHoursTestCase(unittest.TestCase):
    def test_closed_period_has_no_times(self):
        period = OpeningPeriod(Department.SALES, 0, None, None)
        self.assertTrue(period.is_closed)

    def test_open_period_requires_both_ordered_times(self):
        for opens_at, closes_at in ((time(9), None), (time(17), time(9))):
            with self.subTest(opens_at=opens_at, closes_at=closes_at), self.assertRaises(ValueError):
                OpeningPeriod(Department.SALES, 0, opens_at, closes_at)

    def test_hours_keep_department_specific_holiday_exception(self):
        regular = (OpeningPeriod(Department.SERVICE, 0, time(8), time(17)),)
        holidays = (HolidayOpening(date(2026, 12, 25), "Christmas Day", Department.SERVICE, None, None),)
        hours = OpeningHours("dealer-1", "Europe/London", regular, holidays)
        self.assertEqual(hours.holidays[0].department, Department.SERVICE)
        self.assertTrue(hours.holidays[0].is_closed)

    def test_hours_require_tuple_collections(self):
        with self.assertRaises(ValueError):
            OpeningHours("dealer-1", "Europe/London", [], ())  # type: ignore[arg-type]


class MessageTestCase(unittest.TestCase):
    def test_message_request_and_record_are_typed_and_aware(self):
        request = DealershipMessageRequest(
            "dealer-1", Department.GENERAL, "Website question", "Please contact me",
            customer(), ContactMethod.EMAIL,
        )
        record = DealershipMessage(
            "message-1", "MSG-1", request, ReceivedStatus.RECEIVED,
            datetime(2026, 8, 12, 9, tzinfo=timezone.utc),
        )
        self.assertEqual(record.request.preferred_contact_method, ContactMethod.EMAIL)
        with self.assertRaises(ValueError):
            DealershipMessage("message-1", "MSG-1", request, ReceivedStatus.RECEIVED, datetime(2026, 8, 12, 9))


class BusinessInformationTestCase(unittest.TestCase):
    def test_business_information_requires_iso_currency_and_non_negative_age(self):
        with self.assertRaises(ValueError):
            BusinessInformation("Northstar", "gbp", "UK", "Finance notice", 18, "PX notice", "privacy@example.com")
        with self.assertRaises(ValueError):
            BusinessInformation("Northstar", "GBP", "UK", "Finance notice", -1, "PX notice", "privacy@example.com")


if __name__ == "__main__":
    unittest.main()
