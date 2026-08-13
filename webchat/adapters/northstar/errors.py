"""Safe failure construction and strict Northstar error translation."""

from collections.abc import Mapping
import re

from webchat.domain import DealerError, DealerErrorKind, DealerFailure, FieldViolation


_ERROR_KINDS = {
    "NOT_FOUND": DealerErrorKind.NOT_FOUND,
    "BOOKING_NOT_FOUND": DealerErrorKind.VERIFICATION_FAILED,
    "SLOT_UNAVAILABLE": DealerErrorKind.SLOT_UNAVAILABLE,
    "VEHICLE_RESERVED": DealerErrorKind.VEHICLE_RESERVED,
    "VEHICLE_UNAVAILABLE": DealerErrorKind.VEHICLE_UNAVAILABLE,
    "VEHICLE_NOT_RESERVED": DealerErrorKind.VEHICLE_NOT_RESERVED,
    "BOOKING_CANCELLED": DealerErrorKind.BOOKING_CANCELLED,
    "IDEMPOTENCY_CONFLICT": DealerErrorKind.IDEMPOTENCY_CONFLICT,
    "UNAUTHORISED": DealerErrorKind.AUTHENTICATION_FAILED,
    "IDEMPOTENCY_KEY_REQUIRED": DealerErrorKind.INVALID_REQUEST,
    "INVALID_IDEMPOTENCY_KEY": DealerErrorKind.INVALID_REQUEST,
    "INVALID_JSON": DealerErrorKind.INVALID_REQUEST,
    "REQUEST_TOO_LARGE": DealerErrorKind.INVALID_REQUEST,
}
_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def invalid_request(*, resource: str | None = None) -> DealerError:
    """Return a safe local incompatibility failure."""
    return DealerError(DealerFailure(DealerErrorKind.INVALID_REQUEST, resource=resource))


def invalid_response(*, resource: str | None = None) -> DealerError:
    """Return a safe platform-contract failure."""
    return DealerError(DealerFailure(DealerErrorKind.INVALID_RESPONSE, resource=resource))


def temporary_failure(*, resource: str | None = None) -> DealerError:
    """Return a safe retryable transport/platform failure."""
    return DealerError(
        DealerFailure(
            DealerErrorKind.TEMPORARY_FAILURE,
            retryable=True,
            resource=resource,
        )
    )


def map_platform_error(
    status: int,
    payload: object,
    *,
    resource: str | None = None,
    field_map: Mapping[str, str] | None = None,
) -> DealerError:
    """Translate a validated Northstar error envelope into stable semantics."""
    if not isinstance(status, int) or isinstance(status, bool):
        return invalid_response(resource=resource)
    if not isinstance(payload, dict) or not isinstance(payload.get("error"), dict):
        return invalid_response(resource=resource)

    envelope = payload["error"]
    code = envelope.get("code")
    message = envelope.get("message")
    field_errors = envelope.get("fieldErrors")
    retryable = envelope.get("retryable")
    if (
        not isinstance(code, str)
        or not code
        or not isinstance(message, str)
        or not message
        or not isinstance(field_errors, dict)
        or not isinstance(retryable, bool)
        or not all(
            isinstance(field, str)
            and bool(field)
            and isinstance(field_message, str)
            and bool(field_message)
            for field, field_message in field_errors.items()
        )
    ):
        return invalid_response(resource=resource)

    if code == "INTERNAL_ERROR":
        if not retryable:
            return invalid_response(resource=resource)
        return temporary_failure(resource=resource)
    if retryable:
        return invalid_response(resource=resource)

    if code == "VALIDATION_ERROR":
        names = {} if field_map is None else field_map
        violations = tuple(
            FieldViolation(
                field=names.get(field, _camel_to_snake(field)),
                code="invalid",
                message=field_message,
            )
            for field, field_message in field_errors.items()
        )
        return DealerError(
            DealerFailure(
                DealerErrorKind.VALIDATION,
                field_violations=violations,
                resource=resource,
            )
        )

    kind = _ERROR_KINDS.get(code)
    if kind is None:
        return invalid_response(resource=resource)
    return DealerError(DealerFailure(kind, resource=resource))


def _camel_to_snake(value: str) -> str:
    return _CAMEL_BOUNDARY.sub("_", value).lower()
