"""Immutable workshop catalogue and booking contracts."""

from dataclasses import dataclass
from datetime import date, datetime

from .common import BookingStatus, CustomerIdentity, Money, require_aware, require_non_empty
from .dealerships import DealerLocation


def _set_text_fields(instance: object, fields: tuple[str, ...]) -> None:
    for field in fields:
        object.__setattr__(instance, field, require_non_empty(getattr(instance, field), field))


def _non_negative_int(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _positive_int(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _optional_notes(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("notes must be a string or None")
    return value.strip()


def _validate_date_range(date_from: date | None, date_to: date | None) -> None:
    for field, value in (("date_from", date_from), ("date_to", date_to)):
        if value is not None and (isinstance(value, datetime) or not isinstance(value, date)):
            raise ValueError(f"{field} must be a date")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError("date_from must not be after date_to")


@dataclass(frozen=True, slots=True)
class WorkshopService:
    id: str
    name: str
    description: str
    duration_minutes: int
    price_from: Money | None

    def __post_init__(self) -> None:
        _set_text_fields(self, ("id", "name", "description"))
        _positive_int(self.duration_minutes, "duration_minutes")
        if self.price_from is not None and not isinstance(self.price_from, Money):
            raise ValueError("price_from must be Money or None")


@dataclass(frozen=True, slots=True)
class WorkshopSlotSearch:
    dealership_id: str | None = None
    service_type_id: str | None = None
    date_from: date | None = None
    date_to: date | None = None

    def __post_init__(self) -> None:
        for field in ("dealership_id", "service_type_id"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, require_non_empty(value, field))
        _validate_date_range(self.date_from, self.date_to)


@dataclass(frozen=True, slots=True)
class WorkshopSlot:
    id: str
    dealership_id: str
    service_type_id: str
    starts_at: datetime
    dealership_name: str
    service_name: str
    duration_minutes: int
    price_from: Money | None

    def __post_init__(self) -> None:
        _set_text_fields(self, ("id", "dealership_id", "service_type_id", "dealership_name", "service_name"))
        require_aware(self.starts_at, "starts_at")
        _positive_int(self.duration_minutes, "duration_minutes")
        if self.price_from is not None and not isinstance(self.price_from, Money):
            raise ValueError("price_from must be Money or None")


@dataclass(frozen=True, slots=True)
class WorkshopBookingRequest:
    slot_id: str
    customer: CustomerIdentity
    registration: str
    mileage: int
    notes: str | None = None

    def __post_init__(self) -> None:
        _set_text_fields(self, ("slot_id", "registration"))
        if not isinstance(self.customer, CustomerIdentity):
            raise ValueError("customer must be a CustomerIdentity")
        _non_negative_int(self.mileage, "mileage")
        object.__setattr__(self, "notes", _optional_notes(self.notes))


@dataclass(frozen=True, slots=True)
class WorkshopBookingLookup:
    reference: str
    last_name: str
    registration: str
    phone: str

    def __post_init__(self) -> None:
        _set_text_fields(self, ("reference", "last_name", "registration", "phone"))


@dataclass(frozen=True, slots=True)
class WorkshopBookingAmendment:
    booking_id: str
    slot_id: str | None = None
    mileage: int | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        _set_text_fields(self, ("booking_id",))
        if self.slot_id is not None:
            object.__setattr__(self, "slot_id", require_non_empty(self.slot_id, "slot_id"))
        if self.mileage is not None:
            _non_negative_int(self.mileage, "mileage")
        object.__setattr__(self, "notes", _optional_notes(self.notes))
        if self.slot_id is None and self.mileage is None and self.notes is None:
            raise ValueError("an amendment must change slot_id, mileage, or notes")


@dataclass(frozen=True, slots=True)
class WorkshopCancellationRequest:
    booking_id: str

    def __post_init__(self) -> None:
        _set_text_fields(self, ("booking_id",))


@dataclass(frozen=True, slots=True)
class WorkshopBooking:
    id: str
    reference: str
    slot_id: str
    dealership_id: str
    service_type_id: str
    customer: CustomerIdentity
    registration: str
    mileage: int
    notes: str | None
    status: BookingStatus
    created_at: datetime
    updated_at: datetime
    cancelled_at: datetime | None = None

    def __post_init__(self) -> None:
        _set_text_fields(self, ("id", "reference", "slot_id", "dealership_id", "service_type_id", "registration"))
        if not isinstance(self.customer, CustomerIdentity):
            raise ValueError("customer must be a CustomerIdentity")
        _non_negative_int(self.mileage, "mileage")
        object.__setattr__(self, "notes", _optional_notes(self.notes))
        if not isinstance(self.status, BookingStatus):
            raise ValueError("status must be a BookingStatus")
        require_aware(self.created_at, "created_at")
        require_aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        if self.cancelled_at is not None:
            require_aware(self.cancelled_at, "cancelled_at")
            if self.cancelled_at < self.created_at:
                raise ValueError("cancelled_at must not precede created_at")
        if (self.status is BookingStatus.CANCELLED) != (self.cancelled_at is not None):
            raise ValueError("cancelled bookings must have cancelled_at and confirmed bookings must not")


@dataclass(frozen=True, slots=True)
class WorkshopBookingDetails:
    booking: WorkshopBooking
    starts_at: datetime
    service_type_name: str
    dealership: DealerLocation

    def __post_init__(self) -> None:
        if not isinstance(self.booking, WorkshopBooking):
            raise ValueError("booking must be a WorkshopBooking")
        require_aware(self.starts_at, "starts_at")
        object.__setattr__(self, "service_type_name", require_non_empty(self.service_type_name, "service_type_name"))
        if not isinstance(self.dealership, DealerLocation):
            raise ValueError("dealership must be a DealerLocation")
