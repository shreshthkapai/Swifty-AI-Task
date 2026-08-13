"""Northstar sales, test-drive, callback, and valuation mappings."""

from webchat.domain import (
    BookingStatus,
    Callback,
    CallbackRequest,
    CallbackStatus,
    Department,
    EnquiryType,
    InterestStatus,
    Money,
    PartExchangeCondition,
    PartExchangeRequest,
    PartExchangeStatus,
    PartExchangeValuation,
    PartExchangeVehicle,
    ReceivedStatus,
    SalesEnquiry,
    SalesEnquiryRequest,
    TestDriveBooking,
    TestDriveBookingRequest,
    TestDriveSlot,
    TestDriveSlotSearch,
    VehicleInterest,
    VehicleInterestRequest,
)

from ..config import NorthstarConfig
from ..errors import invalid_request, invalid_response
from .common import (
    JsonObject,
    customer_from_payload,
    customer_to_payload,
    money_from_minor,
    read_datetime,
    read_int,
    read_items,
    read_object,
    read_optional_str,
    read_str,
)


_ENQUIRY_TO_PLATFORM = {
    EnquiryType.GENERAL: "general",
    EnquiryType.AVAILABILITY: "availability",
    EnquiryType.FINANCE: "finance",
    EnquiryType.PART_EXCHANGE: "part-exchange",
}
_ENQUIRY_FROM_PLATFORM = {value: key for key, value in _ENQUIRY_TO_PLATFORM.items()}


def sales_enquiry_to_payload(request: SalesEnquiryRequest) -> dict[str, object]:
    payload: dict[str, object] = {
        "dealershipId": request.dealership_id,
        "enquiryType": _ENQUIRY_TO_PLATFORM[request.enquiry_type],
        **customer_to_payload(request.customer),
        "message": request.message,
    }
    if request.vehicle_id is not None:
        payload["vehicleId"] = request.vehicle_id
    return payload


def sales_enquiry_from_payload(payload: object) -> SalesEnquiry:
    body = read_object(payload)
    try:
        request = SalesEnquiryRequest(
            dealership_id=read_str(body, "dealershipId"),
            vehicle_id=read_optional_str(body, "vehicleId"),
            enquiry_type=_ENQUIRY_FROM_PLATFORM[read_str(body, "enquiryType")],
            customer=_customer(body),
            message=read_str(body, "message"),
        )
        return SalesEnquiry(
            id=read_str(body, "id"),
            reference=read_str(body, "reference"),
            request=request,
            status=ReceivedStatus(read_str(body, "status")),
            created_at=read_datetime(body, "createdAt"),
        )
    except (KeyError, ValueError) as error:
        raise invalid_response(resource="sales_enquiry") from error


def test_drive_slot_search_to_params(search: TestDriveSlotSearch) -> dict[str, str]:
    values = {
        "vehicleId": search.vehicle_id,
        "dealershipId": search.dealership_id,
        "dateFrom": None if search.date_from is None else search.date_from.isoformat(),
        "dateTo": None if search.date_to is None else search.date_to.isoformat(),
    }
    return {key: value for key, value in values.items() if value is not None}


def test_drive_slots_from_payload(payload: object) -> tuple[TestDriveSlot, ...]:
    return tuple(_test_drive_slot(item) for item in read_items(payload))


def _test_drive_slot(payload: JsonObject) -> TestDriveSlot:
    if read_str(payload, "status") != "available":
        raise invalid_response(resource="test_drive_slot")
    try:
        return TestDriveSlot(
            id=read_str(payload, "id"),
            dealership_id=read_str(payload, "dealershipId"),
            vehicle_id=read_str(payload, "vehicleId"),
            starts_at=read_datetime(payload, "startsAt"),
            dealership_name=read_str(payload, "dealershipName"),
            vehicle_label=" ".join(
                (
                    read_str(payload, "make"),
                    read_str(payload, "model"),
                    read_str(payload, "variant"),
                )
            ),
        )
    except ValueError as error:
        raise invalid_response(resource="test_drive_slot") from error


def test_drive_booking_to_payload(request: TestDriveBookingRequest) -> dict[str, object]:
    payload: dict[str, object] = {
        "slotId": request.slot_id,
        **customer_to_payload(request.customer),
    }
    if request.notes is not None:
        payload["notes"] = request.notes
    return payload


