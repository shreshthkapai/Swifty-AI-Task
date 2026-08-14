"""Dealer-port implementation backed by the Northstar REST platform."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
import time
from typing import Any, TypeVar
from urllib.parse import quote

from webchat.domain import (
    BusinessInformation,
    Callback,
    CallbackRequest,
    DealerError,
    DealerErrorKind,
    DealerLocation,
    DealershipMessage,
    DealershipMessageRequest,
    Department,
    OpeningHours,
    OfferSearch,
    Page,
    PartExchangeRequest,
    PartExchangeValuation,
    SalesEnquiry,
    SalesEnquiryRequest,
    TestDriveBooking,
    TestDriveBookingRequest,
    TestDriveSlot,
    TestDriveSlotSearch,
    Vehicle,
    VehicleAvailability,
    VehicleDetails,
    VehicleOffer,
    VehicleSearch,
    VehicleInterest,
    VehicleInterestRequest,
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

from .client import NorthstarClient
from .errors import invalid_request
from .mapping.dealerships import (
    business_information_from_payload,
    dealership_message_from_payload,
    dealership_message_to_payload,
    location_from_payload,
    locations_from_payload,
    opening_hours_from_payload,
)
from .mapping.sales import (
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
from .mapping.vehicles import (
    availability_from_payload,
    offer_from_payload,
    offer_search_to_params,
    offers_from_payload,
    vehicle_details_from_payload,
    vehicle_search_to_params,
    vehicles_from_payload,
)
from .mapping.workshop import (
    service_types_from_payload,
    workshop_amendment_to_payload,
    workshop_booking_details_from_payload,
    workshop_booking_from_payload,
    workshop_booking_lookup_to_payload,
    workshop_booking_to_payload,
    workshop_slot_search_to_params,
    workshop_slots_from_payload,
)


T = TypeVar("T")
AsyncOperation = Callable[[], Awaitable[T]]
Sleep = Callable[[float], Awaitable[None]]

_OPERATION_RETRY_COUNT: ContextVar[int] = ContextVar(
    "northstar_operation_retry_count", default=0
)


def reset_operation_retry_count() -> None:
    """Reset retry telemetry for the current async task before an adapter call."""
    _OPERATION_RETRY_COUNT.set(0)


def operation_retry_count() -> int:
    """Return completed retry attempts for the current async task."""
    return _OPERATION_RETRY_COUNT.get()

_MESSAGE_FIELD_MAP = {
    "dealershipId": "dealership_id",
    "department": "department",
    "subject": "subject",
    "message": "message",
    "firstName": "customer.first_name",
    "lastName": "customer.last_name",
    "email": "customer.email",
    "phone": "customer.phone",
    "preferredContactMethod": "preferred_contact_method",
}
_CONTACT_FIELD_MAP = {
    "firstName": "customer.first_name",
    "lastName": "customer.last_name",
    "email": "customer.email",
    "phone": "customer.phone",
}


class NorthstarAdapter:
    """Translate dealer-independent operations into explicit Northstar calls."""

    __slots__ = ("_client", "_sleep", "_monotonic", "_location_cache")

    def __init__(
        self,
        client: NorthstarClient,
        *,
        sleep: Sleep = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(client, NorthstarClient):
            raise TypeError("client must be a NorthstarClient")
        self._client = client
        self._sleep = sleep
        self._monotonic = monotonic
        self._location_cache: dict[str, tuple[float, DealerLocation]] = {}

    async def __aenter__(self) -> "NorthstarAdapter":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_dealerships(self) -> tuple[DealerLocation, ...]:
        payload = await self._read("/api/dealerships", resource="dealership_catalogue")
        locations = locations_from_payload(payload, self._client.config)
        self._cache_locations(locations)
        return locations

    async def search_vehicles(self, search: VehicleSearch) -> Page[Vehicle]:
        params = vehicle_search_to_params(search, self._client.config)
        payload = await self._read(
            "/api/vehicles",
            params=params,
            resource="vehicle_search",
        )
        return vehicles_from_payload(payload, self._client.config)

    async def get_vehicle(self, vehicle_id: str) -> VehicleDetails:
        payload = await self._read(
            f"/api/vehicles/{quote(vehicle_id, safe='')}",
            resource="vehicle",
        )
        return vehicle_details_from_payload(payload, self._client.config)

    async def get_vehicle_availability(self, vehicle_id: str) -> VehicleAvailability:
        payload = await self._read(
            f"/api/vehicles/{quote(vehicle_id, safe='')}/availability",
            resource="vehicle_availability",
        )
        return availability_from_payload(payload, self._client.config)

    async def list_offers(self, search: OfferSearch) -> tuple[VehicleOffer, ...]:
        payload = await self._read(
            "/api/offers",
            params=offer_search_to_params(search),
            resource="offer_catalogue",
        )
        return offers_from_payload(payload, self._client.config)

    async def get_offer(self, offer_id: str) -> VehicleOffer:
        payload = await self._read(
            f"/api/offers/{quote(offer_id, safe='')}",
            resource="offer",
        )
        return offer_from_payload(payload, self._client.config)

    async def create_sales_enquiry(
        self,
        request: SalesEnquiryRequest,
        *,
        idempotency_key: str,
    ) -> SalesEnquiry:
        payload = await self._idempotent_create(
            "/api/sales-enquiries",
            body=sales_enquiry_to_payload(request),
            idempotency_key=idempotency_key,
            resource="sales_enquiry",
            field_map={
                **_CONTACT_FIELD_MAP,
                "dealershipId": "dealership_id",
                "vehicleId": "vehicle_id",
                "enquiryType": "enquiry_type",
                "message": "message",
            },
        )
        return sales_enquiry_from_payload(payload)

    async def list_test_drive_slots(
        self,
        search: TestDriveSlotSearch,
    ) -> tuple[TestDriveSlot, ...]:
        payload = await self._read(
            "/api/test-drive-slots",
            params=test_drive_slot_search_to_params(search),
            resource="test_drive_slots",
        )
        return test_drive_slots_from_payload(payload, self._client.config)

    async def book_test_drive(
        self,
        request: TestDriveBookingRequest,
        *,
        idempotency_key: str,
    ) -> TestDriveBooking:
        payload = await self._idempotent_create(
            "/api/test-drive-bookings",
            body=test_drive_booking_to_payload(request),
            idempotency_key=idempotency_key,
            resource="test_drive_booking",
            field_map={**_CONTACT_FIELD_MAP, "slotId": "slot_id", "notes": "notes"},
        )
        return test_drive_booking_from_payload(payload)

    async def register_vehicle_interest(
        self,
        request: VehicleInterestRequest,
        *,
        idempotency_key: str,
    ) -> VehicleInterest:
        payload = await self._idempotent_create(
            "/api/vehicle-interests",
            body=vehicle_interest_to_payload(request),
            idempotency_key=idempotency_key,
            resource="vehicle_interest",
            field_map={**_CONTACT_FIELD_MAP, "vehicleId": "vehicle_id", "notes": "notes"},
        )
        return vehicle_interest_from_payload(payload)

    async def request_callback(
        self,
        request: CallbackRequest,
        *,
        idempotency_key: str,
    ) -> Callback:
        body = callback_to_payload(request)
        payload = await self._idempotent_create(
            "/api/callback-requests",
            body=body,
            idempotency_key=idempotency_key,
            resource="callback",
            field_map={
                **_CONTACT_FIELD_MAP,
                "dealershipId": "dealership_id",
                "department": "department",
                "vehicleId": "vehicle_id",
                "preferredTime": "preferred_time",
                "reason": "reason",
            },
        )
        return callback_from_payload(payload)

    async def value_part_exchange(
        self,
        request: PartExchangeRequest,
        *,
        idempotency_key: str,
    ) -> PartExchangeValuation:
        payload = await self._idempotent_create(
            "/api/part-exchange-valuations",
            body=part_exchange_to_payload(request),
            idempotency_key=idempotency_key,
            resource="part_exchange",
            field_map={
                **_CONTACT_FIELD_MAP,
                "dealershipId": "dealership_id",
                "registration": "vehicle.registration",
                "mileage": "vehicle.mileage",
                "condition": "vehicle.condition",
            },
        )
        return part_exchange_from_payload(payload, self._client.config)

    async def list_service_types(self) -> tuple[WorkshopService, ...]:
        payload = await self._read(
            "/api/service-types",
            resource="workshop_services",
        )
        return service_types_from_payload(payload, self._client.config)

    async def list_workshop_slots(
        self,
        search: WorkshopSlotSearch,
    ) -> tuple[WorkshopSlot, ...]:
        payload = await self._read(
            "/api/workshop-availability",
            params=workshop_slot_search_to_params(search),
            resource="workshop_slots",
        )
        return workshop_slots_from_payload(payload, self._client.config)

    async def book_workshop(
        self,
        request: WorkshopBookingRequest,
        *,
        idempotency_key: str,
    ) -> WorkshopBooking:
        payload = await self._idempotent_create(
            "/api/workshop-bookings",
            body=workshop_booking_to_payload(request),
            idempotency_key=idempotency_key,
            resource="workshop_booking",
            field_map={
                **_CONTACT_FIELD_MAP,
                "slotId": "slot_id",
                "registration": "registration",
                "mileage": "mileage",
                "notes": "notes",
            },
        )
        return workshop_booking_from_payload(payload)

    async def lookup_workshop_booking(
        self,
        lookup: WorkshopBookingLookup,
    ) -> WorkshopBookingDetails:
        payload = await self._logical_read_post(
            "/api/workshop-bookings/lookup",
            body=workshop_booking_lookup_to_payload(lookup),
            resource="workshop_booking_lookup",
            field_map={
                "reference": "reference",
                "lastName": "last_name",
                "registration": "registration",
                "phone": "phone",
            },
        )
        booking = workshop_booking_from_payload(payload)
        dealership = await self.get_dealership(booking.dealership_id)
        return workshop_booking_details_from_payload(
            payload,
            dealership,
            self._client.config,
        )

    async def get_workshop_booking(self, booking_id: str) -> WorkshopBooking:
        payload = await self._read(
            f"/api/workshop-bookings/{quote(booking_id, safe='')}",
            protected=True,
            resource="workshop_booking",
        )
        return workshop_booking_from_payload(payload)

    async def amend_workshop_booking(
        self,
        amendment: WorkshopBookingAmendment,
    ) -> WorkshopBooking:
        body = workshop_amendment_to_payload(amendment)
        path = f"/api/workshop-bookings/{quote(amendment.booking_id, safe='')}"
        try:
            payload = await self._client.request(
                "PATCH",
                path,
                json=body,
                protected=True,
                resource="workshop_booking",
                field_map={"slotId": "slot_id", "mileage": "mileage", "notes": "notes"},
            )
        except DealerError as error:
            if error.kind is not DealerErrorKind.TEMPORARY_FAILURE:
                raise
            current = await self.get_workshop_booking(amendment.booking_id)
            if self._amendment_matches(current, amendment):
                return current
            raise error
        return workshop_booking_from_payload(payload)

    async def cancel_workshop_booking(
        self,
        request: WorkshopCancellationRequest,
    ) -> WorkshopBooking:
        path = f"/api/workshop-bookings/{quote(request.booking_id, safe='')}"

        async def operation() -> dict[str, Any]:
            return await self._client.request(
                "DELETE",
                path,
                protected=True,
                resource="workshop_booking",
            )

        payload = await self._retry_temporary(operation)
        return workshop_booking_from_payload(payload)

    async def list_workshop_locations(self) -> tuple[DealerLocation, ...]:
        payload = await self._read("/api/workshop-locations", resource="workshop_locations")
        locations = locations_from_payload(payload, self._client.config)
        self._cache_locations(locations)
        return locations

    async def get_dealership(self, dealership_id: str) -> DealerLocation:
        cached = self._cached_location(dealership_id)
        if cached is not None:
            return cached
        payload = await self._read(
            f"/api/dealerships/{quote(dealership_id, safe='')}",
            resource="dealership",
        )
        location = location_from_payload(payload, self._client.config)
        self._cache_locations((location,))
        return location

    async def get_opening_hours(
        self,
        dealership_id: str,
        department: Department | None = None,
    ) -> OpeningHours:
        if department is Department.GENERAL:
            raise invalid_request(resource="opening_hours")
        payload = await self._read(
            f"/api/dealerships/{quote(dealership_id, safe='')}/opening-hours",
            resource="opening_hours",
        )
        return opening_hours_from_payload(payload, self._client.config, department)

    async def send_dealership_message(
        self,
        request: DealershipMessageRequest,
        *,
        idempotency_key: str,
    ) -> DealershipMessage:
        body = dealership_message_to_payload(request)
        payload = await self._idempotent_create(
            "/api/dealership-messages",
            body=body,
            idempotency_key=idempotency_key,
            resource="dealership_message",
            field_map=_MESSAGE_FIELD_MAP,
        )
        return dealership_message_from_payload(payload)

    async def get_business_information(self) -> BusinessInformation:
        payload = await self._read(
            "/api/business-information",
            resource="business_information",
        )
        return business_information_from_payload(payload)

    async def _read(
        self,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
        protected: bool = False,
        resource: str,
        field_map: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            return await self._client.request(
                "GET",
                path,
                params=params,
                protected=protected,
                resource=resource,
                field_map=field_map,
            )

        return await self._retry_temporary(operation)

    async def _idempotent_create(
        self,
        path: str,
        *,
        body: Mapping[str, Any],
        idempotency_key: str,
        resource: str,
        field_map: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            return await self._client.request(
                "POST",
                path,
                json=body,
                protected=True,
                idempotency_key=idempotency_key,
                resource=resource,
                field_map=field_map,
            )

        return await self._retry_temporary(operation)

    async def _logical_read_post(
        self,
        path: str,
        *,
        body: Mapping[str, Any],
        resource: str,
        field_map: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            return await self._client.request(
                "POST",
                path,
                json=body,
                protected=True,
                resource=resource,
                field_map=field_map,
            )

        return await self._retry_temporary(operation)

    async def _retry_temporary(self, operation: AsyncOperation[T]) -> T:
        for attempt, delay in enumerate(
            (*self._client.config.retry_delays_seconds, None)
        ):
            _OPERATION_RETRY_COUNT.set(
                max(_OPERATION_RETRY_COUNT.get(), attempt)
            )
            try:
                return await operation()
            except DealerError as error:
                if error.kind is not DealerErrorKind.TEMPORARY_FAILURE or delay is None:
                    raise
                await self._sleep(delay)
        raise RuntimeError("unreachable")

    def _cache_locations(self, locations: tuple[DealerLocation, ...]) -> None:
        expires_at = self._monotonic() + self._client.config.location_cache_ttl_seconds
        for location in locations:
            self._location_cache[location.id] = (expires_at, location)

    def _cached_location(self, dealership_id: str) -> DealerLocation | None:
        cached = self._location_cache.get(dealership_id)
        if cached is None:
            return None
        expires_at, location = cached
        if self._monotonic() < expires_at:
            return location
        del self._location_cache[dealership_id]
        return None

    @staticmethod
    def _amendment_matches(
        booking: WorkshopBooking,
        amendment: WorkshopBookingAmendment,
    ) -> bool:
        if amendment.slot_id is not None and booking.slot_id != amendment.slot_id:
            return False
        if amendment.mileage is not None and booking.mileage != amendment.mileage:
            return False
        if amendment.notes is not None:
            expected_notes = None if amendment.notes == "" else amendment.notes
            if booking.notes != expected_notes:
                return False
        return True
