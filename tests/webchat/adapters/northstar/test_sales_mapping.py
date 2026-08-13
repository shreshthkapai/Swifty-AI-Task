import unittest
from datetime import date

from webchat.adapters.northstar.config import NorthstarConfig
from webchat.adapters.northstar.mapping.sales import (
    callback_from_payload,
    callback_to_payload,
    part_exchange_from_payload,
    part_exchange_to_payload,
    sales_enquiry_from_payload,
    sales_enquiry_to_payload,
    test_drive_booking_from_payload,
    test_drive_booking_to_payload,
    test_drive_slots_from_payload,
    test_drive_slot_search_to_params,
    vehicle_interest_from_payload,
    vehicle_interest_to_payload,
)
from webchat.domain import (
    CallbackRequest,
    CustomerIdentity,
    DealerErrorKind,
    Department,
    EnquiryType,
    Money,
    PartExchangeCondition,
    PartExchangeRequest,
    PartExchangeVehicle,
    SalesEnquiryRequest,
    TestDriveBookingRequest,
    TestDriveSlotSearch,
    VehicleInterestRequest,
)

from tests.webchat.adapters.northstar.support import (
    CALLBACK_PAYLOAD,
    PART_EXCHANGE_PAYLOAD,
    SALES_ENQUIRY_PAYLOAD,
    TEST_DRIVE_BOOKING_PAYLOAD,
    TEST_DRIVE_SLOT_PAYLOAD,
    VEHICLE_INTEREST_PAYLOAD,
)


class SalesMappingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.config = NorthstarConfig("http://northstar.test", "server-key")
        self.customer = CustomerIdentity(
            "Jamie",
            "Taylor",
            "jamie@example.com",
            "07700900123",
        )

    def test_sales_enquiry_translates_part_exchange_both_directions(self) -> None:
        request = SalesEnquiryRequest(
            dealership_id="northstar-manchester",
            vehicle_id="veh-003",
            enquiry_type=EnquiryType.PART_EXCHANGE,
            customer=self.customer,
            message="Please contact me about this vehicle.",
        )

        self.assertEqual(
            sales_enquiry_to_payload(request),
            {
                "dealershipId": "northstar-manchester",
                "vehicleId": "veh-003",
                "enquiryType": "part-exchange",
                "firstName": "Jamie",
                "lastName": "Taylor",
                "email": "jamie@example.com",
                "phone": "07700900123",
                "message": "Please contact me about this vehicle.",
            },
        )
        self.assertEqual(sales_enquiry_from_payload(SALES_ENQUIRY_PAYLOAD).request, request)

    def test_test_drive_slot_search_and_label_are_composed_deterministically(self) -> None:
        search = TestDriveSlotSearch(
            vehicle_id="veh-003",
            dealership_id="northstar-manchester",
            date_from=date(2026, 8, 13),
            date_to=date(2026, 8, 20),
        )

        self.assertEqual(
            test_drive_slot_search_to_params(search),
            {
                "vehicleId": "veh-003",
                "dealershipId": "northstar-manchester",
                "dateFrom": "2026-08-13",
                "dateTo": "2026-08-20",
            },
        )
        slots = test_drive_slots_from_payload({"items": [TEST_DRIVE_SLOT_PAYLOAD]})
        self.assertEqual(slots[0].vehicle_label, "BMW X3 xDrive20d M Sport")

    def test_test_drive_booking_maps_nullable_notes_and_record(self) -> None:
        request = TestDriveBookingRequest("td-slot-003-1", self.customer, "Morning preferred.")

        self.assertEqual(
            test_drive_booking_to_payload(request),
            {
                "slotId": "td-slot-003-1",
                "firstName": "Jamie",
                "lastName": "Taylor",
                "email": "jamie@example.com",
                "phone": "07700900123",
                "notes": "Morning preferred.",
            },
        )
        self.assertEqual(test_drive_booking_from_payload(TEST_DRIVE_BOOKING_PAYLOAD).request, request)

    def test_interest_callback_and_part_exchange_round_trip(self) -> None:
        interest = VehicleInterestRequest("veh-020", self.customer)
        callback = CallbackRequest(
            dealership_id="northstar-manchester",
            department=Department.SALES,
            customer=self.customer,
            reason="Discuss vehicle finance.",
            preferred_time="Tomorrow afternoon",
            vehicle_id="veh-003",
        )
        part_exchange = PartExchangeRequest(
            dealership_id="northstar-manchester",
            vehicle=PartExchangeVehicle("AB12 CDE", 50000, PartExchangeCondition.GOOD),
            customer=self.customer,
        )

        self.assertEqual(vehicle_interest_to_payload(interest)["vehicleId"], "veh-020")
        self.assertEqual(vehicle_interest_from_payload(VEHICLE_INTEREST_PAYLOAD).request, interest)
        self.assertEqual(callback_to_payload(callback)["preferredTime"], "Tomorrow afternoon")
        self.assertEqual(callback_from_payload(CALLBACK_PAYLOAD).request, callback)
        self.assertEqual(part_exchange_to_payload(part_exchange)["condition"], "good")
        valuation = part_exchange_from_payload(PART_EXCHANGE_PAYLOAD, self.config)
        self.assertEqual(valuation.request, part_exchange)
        self.assertEqual(valuation.estimate_low, Money(800000, "GBP"))
        self.assertEqual(valuation.estimate_high, Money(900000, "GBP"))
        self.assertEqual(valuation.qualification, "Online estimates are indicative.")

    def test_general_callback_is_a_local_invalid_request(self) -> None:
        request = CallbackRequest(
            dealership_id="northstar-manchester",
            department=Department.GENERAL,
            customer=self.customer,
            reason="Please contact me.",
        )

        with self.assertRaises(Exception) as raised:
            callback_to_payload(request)

        self.assertEqual(raised.exception.kind, DealerErrorKind.INVALID_REQUEST)

    def test_unknown_status_and_partial_payload_fail_closed(self) -> None:
        for payload in (
            {**TEST_DRIVE_BOOKING_PAYLOAD, "status": "pending"},
            {key: value for key, value in CALLBACK_PAYLOAD.items() if key != "reason"},
            {**PART_EXCHANGE_PAYLOAD, "estimateLowPence": None},
        ):
            with self.subTest(payload=payload), self.assertRaises(Exception) as raised:
                if "slotId" in payload:
                    test_drive_booking_from_payload(payload)
                elif "reason" not in payload:
                    callback_from_payload(payload)
                else:
                    part_exchange_from_payload(payload, self.config)
            self.assertEqual(raised.exception.kind, DealerErrorKind.INVALID_RESPONSE)


if __name__ == "__main__":
    unittest.main()
