import unittest
from datetime import date

from webchat.adapters.northstar.config import NorthstarConfig
from webchat.adapters.northstar.mapping.dealerships import location_from_payload
from webchat.adapters.northstar.mapping.workshop import (
    service_types_from_payload,
    workshop_amendment_to_payload,
    workshop_booking_details_from_payload,
    workshop_booking_from_payload,
    workshop_booking_lookup_to_payload,
    workshop_booking_to_payload,
    workshop_slot_search_to_params,
    workshop_slots_from_payload,
)
from webchat.domain import (
    BookingStatus,
    CustomerIdentity,
    DealerErrorKind,
    Money,
    WorkshopBookingAmendment,
    WorkshopBookingLookup,
    WorkshopBookingRequest,
    WorkshopSlotSearch,
)

from tests.webchat.adapters.northstar.support import (
    LOCATION_PAYLOAD,
    WORKSHOP_BOOKING_PAYLOAD,
    WORKSHOP_LOOKUP_PAYLOAD,
    WORKSHOP_SERVICE_PAYLOAD,
    WORKSHOP_SLOT_PAYLOAD,
)


class WorkshopMappingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.config = NorthstarConfig("http://northstar.test", "server-key")
        self.customer = CustomerIdentity(
            "Jamie",
            "Taylor",
            "jamie@example.com",
            "07700900123",
        )

    def test_service_and_slot_map_nullable_from_prices(self) -> None:
        services = service_types_from_payload(
            {"items": [WORKSHOP_SERVICE_PAYLOAD, {**WORKSHOP_SERVICE_PAYLOAD, "id": "free", "priceFromPence": None}]},
            self.config,
        )
        slots = workshop_slots_from_payload(
            {"items": [WORKSHOP_SLOT_PAYLOAD]},
            self.config,
        )

        self.assertEqual(services[0].price_from, Money(5499, "GBP"))
        self.assertIsNone(services[1].price_from)
        self.assertEqual(slots[0].price_from, Money(5499, "GBP"))
        self.assertEqual(slots[0].service_name, "MOT")

    def test_slot_search_uses_iso_dates_and_exact_keys(self) -> None:
        search = WorkshopSlotSearch(
            dealership_id="northstar-manchester",
            service_type_id="service-mot",
            date_from=date(2026, 8, 14),
            date_to=date(2026, 8, 20),
        )

        self.assertEqual(
            workshop_slot_search_to_params(search),
            {
                "dealershipId": "northstar-manchester",
                "serviceTypeId": "service-mot",
                "dateFrom": "2026-08-14",
                "dateTo": "2026-08-20",
            },
        )

    def test_booking_request_and_record_use_normalized_northstar_truth(self) -> None:
        request = WorkshopBookingRequest(
            "ws-slot-1",
            self.customer,
            "ab12 cde",
            50000,
            "Check the brakes.",
        )
        self.assertEqual(workshop_booking_to_payload(request)["registration"], "ab12 cde")

        record = workshop_booking_from_payload(WORKSHOP_BOOKING_PAYLOAD)
        self.assertEqual(record.registration, "AB12 CDE")
        self.assertIs(record.status, BookingStatus.CONFIRMED)
        self.assertIsNone(record.cancelled_at)

    def test_lookup_identity_and_details_compose_full_location(self) -> None:
        lookup = WorkshopBookingLookup(
            "WORK-000001",
            "Taylor",
            "AB12 CDE",
            "07700900123",
        )
        self.assertEqual(
            workshop_booking_lookup_to_payload(lookup),
            {
                "reference": "WORK-000001",
                "lastName": "Taylor",
                "registration": "AB12 CDE",
                "phone": "07700900123",
            },
        )
        location = location_from_payload(LOCATION_PAYLOAD, self.config)
        details = workshop_booking_details_from_payload(WORKSHOP_LOOKUP_PAYLOAD, location)
        self.assertEqual(details.booking.id, "wsb-1")
        self.assertEqual(details.service_type_name, "MOT")
        self.assertEqual(details.dealership, location)

    def test_amendment_distinguishes_omitted_notes_from_explicit_clear(self) -> None:
        mileage_only = WorkshopBookingAmendment("wsb-1", mileage=51000)
        clear_notes = WorkshopBookingAmendment("wsb-1", notes="")

        self.assertEqual(workshop_amendment_to_payload(mileage_only), {"mileage": 51000})
        self.assertEqual(workshop_amendment_to_payload(clear_notes), {"notes": ""})

    def test_cancelled_record_requires_cancelled_timestamp(self) -> None:
        cancelled = workshop_booking_from_payload(
            {
                **WORKSHOP_BOOKING_PAYLOAD,
                "status": "cancelled",
                "updatedAt": "2026-08-12T11:00:00+00:00",
                "cancelledAt": "2026-08-12T11:00:00+00:00",
            }
        )
        self.assertIs(cancelled.status, BookingStatus.CANCELLED)

        with self.assertRaises(Exception) as raised:
            workshop_booking_from_payload({**WORKSHOP_BOOKING_PAYLOAD, "status": "cancelled"})
        self.assertEqual(raised.exception.kind, DealerErrorKind.INVALID_RESPONSE)

    def test_non_available_slot_and_partial_lookup_fail_closed(self) -> None:
        with self.assertRaises(Exception) as slot_error:
            workshop_slots_from_payload(
                {"items": [{**WORKSHOP_SLOT_PAYLOAD, "status": "booked"}]},
                self.config,
            )
        self.assertEqual(slot_error.exception.kind, DealerErrorKind.INVALID_RESPONSE)

        location = location_from_payload(LOCATION_PAYLOAD, self.config)
        partial = {key: value for key, value in WORKSHOP_LOOKUP_PAYLOAD.items() if key != "startsAt"}
        with self.assertRaises(Exception) as lookup_error:
            workshop_booking_details_from_payload(partial, location)
        self.assertEqual(lookup_error.exception.kind, DealerErrorKind.INVALID_RESPONSE)


if __name__ == "__main__":
    unittest.main()
