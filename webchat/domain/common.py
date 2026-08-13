"""Shared immutable value objects for the dealership domain."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re
from typing import Generic, TypeVar


T = TypeVar("T")
_CURRENCY_PATTERN = re.compile(r"[A-Z]{3}")


def require_non_empty(value: str, field: str) -> str:
    """Return a stripped non-empty string or raise a validation error."""
    if not isinstance(value, str) or not (cleaned_value := value.strip()):
        raise ValueError(f"{field} must be a non-empty string")
    return cleaned_value


def require_aware(value: datetime, field: str) -> datetime:
    """Return a timezone-aware datetime or raise a validation error."""
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{field} must be a timezone-aware datetime")
    return value


@dataclass(frozen=True, slots=True)
class Money:
    amount_minor: int
    currency: str

    def __post_init__(self) -> None:
        if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int):
            raise ValueError("amount_minor must be a non-boolean integer")
        if self.amount_minor < 0:
            raise ValueError("amount_minor must be at least zero")
        if not isinstance(self.currency, str) or not _CURRENCY_PATTERN.fullmatch(self.currency):
            raise ValueError("currency must be an ISO 4217 code")


@dataclass(frozen=True, slots=True)
class CustomerIdentity:
    first_name: str
    last_name: str
    email: str
    phone: str

    def __post_init__(self) -> None:
        for field in ("first_name", "last_name", "email", "phone"):
            object.__setattr__(self, field, require_non_empty(getattr(self, field), field))


@dataclass(frozen=True, slots=True)
class Address:
    lines: tuple[str, ...]
    town: str
    postcode: str
    country: str

    def __post_init__(self) -> None:
        if not isinstance(self.lines, tuple) or not self.lines:
            raise ValueError("lines must contain at least one address line")
        object.__setattr__(
            self,
            "lines",
            tuple(require_non_empty(line, "lines") for line in self.lines),
        )
        for field in ("town", "postcode", "country"):
            object.__setattr__(self, field, require_non_empty(getattr(self, field), field))


class Department(StrEnum):
    SALES = "sales"
    SERVICE = "service"
    PARTS = "parts"
    GENERAL = "general"


class BookingStatus(StrEnum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class ReceivedStatus(StrEnum):
    RECEIVED = "received"


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    page: int
    page_size: int
    total_items: int
    total_pages: int

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple):
            raise ValueError("items must be a tuple")
        for field in ("page", "page_size", "total_items", "total_pages"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{field} must be an integer")
        if self.page <= 0 or self.page_size <= 0:
            raise ValueError("page and page_size must be positive")
        if self.total_items < 0 or self.total_pages < 0:
            raise ValueError("total_items and total_pages must be non-negative")
