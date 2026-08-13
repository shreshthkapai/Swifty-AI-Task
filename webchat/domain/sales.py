"""Immutable sales contracts shared by dealership adapters and workflows."""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from .common import (
    BookingStatus,
    CustomerIdentity,
    Department,
    Money,
    ReceivedStatus,
    require_aware,
    require_non_empty,
)


class EnquiryType(StrEnum):
    GENERAL = "general"
    AVAILABILITY = "availability"
    FINANCE = "finance"
    PART_EXCHANGE = "part_exchange"


class InterestStatus(StrEnum):
    REGISTERED = "registered"


class CallbackStatus(StrEnum):
    REQUESTED = "requested"


class PartExchangeCondition(StrEnum):
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"


class PartExchangeStatus(StrEnum):
    ESTIMATED = "estimated"


def _optional_text(value: str | None, field: str, *, allow_empty: bool = False) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string or None")
    cleaned = value.strip()
    if not cleaned and not allow_empty:
        raise ValueError(f"{field} must be non-empty when supplied")
    return cleaned


def _non_negative_int(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _record_fields(instance: object, fields: tuple[str, ...]) -> None:
    for field in fields:
        object.__setattr__(instance, field, require_non_empty(getattr(instance, field), field))


def _require_customer(customer: CustomerIdentity) -> None:
    if not isinstance(customer, CustomerIdentity):
        raise ValueError("customer must be a CustomerIdentity")


def _require_aware_field(instance: object, field: str) -> None:
    require_aware(getattr(instance, field), field)


@dataclass(frozen=True, slots=True)
class SalesEnquiryRequest:
    dealership_id: str
    enquiry_type: EnquiryType
    customer: CustomerIdentity
    message: str
    vehicle_id: str | None = None

    def __post_init__(self) -> None:
        _record_fields(self, ("dealership_id", "message"))
        object.__setattr__(self, "vehicle_id", _optional_text(self.vehicle_id, "vehicle_id"))
        if not isinstance(self.enquiry_type, EnquiryType):
            raise ValueError("enquiry_type must be an EnquiryType")
        _require_customer(self.customer)


@dataclass(frozen=True, slots=True)
class SalesEnquiry:
    id: str
    reference: str
    request: SalesEnquiryRequest
    status: ReceivedStatus
    created_at: datetime

    def __post_init__(self) -> None:
        _record_fields(self, ("id", "reference"))
        if not isinstance(self.request, SalesEnquiryRequest):
            raise ValueError("request must be a SalesEnquiryRequest")
        if not isinstance(self.status, ReceivedStatus):
            raise ValueError("status must be a ReceivedStatus")
        _require_aware_field(self, "created_at")


@dataclass(frozen=True, slots=True)
class TestDriveSlotSearch:
    vehicle_id: str | None = None
    dealership_id: str | None = None
    date_from: date | None = None
    date_to: date | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "vehicle_id", _optional_text(self.vehicle_id, "vehicle_id"))
        object.__setattr__(self, "dealership_id", _optional_text(self.dealership_id, "dealership_id"))
        for field in ("date_from", "date_to"):
            value = getattr(self, field)
            if value is not None and (isinstance(value, datetime) or not isinstance(value, date)):
                raise ValueError(f"{field} must be a date")
        if self.date_from is not None and self.date_to is not None and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")


@dataclass(frozen=True, slots=True)
class TestDriveSlot:
    id: str
    dealership_id: str
    vehicle_id: str
    starts_at: datetime
    dealership_name: str
    vehicle_label: str

    def __post_init__(self) -> None:
        _record_fields(self, ("id", "dealership_id", "vehicle_id", "dealership_name", "vehicle_label"))
        _require_aware_field(self, "starts_at")


@dataclass(frozen=True, slots=True)
class TestDriveBookingRequest:
    slot_id: str
    customer: CustomerIdentity
    notes: str | None = None

    def __post_init__(self) -> None:
        _record_fields(self, ("slot_id",))
        _require_customer(self.customer)
        object.__setattr__(self, "notes", _optional_text(self.notes, "notes", allow_empty=True))


