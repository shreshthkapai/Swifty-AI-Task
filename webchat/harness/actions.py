"""Harness-owned lifecycle for confirmed dealership mutations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping

from webchat.domain.common import require_aware, require_non_empty
from webchat.domain.errors import (
    DealerErrorKind,
    DealerFailure,
    FieldViolation,
)

from .contracts import (
    FrozenObject,
    freeze_json_object,
    thaw_json_object,
    validate_frozen_json_object,
)


PENDING_ACTION_SCHEMA_VERSION = 1


class PendingActionType(StrEnum):
    TEST_DRIVE_BOOKING = "test_drive_booking"
    SALES_ENQUIRY = "sales_enquiry"
    VEHICLE_INTEREST = "vehicle_interest"
    CALLBACK = "callback"
    PART_EXCHANGE = "part_exchange"
    WORKSHOP_BOOKING = "workshop_booking"
    WORKSHOP_AMENDMENT = "workshop_amendment"
    WORKSHOP_CANCELLATION = "workshop_cancellation"
    DEALERSHIP_MESSAGE = "dealership_message"


class PendingRequestType(StrEnum):
    TEST_DRIVE_BOOKING = "test_drive_booking_request"
    SALES_ENQUIRY = "sales_enquiry_request"
    VEHICLE_INTEREST = "vehicle_interest_request"
    CALLBACK = "callback_request"
    PART_EXCHANGE = "part_exchange_request"
    WORKSHOP_BOOKING = "workshop_booking_request"
    WORKSHOP_AMENDMENT = "workshop_booking_amendment"
    WORKSHOP_CANCELLATION = "workshop_cancellation_request"
    DEALERSHIP_MESSAGE = "dealership_message_request"


_REQUEST_TYPE_BY_ACTION = {
    PendingActionType.TEST_DRIVE_BOOKING: PendingRequestType.TEST_DRIVE_BOOKING,
    PendingActionType.SALES_ENQUIRY: PendingRequestType.SALES_ENQUIRY,
    PendingActionType.VEHICLE_INTEREST: PendingRequestType.VEHICLE_INTEREST,
    PendingActionType.CALLBACK: PendingRequestType.CALLBACK,
    PendingActionType.PART_EXCHANGE: PendingRequestType.PART_EXCHANGE,
    PendingActionType.WORKSHOP_BOOKING: PendingRequestType.WORKSHOP_BOOKING,
    PendingActionType.WORKSHOP_AMENDMENT: PendingRequestType.WORKSHOP_AMENDMENT,
    PendingActionType.WORKSHOP_CANCELLATION: PendingRequestType.WORKSHOP_CANCELLATION,
    PendingActionType.DEALERSHIP_MESSAGE: PendingRequestType.DEALERSHIP_MESSAGE,
}


class PendingActionState(StrEnum):
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


def _datetime_to_text(value: datetime, field: str) -> str:
    require_aware(value, field)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _datetime_from_text(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO datetime string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO datetime string") from exc
    return require_aware(parsed, field)


def _failure_to_dict(failure: DealerFailure) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": failure.kind.value,
        "retryable": failure.retryable,
        "field_violations": [
            {
                "field": violation.field,
                "code": violation.code,
                "message": violation.message,
            }
            for violation in failure.field_violations
        ],
    }
    if failure.resource is not None:
        result["resource"] = failure.resource
    return result


def _failure_from_dict(value: object) -> DealerFailure:
    if not isinstance(value, Mapping):
        raise ValueError("last_failure must be an object")
    allowed = {"kind", "retryable", "field_violations", "resource"}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unknown last_failure fields: {sorted(unknown)}")
    violations = value.get("field_violations")
    if not isinstance(violations, list):
        raise ValueError("field_violations must be an array")
    parsed_violations: list[FieldViolation] = []
    for item in violations:
        if not isinstance(item, Mapping) or set(item) != {"field", "code", "message"}:
            raise ValueError("each field violation must contain field, code, and message")
        parsed_violations.append(
            FieldViolation(
                field=item["field"],
                code=item["code"],
                message=item["message"],
            )
        )
    try:
        kind = DealerErrorKind(value.get("kind"))
    except (TypeError, ValueError) as exc:
        raise ValueError("last_failure contains an unknown kind") from exc
    retryable = value.get("retryable")
    if not isinstance(retryable, bool):
        raise ValueError("last_failure retryable must be boolean")
    resource = value.get("resource")
    if resource is not None and not isinstance(resource, str):
        raise ValueError("last_failure resource must be a string")
    return DealerFailure(
        kind=kind,
        retryable=retryable,
        field_violations=tuple(parsed_violations),
        resource=resource,
    )


@dataclass(frozen=True, slots=True)
class PendingAction:
    action_id: str
    action_type: PendingActionType
    request_type: PendingRequestType
    request_payload: FrozenObject
    state: PendingActionState
    idempotency_key: str
    created_at: datetime
    expires_at: datetime
    attempt_count: int = 0
    last_failure: DealerFailure | None = None
    schema_version: int = PENDING_ACTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != PENDING_ACTION_SCHEMA_VERSION
        ):
            raise ValueError(
                f"unsupported PendingAction schema version: {self.schema_version}"
            )
        for field in ("action_id", "idempotency_key"):
            object.__setattr__(self, field, require_non_empty(getattr(self, field), field))
        if not isinstance(self.action_type, PendingActionType):
            raise ValueError("action_type must be a PendingActionType")
        if not isinstance(self.request_type, PendingRequestType):
            raise ValueError("request_type must be a PendingRequestType")
        if _REQUEST_TYPE_BY_ACTION[self.action_type] is not self.request_type:
            raise ValueError("request_type does not match action_type")
        if not isinstance(self.state, PendingActionState):
            raise ValueError("state must be a PendingActionState")
        validate_frozen_json_object(self.request_payload, field="request_payload")
        require_aware(self.created_at, "created_at")
        require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        if type(self.attempt_count) is not int or self.attempt_count < 0:
            raise ValueError("attempt_count must be a non-negative integer")
        if self.last_failure is not None and not isinstance(
            self.last_failure,
            DealerFailure,
        ):
            raise ValueError("last_failure must be a DealerFailure or None")

    @classmethod
    def from_mapping(
        cls,
        *,
        action_id: str,
        action_type: PendingActionType,
        request_type: PendingRequestType,
        request_payload: Mapping[str, Any],
        state: PendingActionState,
        idempotency_key: str,
        created_at: datetime,
        expires_at: datetime,
        attempt_count: int = 0,
        last_failure: DealerFailure | None = None,
    ) -> PendingAction:
        return cls(
            action_id=action_id,
            action_type=action_type,
            request_type=request_type,
            request_payload=freeze_json_object(
                request_payload,
                field="request_payload",
            ),
            state=state,
            idempotency_key=idempotency_key,
            created_at=created_at,
            expires_at=expires_at,
            attempt_count=attempt_count,
            last_failure=last_failure,
        )

    def request_payload_dict(self) -> dict[str, Any]:
        return thaw_json_object(self.request_payload)

    def is_expired(self, now: datetime) -> bool:
        require_aware(now, "now")
        return now >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "action_id": self.action_id,
            "action_type": self.action_type.value,
            "request_type": self.request_type.value,
            "request_payload": self.request_payload_dict(),
            "state": self.state.value,
            "idempotency_key": self.idempotency_key,
            "created_at": _datetime_to_text(self.created_at, "created_at"),
            "expires_at": _datetime_to_text(self.expires_at, "expires_at"),
            "attempt_count": self.attempt_count,
        }
        if self.last_failure is not None:
            result["last_failure"] = _failure_to_dict(self.last_failure)
        return result

    @classmethod
    def from_dict(cls, value: object) -> PendingAction:
        if not isinstance(value, Mapping):
            raise ValueError("PendingAction must be an object")
        allowed = {
            "schema_version",
            "action_id",
            "action_type",
            "request_type",
            "request_payload",
            "state",
            "idempotency_key",
            "created_at",
            "expires_at",
            "attempt_count",
            "last_failure",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown PendingAction fields: {sorted(unknown)}")
        payload = value.get("request_payload")
        if not isinstance(payload, Mapping):
            raise ValueError("request_payload must be a JSON object")
        try:
            action_type = PendingActionType(value.get("action_type"))
            request_type = PendingRequestType(value.get("request_type"))
            state = PendingActionState(value.get("state"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "PendingAction contains an unknown type, request, or state"
            ) from exc
        last_failure_value = value.get("last_failure")
        return cls(
            schema_version=value.get("schema_version"),
            action_id=value.get("action_id"),
            action_type=action_type,
            request_type=request_type,
            request_payload=freeze_json_object(payload, field="request_payload"),
            state=state,
            idempotency_key=value.get("idempotency_key"),
            created_at=_datetime_from_text(value.get("created_at"), "created_at"),
            expires_at=_datetime_from_text(value.get("expires_at"), "expires_at"),
            attempt_count=value.get("attempt_count"),
            last_failure=(
                None
                if last_failure_value is None
                else _failure_from_dict(last_failure_value)
            ),
        )
