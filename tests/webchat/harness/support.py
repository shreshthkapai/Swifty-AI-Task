"""Shared dealer-domain fixtures for harness tests."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

from webchat.domain.common import Address, BookingStatus, CustomerIdentity, Money
from webchat.domain.dealer import DealerAdapter
from webchat.domain.dealerships import BusinessInformation, DealerLocation
from webchat.domain.sales import TestDriveBooking, TestDriveSlot
from webchat.domain.vehicles import (
    Vehicle,
    VehicleAvailability,
    VehicleAvailabilityStatus,
)
from webchat.domain.workshop import WorkshopBooking


NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
CUSTOMER = CustomerIdentity("Jamie", "Taylor", "jamie@example.com", "07700900123")


def location() -> DealerLocation:
    return DealerLocation(
        id="northstar-manchester",
        name="Northstar Manchester",
        address=Address(("101 Kingsway",), "Manchester", "M20 2YY", "UK"),
        phone="0161 555 0101",
        email="manchester@example.com",
        latitude=Decimal("53.424"),
        longitude=Decimal("-2.231"),
        brands=("BMW",),
    )


def vehicle(
    vehicle_id: str = "veh-003",
    *,
    status: VehicleAvailabilityStatus = VehicleAvailabilityStatus.AVAILABLE,
    price: Money | None = Money(4_299_500, "GBP"),
) -> Vehicle:
    return Vehicle(
        id=vehicle_id,
        dealership_id="northstar-manchester",
        dealership_name="Northstar Manchester",
        dealership_town="Manchester",
        make="BMW",
        model="X3",
        variant="xDrive20d M Sport",
        year=2024,
        price=price,
        monthly_price=None,
        mileage=8_000,
        fuel_type="Diesel",
        transmission="Automatic",
        colour="Blue",
        body_style="SUV",
        availability=status,
        registration="MA24 XYZ",
        description="Dealer description",
        images=("/x3.jpg",),
        updated_at=NOW,
    )


def availability(
    status: VehicleAvailabilityStatus = VehicleAvailabilityStatus.AVAILABLE,
) -> VehicleAvailability:
    return VehicleAvailability(
        vehicle_id="veh-003",
        status=status,
        can_enquire=True,
        can_book_test_drive=status is VehicleAvailabilityStatus.AVAILABLE,
        can_register_interest=status is VehicleAvailabilityStatus.RESERVED,
        next_test_drive_slot=None,
    )


def drive_slot(slot_id: str = "td-slot-1") -> TestDriveSlot:
    return TestDriveSlot(
        id=slot_id,
        dealership_id="northstar-manchester",
        vehicle_id="veh-003",
        starts_at=NOW + timedelta(days=1),
        dealership_name="Northstar Manchester",
        vehicle_label="BMW X3",
    )


def drive_booking() -> TestDriveBooking:
    from webchat.domain.sales import TestDriveBookingRequest

    return TestDriveBooking(
        id="tdb-1",
        reference="TEST-1",
        request=TestDriveBookingRequest("td-slot-1", CUSTOMER),
        dealership_id="northstar-manchester",
        vehicle_id="veh-003",
        status=BookingStatus.CONFIRMED,
        created_at=NOW,
    )


def workshop_booking(
    status: BookingStatus = BookingStatus.CONFIRMED,
) -> WorkshopBooking:
    return WorkshopBooking(
        id="wsb-1",
        reference="WORK-1",
        slot_id="ws-slot-1",
        dealership_id="northstar-manchester",
        service_type_id="service-mot",
        customer=CUSTOMER,
        registration="AB12 CDE",
        mileage=50_000,
        notes=None,
        status=status,
        created_at=NOW,
        updated_at=NOW,
        cancelled_at=NOW if status is BookingStatus.CANCELLED else None,
    )


def dealer() -> Mock:
    fake = Mock(spec=DealerAdapter)
    fake.list_dealerships.return_value = (location(),)
    fake.get_business_information.return_value = BusinessInformation(
        "Northstar Motors",
        "GBP",
        "United Kingdom",
        "Finance is subject to status and terms.",
        18,
        "Part-exchange valuations are indicative.",
        "privacy@northstar.example",
    )
    return fake
