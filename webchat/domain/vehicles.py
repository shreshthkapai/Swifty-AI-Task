"""Immutable vehicle and offer contracts for dealership adapters."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from .common import Money, require_aware, require_non_empty


class VehicleAvailabilityStatus(StrEnum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    SOLD = "sold"


class VehicleSort(StrEnum):
    NEWEST = "newest"
    PRICE_ASC = "price_asc"
    PRICE_DESC = "price_desc"
    MILEAGE_ASC = "mileage_asc"


def _require_positive_int(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _require_non_negative_int(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _require_optional_non_empty(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    return require_non_empty(value, field)


def _require_matching_currencies(*prices: Money | None) -> None:
    currencies = {price.currency for price in prices if price is not None}
    if len(currencies) > 1:
        raise ValueError("prices must use the same currency")


@dataclass(frozen=True, slots=True)
class VehicleSearch:
    query: str | None = None
    make: str | None = None
    model: str | None = None
    fuel_type: str | None = None
    transmission: str | None = None
    body_style: str | None = None
    availability: VehicleAvailabilityStatus | None = None
    dealership_id: str | None = None
    min_price: Money | None = None
    max_price: Money | None = None
    max_mileage: int | None = None
    min_year: int | None = None
    sort: VehicleSort = VehicleSort.NEWEST
    page: int = 1
    page_size: int = 10

    def __post_init__(self) -> None:
        for field in ("query", "make", "model", "fuel_type", "transmission", "body_style", "dealership_id"):
            object.__setattr__(self, field, _require_optional_non_empty(getattr(self, field), field))
        _require_matching_currencies(self.min_price, self.max_price)
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price.amount_minor > self.max_price.amount_minor
        ):
            raise ValueError("min_price must not exceed max_price")
        if self.max_mileage is not None:
            _require_non_negative_int(self.max_mileage, "max_mileage")
        if self.min_year is not None:
            _require_positive_int(self.min_year, "min_year")
        _require_positive_int(self.page, "page")
        _require_positive_int(self.page_size, "page_size")


@dataclass(frozen=True, slots=True)
class Vehicle:
    id: str
    dealership_id: str
    dealership_name: str
    dealership_town: str
    make: str
    model: str
    variant: str
    year: int
    price: Money | None
    monthly_price: Money | None
    mileage: int
    fuel_type: str
    transmission: str
    colour: str
    body_style: str
    availability: VehicleAvailabilityStatus
    registration: str
    description: str
    images: tuple[str, ...]
    updated_at: datetime

    def __post_init__(self) -> None:
        for field in (
            "id", "dealership_id", "dealership_name", "dealership_town", "make", "model", "variant",
            "fuel_type", "transmission", "colour", "body_style", "registration", "description",
        ):
            object.__setattr__(self, field, require_non_empty(getattr(self, field), field))
        _require_positive_int(self.year, "year")
        _require_non_negative_int(self.mileage, "mileage")
        _require_matching_currencies(self.price, self.monthly_price)
        if not isinstance(self.images, tuple):
            raise ValueError("images must be a tuple")
        object.__setattr__(self, "images", tuple(require_non_empty(image, "images") for image in self.images))
        require_aware(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class VehicleDetails:
    vehicle: Vehicle
    highlights: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.highlights, tuple):
            raise ValueError("highlights must be a tuple")
        object.__setattr__(
            self,
            "highlights",
            tuple(require_non_empty(item, "highlights") for item in self.highlights),
        )


@dataclass(frozen=True, slots=True)
class AvailabilitySlot:
    id: str
    starts_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_non_empty(self.id, "id"))
        require_aware(self.starts_at, "starts_at")


@dataclass(frozen=True, slots=True)
class VehicleAvailability:
    vehicle_id: str
    status: VehicleAvailabilityStatus
    can_enquire: bool
    can_book_test_drive: bool
    can_register_interest: bool
    next_test_drive_slot: AvailabilitySlot | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "vehicle_id", require_non_empty(self.vehicle_id, "vehicle_id"))
        permissions = (self.can_enquire, self.can_book_test_drive, self.can_register_interest)
        if not all(isinstance(value, bool) for value in permissions):
            raise ValueError("availability permissions must be boolean")


@dataclass(frozen=True, slots=True)
class VehicleOffer:
    id: str
    make: str
    model: str
    title: str
    product_type: str
    monthly_price: Money
    upfront_price: Money
    apr_percent: Decimal | None
    term_months: int
    annual_mileage: int
    expires_on: date
    description: str
    image_url: str | None

    def __post_init__(self) -> None:
        for field in ("id", "make", "model", "title", "product_type", "description"):
            object.__setattr__(self, field, require_non_empty(getattr(self, field), field))
        object.__setattr__(self, "image_url", _require_optional_non_empty(self.image_url, "image_url"))
        _require_matching_currencies(self.monthly_price, self.upfront_price)
        if self.apr_percent is not None and (
            not isinstance(self.apr_percent, Decimal)
            or not self.apr_percent.is_finite()
            or self.apr_percent < 0
        ):
            raise ValueError("apr_percent must be a non-negative finite Decimal")
        _require_positive_int(self.term_months, "term_months")
        _require_non_negative_int(self.annual_mileage, "annual_mileage")
        if isinstance(self.expires_on, datetime) or not isinstance(self.expires_on, date):
            raise ValueError("expires_on must be a date")


@dataclass(frozen=True, slots=True)
class OfferSearch:
    make: str | None = None
    product_type: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "make", _require_optional_non_empty(self.make, "make"))
        object.__setattr__(self, "product_type", _require_optional_non_empty(self.product_type, "product_type"))
