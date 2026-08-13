"""Strict primitives shared by Northstar mapping modules."""

from collections.abc import Mapping
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

from webchat.domain import CustomerIdentity, Money

from ..errors import invalid_response


JsonObject = Mapping[str, Any]


def read_object(value: object) -> JsonObject:
    if not isinstance(value, Mapping):
        raise invalid_response()
    return value


def read_str(payload: JsonObject, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise invalid_response()
    return value


def read_optional_str(payload: JsonObject, key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise invalid_response()
    return value


def read_int(payload: JsonObject, key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise invalid_response()
    return value


def read_bool(payload: JsonObject, key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise invalid_response()
    return value


def read_decimal(payload: JsonObject, key: str) -> Decimal:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise invalid_response()
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise invalid_response() from error
    if not result.is_finite():
        raise invalid_response()
    return result


def read_optional_decimal(payload: JsonObject, key: str) -> Decimal | None:
    if payload.get(key) is None:
        return None
    return read_decimal(payload, key)


def read_datetime(payload: JsonObject, key: str) -> datetime:
    value = payload.get(key)
    if not isinstance(value, str):
        raise invalid_response()
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise invalid_response() from error
    if result.utcoffset() is None:
        raise invalid_response()
    return result


def read_optional_datetime(payload: JsonObject, key: str) -> datetime | None:
    if payload.get(key) is None:
        return None
    return read_datetime(payload, key)


def read_date(payload: JsonObject, key: str) -> date:
    value = payload.get(key)
    if not isinstance(value, str):
        raise invalid_response()
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise invalid_response() from error


def read_optional_time(payload: JsonObject, key: str) -> time | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise invalid_response()
    try:
        result = time.fromisoformat(value)
    except ValueError as error:
        raise invalid_response() from error
    if result.tzinfo is not None:
        raise invalid_response()
    return result


def read_items(payload: object) -> tuple[JsonObject, ...]:
    body = read_object(payload)
    items = body.get("items")
    if not isinstance(items, list):
        raise invalid_response()
    return tuple(read_object(item) for item in items)


def read_string_tuple(payload: JsonObject, key: str) -> tuple[str, ...]:
    values = payload.get(key)
    if not isinstance(values, list) or not all(isinstance(item, str) and item for item in values):
        raise invalid_response()
    return tuple(values)


def money_from_minor(payload: JsonObject, key: str, currency: str) -> Money | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise invalid_response()
    try:
        return Money(value, currency)
    except ValueError as error:
        raise invalid_response() from error


def customer_to_payload(customer: CustomerIdentity) -> dict[str, str]:
    return {
        "firstName": customer.first_name,
        "lastName": customer.last_name,
        "email": customer.email,
        "phone": customer.phone,
    }


def customer_from_payload(
    payload: JsonObject,
    *,
    first_name: str = "first_name",
    last_name: str = "last_name",
    email: str = "email",
    phone: str = "phone",
) -> CustomerIdentity:
    try:
        return CustomerIdentity(
            read_str(payload, first_name),
            read_str(payload, last_name),
            read_str(payload, email),
            read_str(payload, phone),
        )
    except ValueError as error:
        raise invalid_response() from error
