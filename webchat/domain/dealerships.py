"""Immutable dealership location, opening-hours, and messaging contracts."""

from dataclasses import dataclass
from datetime import date as CalendarDate, datetime, time
from decimal import Decimal
from enum import StrEnum
import re

from .common import (
    Address,
    CustomerIdentity,
    Department,
    ReceivedStatus,
    require_aware,
    require_non_empty,
)


_CURRENCY_PATTERN = re.compile(r"[A-Z]{3}")


class ContactMethod(StrEnum):
    EMAIL = "email"
    PHONE = "phone"


def _set_text_fields(instance: object, fields: tuple[str, ...]) -> None:
    for field in fields:
        object.__setattr__(instance, field, require_non_empty(getattr(instance, field), field))


def _require_enum(value: object, expected: type[StrEnum], field: str) -> None:
    if not isinstance(value, expected):
        raise ValueError(f"{field} must be a {expected.__name__}")


def _validate_period(opens_at: time | None, closes_at: time | None) -> None:
    if (opens_at is None) != (closes_at is None):
        raise ValueError("opens_at and closes_at must both be supplied or both be absent")
    if opens_at is not None:
        if not isinstance(opens_at, time) or not isinstance(closes_at, time):
            raise ValueError("opening values must be times")
        if opens_at >= closes_at:
            raise ValueError("opens_at must be before closes_at")


@dataclass(frozen=True, slots=True)
class DealerLocation:
    id: str
    name: str
    address: Address
    phone: str
    email: str
    latitude: Decimal
    longitude: Decimal
    brands: tuple[str, ...]

    def __post_init__(self) -> None:
        _set_text_fields(self, ("id", "name", "phone", "email"))
        if not isinstance(self.address, Address):
            raise ValueError("address must be an Address")
        coordinate_ranges = (
            ("latitude", Decimal("-90"), Decimal("90")),
            ("longitude", Decimal("-180"), Decimal("180")),
        )
        for field, minimum, maximum in coordinate_ranges:
            value = getattr(self, field)
            if not isinstance(value, Decimal) or not value.is_finite() or not minimum <= value <= maximum:
                raise ValueError(f"{field} must be a finite Decimal between {minimum} and {maximum}")
        if not isinstance(self.brands, tuple):
            raise ValueError("brands must be a tuple")
        object.__setattr__(self, "brands", tuple(require_non_empty(brand, "brands") for brand in self.brands))


@dataclass(frozen=True, slots=True)
class OpeningPeriod:
    department: Department
    day_of_week: int
    opens_at: time | None
    closes_at: time | None

    def __post_init__(self) -> None:
        _require_enum(self.department, Department, "department")
        if (
            isinstance(self.day_of_week, bool)
            or not isinstance(self.day_of_week, int)
            or not 0 <= self.day_of_week <= 6
        ):
            raise ValueError("day_of_week must be an integer from 0 to 6")
        _validate_period(self.opens_at, self.closes_at)

    @property
    def is_closed(self) -> bool:
        return self.opens_at is None


@dataclass(frozen=True, slots=True)
class HolidayOpening:
    date: CalendarDate
    label: str
    department: Department
    opens_at: time | None
    closes_at: time | None

    def __post_init__(self) -> None:
        if isinstance(self.date, datetime) or not isinstance(self.date, CalendarDate):
            raise ValueError("date must be a date")
        object.__setattr__(self, "label", require_non_empty(self.label, "label"))
        _require_enum(self.department, Department, "department")
        _validate_period(self.opens_at, self.closes_at)

    @property
    def is_closed(self) -> bool:
        return self.opens_at is None


@dataclass(frozen=True, slots=True)
class OpeningHours:
    dealership_id: str
    timezone: str
    regular: tuple[OpeningPeriod, ...]
    holidays: tuple[HolidayOpening, ...]

    def __post_init__(self) -> None:
        _set_text_fields(self, ("dealership_id", "timezone"))
        if not isinstance(self.regular, tuple) or not all(
            isinstance(period, OpeningPeriod) for period in self.regular
        ):
            raise ValueError("regular must be a tuple of OpeningPeriod values")
        if not isinstance(self.holidays, tuple) or not all(
            isinstance(period, HolidayOpening) for period in self.holidays
        ):
            raise ValueError("holidays must be a tuple of HolidayOpening values")


@dataclass(frozen=True, slots=True)
class DealershipMessageRequest:
    dealership_id: str
    department: Department
    subject: str
    message: str
    customer: CustomerIdentity
    preferred_contact_method: ContactMethod

    def __post_init__(self) -> None:
        _set_text_fields(self, ("dealership_id", "subject", "message"))
        _require_enum(self.department, Department, "department")
        _require_enum(self.preferred_contact_method, ContactMethod, "preferred_contact_method")
        if not isinstance(self.customer, CustomerIdentity):
            raise ValueError("customer must be a CustomerIdentity")


@dataclass(frozen=True, slots=True)
class DealershipMessage:
    id: str
    reference: str
    request: DealershipMessageRequest
    status: ReceivedStatus
    created_at: datetime

    def __post_init__(self) -> None:
        _set_text_fields(self, ("id", "reference"))
        if not isinstance(self.request, DealershipMessageRequest):
            raise ValueError("request must be a DealershipMessageRequest")
        _require_enum(self.status, ReceivedStatus, "status")
        require_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class BusinessInformation:
    organisation: str
    currency: str
    market: str
    finance_notice: str
    finance_minimum_age: int
    part_exchange_notice: str
    privacy_contact: str

    def __post_init__(self) -> None:
        _set_text_fields(
            self,
            ("organisation", "currency", "market", "finance_notice", "part_exchange_notice", "privacy_contact"),
        )
        if not _CURRENCY_PATTERN.fullmatch(self.currency):
            raise ValueError("currency must be an ISO 4217 code")
        if (
            isinstance(self.finance_minimum_age, bool)
            or not isinstance(self.finance_minimum_age, int)
            or self.finance_minimum_age < 0
        ):
            raise ValueError("finance_minimum_age must be a non-negative integer")
