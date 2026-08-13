"""Dealer-independent asynchronous port implemented by dealership adapters."""

from typing import Protocol, runtime_checkable

from .common import Department, Page
from .dealerships import (
    BusinessInformation,
    DealerLocation,
    DealershipMessage,
    DealershipMessageRequest,
    OpeningHours,
)
from .sales import (
    Callback,
    CallbackRequest,
    PartExchangeRequest,
    PartExchangeValuation,
    SalesEnquiry,
    SalesEnquiryRequest,
    TestDriveBooking,
    TestDriveBookingRequest,
    TestDriveSlot,
    TestDriveSlotSearch,
    VehicleInterest,
    VehicleInterestRequest,
)
from .vehicles import (
    OfferSearch,
    Vehicle,
    VehicleAvailability,
    VehicleDetails,
    VehicleOffer,
    VehicleSearch,
)
from .workshop import (
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


@runtime_checkable
class DealerAdapter(Protocol):
    """Structural dealer port; methods raise structured ``DealerError`` failures."""

    async def search_vehicles(self, search: VehicleSearch) -> Page[Vehicle]:
        """Search inventory; raise ``DealerError`` on a stable dealer failure."""
        ...

    async def get_vehicle(self, vehicle_id: str) -> VehicleDetails:
        """Return vehicle details; raise ``DealerError`` on dealer failure."""
        ...

    async def get_vehicle_availability(self, vehicle_id: str) -> VehicleAvailability:
        """Return live status; raise ``DealerError`` on dealer failure."""
        ...

    async def list_offers(self, search: OfferSearch) -> tuple[VehicleOffer, ...]:
        """List offers; raise ``DealerError`` on dealer failure."""
        ...

    async def get_offer(self, offer_id: str) -> VehicleOffer:
        """Return one offer; raise ``DealerError`` on dealer failure."""
        ...

    async def create_sales_enquiry(
        self, request: SalesEnquiryRequest, *, idempotency_key: str
    ) -> SalesEnquiry:
        """Persist an enquiry idempotently; raise ``DealerError`` on failure."""
        ...

    async def list_test_drive_slots(self, search: TestDriveSlotSearch) -> tuple[TestDriveSlot, ...]:
        """List test-drive slots; raise ``DealerError`` on dealer failure."""
        ...

    async def book_test_drive(
        self, request: TestDriveBookingRequest, *, idempotency_key: str
    ) -> TestDriveBooking:
        """Book a test drive idempotently; raise ``DealerError`` on failure."""
        ...

    async def register_vehicle_interest(
        self, request: VehicleInterestRequest, *, idempotency_key: str
    ) -> VehicleInterest:
        """Register interest idempotently; raise ``DealerError`` on failure."""
        ...

    async def request_callback(
        self, request: CallbackRequest, *, idempotency_key: str
    ) -> Callback:
        """Persist a callback idempotently; raise ``DealerError`` on failure."""
        ...

    async def value_part_exchange(
        self, request: PartExchangeRequest, *, idempotency_key: str
    ) -> PartExchangeValuation:
        """Create a valuation idempotently; raise ``DealerError`` on failure."""
        ...

    async def list_service_types(self) -> tuple[WorkshopService, ...]:
        """List services; raise ``DealerError`` on dealer failure."""
        ...

    async def list_workshop_locations(self) -> tuple[DealerLocation, ...]:
        """List workshop locations; raise ``DealerError`` on dealer failure."""
        ...

    async def list_workshop_slots(self, search: WorkshopSlotSearch) -> tuple[WorkshopSlot, ...]:
        """List workshop slots; raise ``DealerError`` on dealer failure."""
        ...

    async def book_workshop(
        self, request: WorkshopBookingRequest, *, idempotency_key: str
    ) -> WorkshopBooking:
        """Book workshop idempotently; raise ``DealerError`` on failure."""
        ...

    async def lookup_workshop_booking(self, lookup: WorkshopBookingLookup) -> WorkshopBookingDetails:
        """Verify and return booking details; raise ``DealerError`` on failure."""
        ...

    async def get_workshop_booking(self, booking_id: str) -> WorkshopBooking:
        """Read an authorised booking; raise ``DealerError`` on failure."""
        ...

    async def amend_workshop_booking(self, amendment: WorkshopBookingAmendment) -> WorkshopBooking:
        """Amend an authorised booking; raise ``DealerError`` on failure."""
        ...

    async def cancel_workshop_booking(self, request: WorkshopCancellationRequest) -> WorkshopBooking:
        """Cancel repeat-safely; raise ``DealerError`` on dealer failure."""
        ...

    async def list_dealerships(self) -> tuple[DealerLocation, ...]:
        """List dealerships; raise ``DealerError`` on dealer failure."""
        ...

    async def get_dealership(self, dealership_id: str) -> DealerLocation:
        """Return one dealership; raise ``DealerError`` on dealer failure."""
        ...

    async def get_opening_hours(
        self, dealership_id: str, department: Department | None = None
    ) -> OpeningHours:
        """Return opening hours; raise ``DealerError`` on dealer failure."""
        ...

    async def send_dealership_message(
        self, request: DealershipMessageRequest, *, idempotency_key: str
    ) -> DealershipMessage:
        """Persist a message idempotently; raise ``DealerError`` on failure."""
        ...

    async def get_business_information(self) -> BusinessInformation:
        """Return business notices; raise ``DealerError`` on dealer failure."""
        ...
