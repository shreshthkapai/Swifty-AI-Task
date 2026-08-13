from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import unittest

from webchat.domain.common import (
    Address,
    BookingStatus,
    CustomerIdentity,
    Department,
    Money,
    Page,
    ReceivedStatus,
    require_aware,
    require_non_empty,
)


class MoneyTestCase(unittest.TestCase):
    def test_money_requires_minor_units_and_iso_code(self):
        self.assertEqual(Money(29_995_00, "GBP").amount_minor, 29_995_00)
        with self.assertRaises(ValueError):
            Money(-1, "GBP")
        with self.assertRaises(ValueError):
            Money(100, "gbp")

    def test_money_rejects_boolean_minor_units(self):
        with self.assertRaises(ValueError):
            Money(True, "GBP")

    def test_money_is_immutable(self):
        money = Money(100, "GBP")
        with self.assertRaises(FrozenInstanceError):
            money.amount_minor = 200


class ValidationTestCase(unittest.TestCase):
    def test_require_non_empty_strips_and_rejects_blank_values(self):
        self.assertEqual(require_non_empty("  Ada  ", "first_name"), "Ada")
        with self.assertRaises(ValueError):
            require_non_empty("   ", "first_name")

    def test_require_aware_rejects_naive_datetimes(self):
        with self.assertRaises(ValueError):
            require_aware(datetime(2026, 8, 11, 9, 30), "created_at")
        self.assertEqual(
            require_aware(datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc), "created_at"),
            datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc),
        )


class CustomerIdentityTestCase(unittest.TestCase):
    def test_customer_identity_rejects_empty_fields(self):
        for field in ("first_name", "last_name", "email", "phone"):
            values = {
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ada@example.test",
                "phone": "01234 567890",
            }
            values[field] = " "
            with self.subTest(field=field), self.assertRaises(ValueError):
                CustomerIdentity(**values)


class AddressTestCase(unittest.TestCase):
    def test_address_rejects_empty_lines(self):
        with self.assertRaises(ValueError):
            Address((), "London", "SW1A 1AA", "GB")
        with self.assertRaises(ValueError):
            Address(("10 Example Street", " "), "London", "SW1A 1AA", "GB")


class PageTestCase(unittest.TestCase):
    def test_page_rejects_mutable_items(self):
        with self.assertRaises(ValueError):
            Page(items=["booking-1"], page=1, page_size=10, total_items=1, total_pages=1)

    def test_page_rejects_inconsistent_metadata(self):
        with self.assertRaises(ValueError):
            Page(items=(), page=1, page_size=0, total_items=0, total_pages=0)

    def test_page_requires_positive_pagination_and_non_negative_totals(self):
        invalid_values = (
            {"page": 0, "page_size": 10, "total_items": 0, "total_pages": 0},
            {"page": 1, "page_size": 10, "total_items": -1, "total_pages": 0},
            {"page": 1, "page_size": 10, "total_items": 0, "total_pages": -1},
        )
        for values in invalid_values:
            with self.subTest(values=values), self.assertRaises(ValueError):
                Page(items=(), **values)


class EnumTestCase(unittest.TestCase):
    def test_domain_enums_expose_contract_values(self):
        self.assertEqual(Department.SALES, "sales")
        self.assertEqual(BookingStatus.CONFIRMED, "confirmed")
        self.assertEqual(ReceivedStatus.RECEIVED, "received")