@dataclass(frozen=True, slots=True)
class TestDriveBooking:
    id: str
    reference: str
    request: TestDriveBookingRequest
    dealership_id: str
    vehicle_id: str
    status: BookingStatus
    created_at: datetime

    def __post_init__(self) -> None:
        _record_fields(self, ("id", "reference", "dealership_id", "vehicle_id"))
        if not isinstance(self.request, TestDriveBookingRequest):
            raise ValueError("request must be a TestDriveBookingRequest")
        if not isinstance(self.status, BookingStatus):
            raise ValueError("status must be a BookingStatus")
        _require_aware_field(self, "created_at")


@dataclass(frozen=True, slots=True)
class VehicleInterestRequest:
    vehicle_id: str
    customer: CustomerIdentity
    notes: str | None = None

    def __post_init__(self) -> None:
        _record_fields(self, ("vehicle_id",))
        _require_customer(self.customer)
        object.__setattr__(self, "notes", _optional_text(self.notes, "notes", allow_empty=True))


@dataclass(frozen=True, slots=True)
class VehicleInterest:
    id: str
    reference: str
    request: VehicleInterestRequest
    dealership_id: str
    status: InterestStatus
    created_at: datetime

    def __post_init__(self) -> None:
        _record_fields(self, ("id", "reference", "dealership_id"))
        if not isinstance(self.request, VehicleInterestRequest):
            raise ValueError("request must be a VehicleInterestRequest")
        if not isinstance(self.status, InterestStatus):
            raise ValueError("status must be an InterestStatus")
        _require_aware_field(self, "created_at")


@dataclass(frozen=True, slots=True)
class CallbackRequest:
    dealership_id: str
    department: Department
    customer: CustomerIdentity
    reason: str
    preferred_time: str | None = None
    vehicle_id: str | None = None

    def __post_init__(self) -> None:
        _record_fields(self, ("dealership_id", "reason"))
        if not isinstance(self.department, Department):
            raise ValueError("department must be a Department")
        _require_customer(self.customer)
        object.__setattr__(self, "preferred_time", _optional_text(self.preferred_time, "preferred_time"))
        object.__setattr__(self, "vehicle_id", _optional_text(self.vehicle_id, "vehicle_id"))


@dataclass(frozen=True, slots=True)
class Callback:
    id: str
    reference: str
    request: CallbackRequest
    status: CallbackStatus
    created_at: datetime

    def __post_init__(self) -> None:
        _record_fields(self, ("id", "reference"))
        if not isinstance(self.request, CallbackRequest):
            raise ValueError("request must be a CallbackRequest")
        if not isinstance(self.status, CallbackStatus):
            raise ValueError("status must be a CallbackStatus")
        _require_aware_field(self, "created_at")


@dataclass(frozen=True, slots=True)
class PartExchangeVehicle:
    registration: str
    mileage: int
    condition: PartExchangeCondition

    def __post_init__(self) -> None:
        _record_fields(self, ("registration",))
        _non_negative_int(self.mileage, "mileage")
        if not isinstance(self.condition, PartExchangeCondition):
            raise ValueError("condition must be a PartExchangeCondition")


@dataclass(frozen=True, slots=True)
class PartExchangeRequest:
    dealership_id: str
    vehicle: PartExchangeVehicle
    customer: CustomerIdentity

    def __post_init__(self) -> None:
        _record_fields(self, ("dealership_id",))
        if not isinstance(self.vehicle, PartExchangeVehicle):
            raise ValueError("vehicle must be a PartExchangeVehicle")
        _require_customer(self.customer)


@dataclass(frozen=True, slots=True)
class PartExchangeValuation:
    id: str
    reference: str
    request: PartExchangeRequest
    estimate_low: Money
    estimate_high: Money
    status: PartExchangeStatus
    created_at: datetime
    qualification: str

    def __post_init__(self) -> None:
        _record_fields(self, ("id", "reference", "qualification"))
        if not isinstance(self.request, PartExchangeRequest):
            raise ValueError("request must be a PartExchangeRequest")
        if not isinstance(self.estimate_low, Money) or not isinstance(self.estimate_high, Money):
            raise ValueError("valuation estimates must be Money")
        if self.estimate_low.currency != self.estimate_high.currency:
            raise ValueError("valuation estimates must use the same currency")
        if self.estimate_low.amount_minor > self.estimate_high.amount_minor:
            raise ValueError("estimate_low must not exceed estimate_high")
        if not isinstance(self.status, PartExchangeStatus):
            raise ValueError("status must be a PartExchangeStatus")
        _require_aware_field(self, "created_at")
