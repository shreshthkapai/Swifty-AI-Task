"""Northstar workshop catalogue, slot, and booking mappings."""

from webchat.domain import (
    BookingStatus,
    DealerLocation,
    WorkshopBooking,
    WorkshopBookingAmendment,
    WorkshopBookingDetails,
    WorkshopBookingLookup,
    WorkshopBookingRequest,
    WorkshopService,
    WorkshopSlot,
    WorkshopSlotSearch,
)

from ..config import NorthstarConfig
from ..errors import invalid_response
from .common import (
    JsonObject,
    customer_from_payload,
    customer_to_payload,
    money_from_minor,
    read_datetime,
    read_int,
    read_items,
    read_object,
    read_optional_datetime,
    read_optional_str,
    read_str,
    read_zoned_datetime,
)


def service_types_from_payload(
    payload: object,
    config: NorthstarConfig,
) -> tuple[WorkshopService, ...]:
    return tuple(_service_from_payload(item, config) for item in read_items(payload))


def _service_from_payload(payload: JsonObject, config: NorthstarConfig) -> WorkshopService:
    try:
        return WorkshopService(
            id=read_str(payload, "id"),
            name=read_str(payload, "name"),
            description=read_str(payload, "description"),
            duration_minutes=read_int(payload, "durationMinutes"),
            price_from=money_from_minor(payload, "priceFromPence", config.currency),
        )
    except ValueError as error:
        raise invalid_response(resource="workshop_service") from error


def workshop_slot_search_to_params(search: WorkshopSlotSearch) -> dict[str, str]:
    values = {
        "dealershipId": search.dealership_id,
        "serviceTypeId": search.service_type_id,
        "dateFrom": None if search.date_from is None else search.date_from.isoformat(),
        "dateTo": None if search.date_to is None else search.date_to.isoformat(),
    }
    return {key: value for key, value in values.items() if value is not None}


def workshop_slots_from_payload(
    payload: object,
    config: NorthstarConfig,
) -> tuple[WorkshopSlot, ...]:
    return tuple(_workshop_slot(item, config) for item in read_items(payload))


def _workshop_slot(payload: JsonObject, config: NorthstarConfig) -> WorkshopSlot:
    if read_str(payload, "status") != "available":
        raise invalid_response(resource="workshop_slot")
    try:
        return WorkshopSlot(
            id=read_str(payload, "id"),
            dealership_id=read_str(payload, "dealershipId"),
            service_type_id=read_str(payload, "serviceTypeId"),
            starts_at=read_zoned_datetime(payload, "startsAt", config.timezone),
            dealership_name=read_str(payload, "dealershipName"),
            service_name=read_str(payload, "serviceName"),
            duration_minutes=read_int(payload, "durationMinutes"),
            price_from=money_from_minor(payload, "priceFromPence", config.currency),
        )
    except ValueError as error:
        raise invalid_response(resource="workshop_slot") from error


def workshop_booking_to_payload(request: WorkshopBookingRequest) -> dict[str, object]:
    payload: dict[str, object] = {
        "slotId": request.slot_id,
        "registration": request.registration,
        "mileage": request.mileage,
        **customer_to_payload(request.customer),
    }
    if request.notes is not None:
        payload["notes"] = request.notes
    return payload


def workshop_booking_lookup_to_payload(lookup: WorkshopBookingLookup) -> dict[str, str]:
    return {
        "reference": lookup.reference,
        "lastName": lookup.last_name,
        "registration": lookup.registration,
        "phone": lookup.phone,
    }


def workshop_amendment_to_payload(amendment: WorkshopBookingAmendment) -> dict[str, object]:
    payload: dict[str, object] = {}
    if amendment.slot_id is not None:
        payload["slotId"] = amendment.slot_id
    if amendment.mileage is not None:
        payload["mileage"] = amendment.mileage
    if amendment.notes is not None:
        payload["notes"] = amendment.notes
    return payload


def workshop_booking_from_payload(payload: object) -> WorkshopBooking:
    body = read_object(payload)
    try:
        return WorkshopBooking(
            id=read_str(body, "id"),
            reference=read_str(body, "reference"),
            slot_id=read_str(body, "slotId"),
            dealership_id=read_str(body, "dealershipId"),
            service_type_id=read_str(body, "serviceTypeId"),
            customer=customer_from_payload(
                body,
                first_name="firstName",
                last_name="lastName",
            ),
            registration=read_str(body, "registration"),
            mileage=read_int(body, "mileage"),
            notes=read_optional_str(body, "notes"),
            status=BookingStatus(read_str(body, "status")),
            created_at=read_datetime(body, "createdAt"),
            updated_at=read_datetime(body, "updatedAt"),
            cancelled_at=read_optional_datetime(body, "cancelledAt"),
        )
    except ValueError as error:
        raise invalid_response(resource="workshop_booking") from error


def workshop_booking_details_from_payload(
    payload: object,
    dealership: DealerLocation,
    config: NorthstarConfig,
) -> WorkshopBookingDetails:
    body = read_object(payload)
    if (
        read_str(body, "dealershipName") != dealership.name
        or read_str(body, "dealershipAddressLine") != dealership.address.lines[0]
        or read_str(body, "dealershipTown") != dealership.address.town
        or read_str(body, "dealershipPostcode") != dealership.address.postcode
    ):
        raise invalid_response(resource="workshop_booking")
    try:
        return WorkshopBookingDetails(
            booking=workshop_booking_from_payload(body),
            starts_at=read_zoned_datetime(body, "startsAt", config.timezone),
            service_type_name=read_str(body, "serviceTypeName"),
            dealership=dealership,
        )
    except ValueError as error:
        raise invalid_response(resource="workshop_booking") from error
