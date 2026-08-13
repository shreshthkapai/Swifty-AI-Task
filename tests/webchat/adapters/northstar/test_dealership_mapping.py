import unittest
from datetime import date, time
from decimal import Decimal

from webchat.adapters.northstar.config import NorthstarConfig
from webchat.adapters.northstar.mapping.dealerships import (
    business_information_from_payload,
    dealership_message_from_payload,
    dealership_message_to_payload,
    location_from_payload,
    locations_from_payload,
    opening_hours_from_payload,
)
from webchat.domain import (
    ContactMethod,
    CustomerIdentity,
    DealerErrorKind,
    DealershipMessageRequest,
    Department,
    ReceivedStatus,
)


LOCATION_PAYLOAD = {
    "id": "northstar-manchester",
    "name": "Northstar Manchester",
    "town": "Manchester",
    "postcode": "M20 2YY",
    "addressLine": "101 Kingsway",
    "phone": "0161 555 0101",
    "email": "manchester@northstarmotors.example",
    "latitude": 53.424,
    "longitude": -2.231,
    "brands": ["BMW", "MINI", "Volvo"],
}


class DealershipMappingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.config = NorthstarConfig(
            base_url="http://northstar.test",
            api_key="server-key",
        )

    def test_location_mapping_adds_configured_country_without_losing_precision(self) -> None:
        location = location_from_payload(LOCATION_PAYLOAD, self.config)

        self.assertEqual(location.id, "northstar-manchester")
        self.assertEqual(location.address.lines, ("101 Kingsway",))
        self.assertEqual(location.address.town, "Manchester")
        self.assertEqual(location.address.country, "United Kingdom")
        self.assertEqual(location.latitude, Decimal("53.424"))
        self.assertEqual(location.longitude, Decimal("-2.231"))
        self.assertEqual(location.brands, ("BMW", "MINI", "Volvo"))

    def test_location_list_requires_a_complete_items_array(self) -> None:
        locations = locations_from_payload({"items": [LOCATION_PAYLOAD]}, self.config)

        self.assertEqual(tuple(item.id for item in locations), ("northstar-manchester",))

    def test_opening_hours_add_timezone_and_filter_department(self) -> None:
        payload = {
            "dealershipId": "northstar-manchester",
            "weekly": [
                {
                    "department": "sales",
                    "day": "Monday",
                    "dayOfWeek": 0,
                    "opensAt": "09:00",
                    "closesAt": "18:00",
                    "closed": False,
                },
                {
                    "department": "service",
                    "day": "Sunday",
                    "dayOfWeek": 6,
                    "opensAt": None,
                    "closesAt": None,
                    "closed": True,
                },
            ],
            "holidayExceptions": [
                {
                    "department": "sales",
                    "date": "2026-08-31",
                    "label": "Bank holiday",
                    "opensAt": "10:00",
                    "closesAt": "16:00",
                    "closed": False,
                },
                {
                    "department": "service",
                    "date": "2026-08-31",
                    "label": "Bank holiday",
                    "opensAt": None,
                    "closesAt": None,
                    "closed": True,
                },
            ],
        }

        hours = opening_hours_from_payload(payload, self.config, Department.SALES)

        self.assertEqual(hours.timezone, "Europe/London")
        self.assertEqual(len(hours.regular), 1)
        self.assertEqual(hours.regular[0].opens_at, time(9, 0))
        self.assertEqual(hours.regular[0].closes_at, time(18, 0))
        self.assertEqual(len(hours.holidays), 1)
        self.assertEqual(hours.holidays[0].date, date(2026, 8, 31))
        self.assertEqual(hours.holidays[0].opens_at, time(10, 0))

    def test_message_payload_and_record_use_exact_northstar_shape(self) -> None:
        request = DealershipMessageRequest(
            dealership_id="northstar-manchester",
            department=Department.SALES,
            subject="Vehicle question",
            message="Please contact me about the X3.",
            customer=CustomerIdentity("Jamie", "Taylor", "jamie@example.com", "07700900123"),
            preferred_contact_method=ContactMethod.PHONE,
        )
        payload = dealership_message_to_payload(request)

        self.assertEqual(
            payload,
            {
                "dealershipId": "northstar-manchester",
                "department": "sales",
                "subject": "Vehicle question",
                "message": "Please contact me about the X3.",
                "firstName": "Jamie",
                "lastName": "Taylor",
                "email": "jamie@example.com",
                "phone": "07700900123",
                "preferredContactMethod": "phone",
            },
        )

        record = dealership_message_from_payload(
            {
                "id": "msg-1",
                "reference": "MSG-000001",
                "dealershipId": "northstar-manchester",
                "department": "sales",
                "subject": "Vehicle question",
                "message": "Please contact me about the X3.",
                "firstName": "Jamie",
                "lastName": "Taylor",
                "email": "jamie@example.com",
                "phone": "07700900123",
                "preferredContactMethod": "phone",
                "status": "received",
                "createdAt": "2026-08-12T10:30:00+00:00",
            }
        )
        self.assertEqual(record.id, "msg-1")
        self.assertEqual(record.request, request)
        self.assertIs(record.status, ReceivedStatus.RECEIVED)

    def test_business_information_maps_nested_legal_notices(self) -> None:
        information = business_information_from_payload(
            {
                "organisation": "Northstar Motors",
                "currency": "GBP",
                "market": "United Kingdom",
                "finance": {"notice": "Finance notice.", "minimumAge": 18},
                "partExchange": {"estimateNotice": "Estimate notice."},
                "privacyContact": "privacy@example.com",
            }
        )

        self.assertEqual(information.finance_notice, "Finance notice.")
        self.assertEqual(information.finance_minimum_age, 18)
        self.assertEqual(information.part_exchange_notice, "Estimate notice.")

    def test_missing_wrong_or_unknown_values_fail_as_invalid_response(self) -> None:
        malformed = (
            {**LOCATION_PAYLOAD, "brands": "BMW"},
            {**LOCATION_PAYLOAD, "latitude": True},
            {key: value for key, value in LOCATION_PAYLOAD.items() if key != "email"},
        )

        for payload in malformed:
            with self.subTest(payload=payload), self.assertRaises(Exception) as raised:
                location_from_payload(payload, self.config)
            self.assertEqual(raised.exception.kind, DealerErrorKind.INVALID_RESPONSE)


if __name__ == "__main__":
    unittest.main()
