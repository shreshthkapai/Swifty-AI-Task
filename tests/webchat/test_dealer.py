from inspect import Parameter, iscoroutinefunction, signature
import unittest

from webchat.domain.dealer import DealerAdapter


METHODS = {
    "search_vehicles", "get_vehicle", "get_vehicle_availability", "list_offers", "get_offer",
    "create_sales_enquiry", "list_test_drive_slots", "book_test_drive",
    "register_vehicle_interest", "request_callback", "value_part_exchange",
    "list_service_types", "list_workshop_locations", "list_workshop_slots", "book_workshop",
    "lookup_workshop_booking", "get_workshop_booking", "amend_workshop_booking",
    "cancel_workshop_booking", "list_dealerships", "get_dealership", "get_opening_hours",
    "send_dealership_message", "get_business_information",
}

CREATE_METHODS = {
    "create_sales_enquiry", "book_test_drive", "register_vehicle_interest", "request_callback",
    "value_part_exchange", "book_workshop", "send_dealership_message",
}


class FakeDealer:
    async def search_vehicles(self, search): pass
    async def get_vehicle(self, vehicle_id): pass
    async def get_vehicle_availability(self, vehicle_id): pass
    async def list_offers(self, search): pass
    async def get_offer(self, offer_id): pass
    async def create_sales_enquiry(self, request, *, idempotency_key): pass
    async def list_test_drive_slots(self, search): pass
    async def book_test_drive(self, request, *, idempotency_key): pass
    async def register_vehicle_interest(self, request, *, idempotency_key): pass
    async def request_callback(self, request, *, idempotency_key): pass
    async def value_part_exchange(self, request, *, idempotency_key): pass
    async def list_service_types(self): pass
    async def list_workshop_locations(self): pass
    async def list_workshop_slots(self, search): pass
    async def book_workshop(self, request, *, idempotency_key): pass
    async def lookup_workshop_booking(self, lookup): pass
    async def get_workshop_booking(self, booking_id): pass
    async def amend_workshop_booking(self, amendment): pass
    async def cancel_workshop_booking(self, request): pass
    async def list_dealerships(self): pass
    async def get_dealership(self, dealership_id): pass
    async def get_opening_hours(self, dealership_id, department=None): pass
    async def send_dealership_message(self, request, *, idempotency_key): pass
    async def get_business_information(self): pass


class DealerAdapterTestCase(unittest.TestCase):
    def test_protocol_exposes_exact_async_capabilities(self):
        public_methods = {
            name for name, value in DealerAdapter.__dict__.items()
            if not name.startswith("_") and callable(value)
        }
        self.assertEqual(public_methods, METHODS)
        for name in METHODS:
            with self.subTest(method=name):
                self.assertTrue(iscoroutinefunction(getattr(DealerAdapter, name)))

    def test_create_methods_require_keyword_only_idempotency_key(self):
        for name in CREATE_METHODS:
            with self.subTest(method=name):
                parameter = signature(getattr(DealerAdapter, name)).parameters["idempotency_key"]
                self.assertIs(parameter.kind, Parameter.KEYWORD_ONLY)
                self.assertIs(parameter.default, Parameter.empty)

    def test_complete_fake_satisfies_structural_protocol(self):
        self.assertIsInstance(FakeDealer(), DealerAdapter)


if __name__ == "__main__":
    unittest.main()
