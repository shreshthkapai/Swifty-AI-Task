"""Stable machine-readable failures raised by dealer adapters."""

from dataclasses import dataclass
from enum import StrEnum

from .common import require_non_empty


class DealerErrorKind(StrEnum):
    VALIDATION = "validation"
    NOT_FOUND = "not_found"
    VERIFICATION_FAILED = "verification_failed"
    SLOT_UNAVAILABLE = "slot_unavailable"
    VEHICLE_RESERVED = "vehicle_reserved"
    VEHICLE_UNAVAILABLE = "vehicle_unavailable"
    VEHICLE_NOT_RESERVED = "vehicle_not_reserved"
    BOOKING_CANCELLED = "booking_cancelled"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    AUTHENTICATION_FAILED = "authentication_failed"
    INVALID_REQUEST = "invalid_request"
    TEMPORARY_FAILURE = "temporary_failure"
    INVALID_RESPONSE = "invalid_response"


@dataclass(frozen=True, slots=True)
class FieldViolation:
    field: str
    code: str
    message: str

    def __post_init__(self) -> None:
        for name in ("field", "code", "message"):
            object.__setattr__(self, name, require_non_empty(getattr(self, name), name))


@dataclass(frozen=True, slots=True)
class DealerFailure:
    kind: DealerErrorKind
    retryable: bool = False
    field_violations: tuple[FieldViolation, ...] = ()
    resource: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, DealerErrorKind):
            raise ValueError("kind must be a DealerErrorKind")
        if not isinstance(self.retryable, bool):
            raise ValueError("retryable must be boolean")
        if self.retryable != (self.kind is DealerErrorKind.TEMPORARY_FAILURE):
            raise ValueError("only temporary failures are retryable")
        if not isinstance(self.field_violations, tuple) or not all(
            isinstance(item, FieldViolation) for item in self.field_violations
        ):
            raise ValueError("field_violations must be a tuple of FieldViolation values")
        if self.resource is not None:
            object.__setattr__(self, "resource", require_non_empty(self.resource, "resource"))


class DealerError(Exception):
    """Exception wrapper exposing only stable, safe dealership failure data."""

    __slots__ = ("_failure",)

    def __init__(self, failure: DealerFailure) -> None:
        if not isinstance(failure, DealerFailure):
            raise TypeError("failure must be a DealerFailure")
        self._failure = failure
        message = failure.kind.value
        if failure.resource is not None:
            message = f"{message}: {failure.resource}"
        super().__init__(message)

    @property
    def failure(self) -> DealerFailure:
        return self._failure

    @property
    def kind(self) -> DealerErrorKind:
        return self._failure.kind

    @property
    def retryable(self) -> bool:
        return self._failure.retryable

    @property
    def field_violations(self) -> tuple[FieldViolation, ...]:
        return self._failure.field_violations

    @property
    def resource(self) -> str | None:
        return self._failure.resource
