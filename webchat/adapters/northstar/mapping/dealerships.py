"""Northstar dealership, hours, messaging, and business mappings."""

from webchat.domain import (
    Address,
    BusinessInformation,
    ContactMethod,
    DealerLocation,
    DealershipMessage,
    DealershipMessageRequest,
    Department,
    HolidayOpening,
    OpeningHours,
    OpeningPeriod,
    ReceivedStatus,
)

from ..config import NorthstarConfig
from ..errors import invalid_response
from .common import (
    JsonObject,
    customer_from_payload,
    customer_to_payload,
    read_bool,
    read_date,
    read_datetime,
    read_decimal,
    read_int,
    read_items,
    read_object,
    read_optional_time,
    read_str,
    read_string_tuple,
)


def location_from_payload(payload: object, config: NorthstarConfig) -> DealerLocation:
    body = read_object(payload)
    try:
        return DealerLocation(
            id=read_str(body, "id"),
            name=read_str(body, "name"),
            address=Address(
                lines=(read_str(body, "addressLine"),),
                town=read_str(body, "town"),
                postcode=read_str(body, "postcode"),
                country=config.country,
            ),
            phone=read_str(body, "phone"),
            email=read_str(body, "email"),
            latitude=read_decimal(body, "latitude"),
            longitude=read_decimal(body, "longitude"),
            brands=read_string_tuple(body, "brands"),
        )
    except ValueError as error:
        raise invalid_response(resource="dealership") from error


def locations_from_payload(payload: object, config: NorthstarConfig) -> tuple[DealerLocation, ...]:
    return tuple(location_from_payload(item, config) for item in read_items(payload))


def opening_hours_from_payload(
    payload: object,
    config: NorthstarConfig,
    department: Department | None = None,
) -> OpeningHours:
    body = read_object(payload)
    weekly = body.get("weekly")
    holidays = body.get("holidayExceptions")
    if not isinstance(weekly, list) or not isinstance(holidays, list):
        raise invalid_response(resource="opening_hours")

    regular = tuple(
        period
        for item in weekly
        if (period := _weekly_period(read_object(item))).department is department or department is None
    )
    exceptions = tuple(
        period
        for item in holidays
        if (period := _holiday_period(read_object(item))).department is department or department is None
    )
    try:
        return OpeningHours(
            dealership_id=read_str(body, "dealershipId"),
            timezone=config.timezone,
            regular=regular,
            holidays=exceptions,
        )
    except ValueError as error:
        raise invalid_response(resource="opening_hours") from error


def _weekly_period(payload: JsonObject) -> OpeningPeriod:
    department = _department(payload, "department")
    read_str(payload, "day")
    day = read_int(payload, "dayOfWeek")
    opens_at = read_optional_time(payload, "opensAt")
    closes_at = read_optional_time(payload, "closesAt")
    _validate_closed(payload, opens_at, closes_at)
    try:
        return OpeningPeriod(department, day, opens_at, closes_at)
    except ValueError as error:
        raise invalid_response(resource="opening_hours") from error


def _holiday_period(payload: JsonObject) -> HolidayOpening:
    department = _department(payload, "department")
    opens_at = read_optional_time(payload, "opensAt")
    closes_at = read_optional_time(payload, "closesAt")
    _validate_closed(payload, opens_at, closes_at)
    try:
        return HolidayOpening(
            date=read_date(payload, "date"),
            label=read_str(payload, "label"),
            department=department,
            opens_at=opens_at,
            closes_at=closes_at,
        )
    except ValueError as error:
        raise invalid_response(resource="opening_hours") from error


def _validate_closed(payload: JsonObject, opens_at: object, closes_at: object) -> None:
    closed = read_bool(payload, "closed")
    if closed != (opens_at is None and closes_at is None):
        raise invalid_response(resource="opening_hours")


def _department(payload: JsonObject, key: str) -> Department:
    try:
        return Department(read_str(payload, key))
    except ValueError as error:
        raise invalid_response() from error


def dealership_message_to_payload(request: DealershipMessageRequest) -> dict[str, object]:
    return {
        "dealershipId": request.dealership_id,
        "department": request.department.value,
        "subject": request.subject,
        "message": request.message,
        **customer_to_payload(request.customer),
        "preferredContactMethod": request.preferred_contact_method.value,
    }


def dealership_message_from_payload(payload: object) -> DealershipMessage:
    body = read_object(payload)
    try:
        request = DealershipMessageRequest(
            dealership_id=read_str(body, "dealershipId"),
            department=Department(read_str(body, "department")),
            subject=read_str(body, "subject"),
            message=read_str(body, "message"),
            customer=customer_from_payload(
                body,
                first_name="firstName",
                last_name="lastName",
            ),
            preferred_contact_method=ContactMethod(read_str(body, "preferredContactMethod")),
        )
        return DealershipMessage(
            id=read_str(body, "id"),
            reference=read_str(body, "reference"),
            request=request,
            status=ReceivedStatus(read_str(body, "status")),
            created_at=read_datetime(body, "createdAt"),
        )
    except ValueError as error:
        raise invalid_response(resource="dealership_message") from error


def business_information_from_payload(payload: object) -> BusinessInformation:
    body = read_object(payload)
    finance = read_object(body.get("finance"))
    part_exchange = read_object(body.get("partExchange"))
    try:
        return BusinessInformation(
            organisation=read_str(body, "organisation"),
            currency=read_str(body, "currency"),
            market=read_str(body, "market"),
            finance_notice=read_str(finance, "notice"),
            finance_minimum_age=read_int(finance, "minimumAge"),
            part_exchange_notice=read_str(part_exchange, "estimateNotice"),
            privacy_contact=read_str(body, "privacyContact"),
        )
    except ValueError as error:
        raise invalid_response(resource="business_information") from error
