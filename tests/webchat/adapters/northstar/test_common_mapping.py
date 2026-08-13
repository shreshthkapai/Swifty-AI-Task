import unittest
from datetime import datetime, timezone
from decimal import Decimal

from webchat.adapters.northstar.mapping.common import (
    read_datetime,
    read_decimal,
    read_int,
)
from webchat.domain import DealerErrorKind


class CommonMappingTestCase(unittest.TestCase):
    def test_utc_z_timestamp_becomes_an_aware_datetime(self) -> None:
        result = read_datetime({"createdAt": "2026-08-12T10:30:00Z"}, "createdAt")

        self.assertEqual(result, datetime(2026, 8, 12, 10, 30, tzinfo=timezone.utc))

    def test_decimal_is_built_from_textual_number_not_binary_float(self) -> None:
        result = read_decimal({"latitude": 53.424}, "latitude")

        self.assertEqual(result, Decimal("53.424"))

    def test_integer_reader_rejects_boolean(self) -> None:
        with self.assertRaises(Exception) as raised:
            read_int({"minimumAge": True}, "minimumAge")

        self.assertEqual(raised.exception.kind, DealerErrorKind.INVALID_RESPONSE)

    def test_naive_or_invalid_timestamp_fails_closed(self) -> None:
        for value in ("2026-08-12T10:30:00", "not-a-date", 7):
            with self.subTest(value=value), self.assertRaises(Exception) as raised:
                read_datetime({"createdAt": value}, "createdAt")
            self.assertEqual(raised.exception.kind, DealerErrorKind.INVALID_RESPONSE)


if __name__ == "__main__":
    unittest.main()
