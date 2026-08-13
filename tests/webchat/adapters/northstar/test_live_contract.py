from __future__ import annotations

from datetime import date, timedelta
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import httpx

from webchat.adapters.northstar import NorthstarAdapter, NorthstarClient, NorthstarConfig
from webchat.domain import (
    BookingStatus,
    CallbackRequest,
    ContactMethod,
    CustomerIdentity,
    DealerErrorKind,
    DealershipMessageRequest,
    Department,
    EnquiryType,
    OfferSearch,
    PartExchangeCondition,
    PartExchangeRequest,
    PartExchangeVehicle,
    SalesEnquiryRequest,
    TestDriveBookingRequest,
    TestDriveSlotSearch,
    VehicleInterestRequest,
    VehicleSearch,
    WorkshopBookingAmendment,
    WorkshopBookingLookup,
    WorkshopBookingRequest,
    WorkshopCancellationRequest,
    WorkshopSlotSearch,
)


ROOT = Path(__file__).resolve().parents[4]
PLATFORM_ROOT = ROOT / "dealership-platform"
API_KEY = "northstar-local-development"
ADMIN_KEY = "northstar-local-admin"


class LiveNorthstarContractTestCase(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.port = cls._available_port()
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        environment = {
            **os.environ,
            "NORTHSTAR_API_KEY": API_KEY,
            "NORTHSTAR_ADMIN_KEY": ADMIN_KEY,
            "NORTHSTAR_DATA_PATH": str(
                Path(cls.temporary_directory.name) / "adapter-contract.sqlite3"
            ),
            "NORTHSTAR_PORT": str(cls.port),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        cls.server = subprocess.Popen(
            [sys.executable, "-m", "src.server"],
            cwd=PLATFORM_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        cls._wait_until_ready()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.server.poll() is None:
            cls.server.terminate()
            try:
                cls.server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                cls.server.kill()
                cls.server.wait(timeout=3)
        if cls.server.stdout:
            cls.server.stdout.close()
        cls.temporary_directory.cleanup()

    async def asyncSetUp(self) -> None:
        async with httpx.AsyncClient(base_url=self.base_url) as http:
            response = await http.post(
                "/admin/api/reset",
                headers={"X-Admin-Key": ADMIN_KEY},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.customer = CustomerIdentity(
            "Jamie",
            "Taylor",
            "jamie@example.com",
            "07700900123",
        )

    @staticmethod
    def _available_port() -> int:
        with socket.socket() as server_socket:
            server_socket.bind(("127.0.0.1", 0))
            return int(server_socket.getsockname()[1])

    @classmethod
    def _wait_until_ready(cls) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if cls.server.poll() is not None:
                output = cls.server.stdout.read() if cls.server.stdout else ""
                raise RuntimeError(f"HTTP test server stopped during startup:\n{output}")
            try:
                with urlopen(f"{cls.base_url}/health", timeout=1) as response:
                    if response.status == 200:
                        return
            except (HTTPError, URLError):
                time.sleep(0.05)
        raise RuntimeError("HTTP test server did not become ready")

    def make_adapter(self, api_key: str = API_KEY) -> NorthstarAdapter:
        config = NorthstarConfig(self.base_url, api_key)
        http = httpx.AsyncClient(base_url=config.base_url)
        adapter = NorthstarAdapter(NorthstarClient(config, http))
        self.addAsyncCleanup(adapter.aclose)
        return adapter

    async def test_all_24_operations_match_the_live_platform(self) -> None:
        adapter = self.make_adapter()

        vehicles = await adapter.search_vehicles(VehicleSearch(make="BMW", page_size=5))
        self.assertGreater(len(vehicles.items), 0)
        vehicle = await adapter.get_vehicle("veh-001")
        availability = await adapter.get_vehicle_availability("veh-001")
        self.assertEqual(vehicle.vehicle.id, availability.vehicle_id)

        offers = await adapter.list_offers(OfferSearch())
        offer = await adapter.get_offer("offer-01")
        self.assertIn(offer, offers)

        enquiry = await adapter.create_sales_enquiry(
            SalesEnquiryRequest(
                "northstar-manchester",
                EnquiryType.AVAILABILITY,
                self.customer,
                "Please confirm current vehicle availability.",
                "veh-001",
            ),
            idempotency_key="live-enquiry",
        )
        self.assertTrue(enquiry.reference.startswith("SALE-"))

        test_drive_slots = await adapter.list_test_drive_slots(
            TestDriveSlotSearch(vehicle_id="veh-001")
        )
        self.assertGreater(len(test_drive_slots), 0)
        test_drive = await adapter.book_test_drive(
            TestDriveBookingRequest(test_drive_slots[0].id, self.customer),
            idempotency_key="live-test-drive",
        )
        self.assertIs(test_drive.status, BookingStatus.CONFIRMED)

        interest = await adapter.register_vehicle_interest(
            VehicleInterestRequest("veh-007", self.customer),
            idempotency_key="live-interest",
        )
        self.assertTrue(interest.reference.startswith("WAIT-"))

        callback = await adapter.request_callback(
            CallbackRequest(
                "northstar-manchester",
                Department.SALES,
                self.customer,
                "Discuss the available BMW.",
                vehicle_id="veh-001",
            ),
            idempotency_key="live-callback",
        )
        self.assertTrue(callback.reference.startswith("CALL-"))

        valuation = await adapter.value_part_exchange(
            PartExchangeRequest(
                "northstar-manchester",
                PartExchangeVehicle("AB12 CDE", 50000, PartExchangeCondition.GOOD),
                self.customer,
            ),
            idempotency_key="live-part-exchange",
        )
        self.assertLess(valuation.estimate_low.amount_minor, valuation.estimate_high.amount_minor)

        services = await adapter.list_service_types()
        workshop_locations = await adapter.list_workshop_locations()
        self.assertEqual(len(workshop_locations), 4)
        workshop_slots = await adapter.list_workshop_slots(
            WorkshopSlotSearch(
                dealership_id="northstar-manchester",
                service_type_id="mot",
            )
        )
        self.assertGreaterEqual(len(workshop_slots), 2)

        workshop = await adapter.book_workshop(
            WorkshopBookingRequest(
                workshop_slots[0].id,
                self.customer,
                "AB12 CDE",
                50000,
                "Check the brakes.",
            ),
            idempotency_key="live-workshop",
        )
        details = await adapter.lookup_workshop_booking(
            WorkshopBookingLookup(
                workshop.reference,
                self.customer.last_name,
                workshop.registration,
                self.customer.phone,
            )
        )
        self.assertEqual(details.booking.id, workshop.id)
        raw_booking = await adapter.get_workshop_booking(workshop.id)
        self.assertEqual(raw_booking.id, workshop.id)

        amended = await adapter.amend_workshop_booking(
            WorkshopBookingAmendment(
                workshop.id,
                slot_id=workshop_slots[1].id,
                mileage=51000,
                notes="",
            )
        )
        self.assertEqual(amended.slot_id, workshop_slots[1].id)
        self.assertEqual(amended.mileage, 51000)
        self.assertIsNone(amended.notes)
        cancelled = await adapter.cancel_workshop_booking(
            WorkshopCancellationRequest(workshop.id)
        )
        self.assertIs(cancelled.status, BookingStatus.CANCELLED)

        dealerships = await adapter.list_dealerships()
        dealership = await adapter.get_dealership("northstar-manchester")
        hours = await adapter.get_opening_hours(
            dealership.id,
            Department.SALES,
        )
        self.assertEqual(len(dealerships), 4)
        self.assertGreater(len(hours.regular), 0)

        message = await adapter.send_dealership_message(
            DealershipMessageRequest(
                dealership.id,
                Department.SALES,
                "Vehicle enquiry",
                "Please contact me about the available BMW.",
                self.customer,
                ContactMethod.EMAIL,
            ),
            idempotency_key="live-message",
        )
        information = await adapter.get_business_information()
        self.assertTrue(message.reference.startswith("MSG-"))
        self.assertEqual(information.currency, "GBP")
        self.assertTrue(any(service.id == "mot" for service in services))

    async def test_live_seeded_failures_map_to_recovery_kinds(self) -> None:
        adapter = self.make_adapter()

        cases = (
            ("veh-007", DealerErrorKind.VEHICLE_RESERVED),
            ("veh-013", DealerErrorKind.VEHICLE_UNAVAILABLE),
        )
        for vehicle_id, expected_kind in cases:
            slots = await adapter.list_test_drive_slots(
                TestDriveSlotSearch(vehicle_id=vehicle_id)
            )
            self.assertGreater(len(slots), 0)
            with self.subTest(vehicle_id=vehicle_id), self.assertRaises(Exception) as raised:
                await adapter.book_test_drive(
                    TestDriveBookingRequest(slots[0].id, self.customer),
                    idempotency_key=f"failure-{vehicle_id}",
                )
            self.assertEqual(raised.exception.kind, expected_kind)

        with self.assertRaises(Exception) as interest_error:
            await adapter.register_vehicle_interest(
                VehicleInterestRequest("veh-001", self.customer),
                idempotency_key="interest-not-reserved",
            )
        self.assertEqual(interest_error.exception.kind, DealerErrorKind.VEHICLE_NOT_RESERVED)

        with self.assertRaises(Exception) as lookup_error:
            await adapter.lookup_workshop_booking(
                WorkshopBookingLookup(
                    "WORK-10001",
                    "WrongSurname",
                    "AB12 CDE",
                    "07700900123",
                )
            )
        self.assertEqual(lookup_error.exception.kind, DealerErrorKind.VERIFICATION_FAILED)

    async def test_live_stale_slots_idempotency_and_cancelled_amendment(self) -> None:
        adapter = self.make_adapter()
        slots = await adapter.list_workshop_slots(
            WorkshopSlotSearch(
                dealership_id="northstar-manchester",
                service_type_id="mot",
            )
        )
        request = WorkshopBookingRequest(
            slots[0].id,
            self.customer,
            "AB12 CDE",
            50000,
        )
        first = await adapter.book_workshop(request, idempotency_key="same-workshop-key")
        replay = await adapter.book_workshop(request, idempotency_key="same-workshop-key")
        self.assertEqual(first.id, replay.id)

        with self.assertRaises(Exception) as stale:
            await adapter.book_workshop(request, idempotency_key="different-workshop-key")
        self.assertEqual(stale.exception.kind, DealerErrorKind.SLOT_UNAVAILABLE)

        changed = WorkshopBookingRequest(
            slots[1].id,
            self.customer,
            "AB12 CDE",
            50000,
        )
        with self.assertRaises(Exception) as conflict:
            await adapter.book_workshop(changed, idempotency_key="same-workshop-key")
        self.assertEqual(conflict.exception.kind, DealerErrorKind.IDEMPOTENCY_CONFLICT)

        await adapter.cancel_workshop_booking(WorkshopCancellationRequest(first.id))
        repeated = await adapter.cancel_workshop_booking(WorkshopCancellationRequest(first.id))
        self.assertIs(repeated.status, BookingStatus.CANCELLED)
        with self.assertRaises(Exception) as cancelled:
            await adapter.amend_workshop_booking(
                WorkshopBookingAmendment(first.id, mileage=51000)
            )
        self.assertEqual(cancelled.exception.kind, DealerErrorKind.BOOKING_CANCELLED)

    async def test_live_validation_authentication_and_empty_availability(self) -> None:
        adapter = self.make_adapter()
        invalid_customer = CustomerIdentity(
            "Jamie",
            "Taylor",
            "jamie@example.com",
            "not-a-phone",
        )
        with self.assertRaises(Exception) as validation:
            await adapter.create_sales_enquiry(
                SalesEnquiryRequest(
                    "northstar-manchester",
                    EnquiryType.GENERAL,
                    invalid_customer,
                    "Please contact me.",
                ),
                idempotency_key="invalid-customer",
            )
        self.assertEqual(validation.exception.kind, DealerErrorKind.VALIDATION)
        self.assertEqual(validation.exception.field_violations[0].field, "customer.phone")

        unauthorised = self.make_adapter("incorrect-key")
        with self.assertRaises(Exception) as auth:
            await unauthorised.request_callback(
                CallbackRequest(
                    "northstar-manchester",
                    Department.SALES,
                    self.customer,
                    "Please contact me.",
                ),
                idempotency_key="wrong-auth",
            )
        self.assertEqual(auth.exception.kind, DealerErrorKind.AUTHENTICATION_FAILED)

        today = date.today()
        empty = await adapter.list_workshop_slots(
            WorkshopSlotSearch(
                dealership_id="northstar-bolton",
                date_from=today,
                date_to=today + timedelta(days=6),
            )
        )
        self.assertEqual(empty, ())


if __name__ == "__main__":
    unittest.main()