def test_drive_booking_from_payload(payload: object) -> TestDriveBooking:
    body = read_object(payload)
    status = read_str(body, "status")
    if status != BookingStatus.CONFIRMED.value:
        raise invalid_response(resource="test_drive_booking")
    try:
        request = TestDriveBookingRequest(
            slot_id=read_str(body, "slotId"),
            customer=_customer(body),
            notes=read_optional_str(body, "notes"),
        )
        return TestDriveBooking(
            id=read_str(body, "id"),
            reference=read_str(body, "reference"),
            request=request,
            dealership_id=read_str(body, "dealershipId"),
            vehicle_id=read_str(body, "vehicleId"),
            status=BookingStatus(status),
            created_at=read_datetime(body, "createdAt"),
        )
    except ValueError as error:
        raise invalid_response(resource="test_drive_booking") from error


def vehicle_interest_to_payload(request: VehicleInterestRequest) -> dict[str, object]:
    payload: dict[str, object] = {
        "vehicleId": request.vehicle_id,
        **customer_to_payload(request.customer),
    }
    if request.notes is not None:
        payload["notes"] = request.notes
    return payload


def vehicle_interest_from_payload(payload: object) -> VehicleInterest:
    body = read_object(payload)
    try:
        request = VehicleInterestRequest(
            vehicle_id=read_str(body, "vehicleId"),
            customer=_customer(body),
            notes=read_optional_str(body, "notes"),
        )
        return VehicleInterest(
            id=read_str(body, "id"),
            reference=read_str(body, "reference"),
            request=request,
            dealership_id=read_str(body, "dealershipId"),
            status=InterestStatus(read_str(body, "status")),
            created_at=read_datetime(body, "createdAt"),
        )
    except ValueError as error:
        raise invalid_response(resource="vehicle_interest") from error


def callback_to_payload(request: CallbackRequest) -> dict[str, object]:
    if request.department is Department.GENERAL:
        raise invalid_request(resource="callback")
    payload: dict[str, object] = {
        "dealershipId": request.dealership_id,
        "department": request.department.value,
        **customer_to_payload(request.customer),
        "reason": request.reason,
    }
    if request.preferred_time is not None:
        payload["preferredTime"] = request.preferred_time
    if request.vehicle_id is not None:
        payload["vehicleId"] = request.vehicle_id
    return payload


def callback_from_payload(payload: object) -> Callback:
    body = read_object(payload)
    try:
        request = CallbackRequest(
            dealership_id=read_str(body, "dealershipId"),
            department=Department(read_str(body, "department")),
            customer=_customer(body),
            reason=read_str(body, "reason"),
            preferred_time=read_optional_str(body, "preferredTime"),
            vehicle_id=read_optional_str(body, "vehicleId"),
        )
        if request.department is Department.GENERAL:
            raise ValueError("unsupported callback department")
        return Callback(
            id=read_str(body, "id"),
            reference=read_str(body, "reference"),
            request=request,
            status=CallbackStatus(read_str(body, "status")),
            created_at=read_datetime(body, "createdAt"),
        )
    except ValueError as error:
        raise invalid_response(resource="callback") from error


def part_exchange_to_payload(request: PartExchangeRequest) -> dict[str, object]:
    return {
        "dealershipId": request.dealership_id,
        "registration": request.vehicle.registration,
        "mileage": request.vehicle.mileage,
        "condition": request.vehicle.condition.value,
        **customer_to_payload(request.customer),
    }


def part_exchange_from_payload(
    payload: object,
    config: NorthstarConfig,
) -> PartExchangeValuation:
    body = read_object(payload)
    low = _required_money(body, "estimateLowPence", config.currency)
    high = _required_money(body, "estimateHighPence", config.currency)
    try:
        request = PartExchangeRequest(
            dealership_id=read_str(body, "dealershipId"),
            vehicle=PartExchangeVehicle(
                registration=read_str(body, "registration"),
                mileage=read_int(body, "mileage"),
                condition=PartExchangeCondition(read_str(body, "condition")),
            ),
            customer=_customer(body),
        )
        return PartExchangeValuation(
            id=read_str(body, "id"),
            reference=read_str(body, "reference"),
            request=request,
            estimate_low=low,
            estimate_high=high,
            status=PartExchangeStatus(read_str(body, "status")),
            created_at=read_datetime(body, "createdAt"),
            qualification=read_str(body, "estimateNotice"),
        )
    except ValueError as error:
        raise invalid_response(resource="part_exchange") from error


def _customer(payload: JsonObject):
    return customer_from_payload(
        payload,
        first_name="firstName",
        last_name="lastName",
    )


def _required_money(payload: JsonObject, key: str, currency: str) -> Money:
    value = money_from_minor(payload, key, currency)
    if value is None:
        raise invalid_response(resource="part_exchange")
    return value
