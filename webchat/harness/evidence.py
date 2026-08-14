"""Immutable, provider- and dealer-neutral evidence contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
import hashlib
import json
import math
import re
from typing import Any, TypeAlias


EVIDENCE_ENVELOPE_SCHEMA_VERSION = 1
_SOURCE_OPERATION = re.compile(r"^[a-z][a-z0-9_]*$")
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_.\[\]-]*$")
_EVIDENCE_ID = re.compile(r"^(?:ev|gap)_[0-9a-f]{24}$")

EvidenceValue: TypeAlias = bool | int | float | str


class EvidenceAuthority(StrEnum):
    """Owner of the asserted value, independent of transport or model provider."""

    DEALER = "dealer"
    CUSTOMER = "customer"
    PAGE_CONTEXT = "page_context"
    HARNESS = "harness"


class EvidenceFreshness(StrEnum):
    """How current a value is for the turn being answered."""

    LIVE = "live"
    SNAPSHOT = "snapshot"
    STABLE = "stable"


class EvidenceGapReason(StrEnum):
    """Machine-readable reason a requested field cannot be evidenced."""

    NOT_PUBLISHED = "not_published"
    NOT_RETURNED = "not_returned"
    NOT_APPLICABLE = "not_applicable"
    REDACTED = "redacted"


def _require_token(value: object, field: str, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be a non-empty normalized string")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise ValueError(f"{field} has an unsupported format")
    return value


def _require_aware(value: object, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be a timezone-aware datetime")
    return value


def _validate_value(value: object) -> EvidenceValue:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("value must be finite")
        return value
    if isinstance(value, str) and value:
        return value
    raise ValueError("value must be a non-empty JSON scalar")


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(canonical).hexdigest()[:24]}"


def _identity_payload(
    *,
    source_operation: str,
    entity_id: str,
    field_name: str,
    authority: EvidenceAuthority,
    freshness: EvidenceFreshness,
    value: EvidenceValue | None = None,
    reason: EvidenceGapReason | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source_operation": source_operation,
        "entity_id": entity_id,
        "field_name": field_name,
        "authority": authority.value,
        "freshness": freshness.value,
    }
    if reason is None:
        payload["value"] = value
    else:
        payload["reason"] = reason.value
    return payload


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    source_operation: str
    entity_id: str
    field_name: str
    value: EvidenceValue
    authority: EvidenceAuthority
    freshness: EvidenceFreshness
    observed_at: datetime
    evidence_id: str = field(init=False)

    def __post_init__(self) -> None:
        _require_token(self.source_operation, "source_operation", _SOURCE_OPERATION)
        _require_token(self.entity_id, "entity_id")
        _require_token(self.field_name, "field_name", _FIELD_NAME)
        _validate_value(self.value)
        if not isinstance(self.authority, EvidenceAuthority):
            raise ValueError("authority must be an EvidenceAuthority")
        if not isinstance(self.freshness, EvidenceFreshness):
            raise ValueError("freshness must be an EvidenceFreshness")
        _require_aware(self.observed_at, "observed_at")
        object.__setattr__(self, "evidence_id", _stable_id(
            "ev",
            _identity_payload(
                source_operation=self.source_operation,
                entity_id=self.entity_id,
                field_name=self.field_name,
                value=self.value,
                authority=self.authority,
                freshness=self.freshness,
            ),
        ))

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_operation": self.source_operation,
            "entity_id": self.entity_id,
            "field_name": self.field_name,
            "value": self.value,
            "authority": self.authority.value,
            "freshness": self.freshness.value,
            "observed_at": self.observed_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class EvidenceGap:
    source_operation: str
    entity_id: str
    field_name: str
    reason: EvidenceGapReason
    authority: EvidenceAuthority
    freshness: EvidenceFreshness
    observed_at: datetime
    evidence_id: str = field(init=False)

    def __post_init__(self) -> None:
        _require_token(self.source_operation, "source_operation", _SOURCE_OPERATION)
        _require_token(self.entity_id, "entity_id")
        _require_token(self.field_name, "field_name", _FIELD_NAME)
        if not isinstance(self.reason, EvidenceGapReason):
            raise ValueError("reason must be an EvidenceGapReason")
        if not isinstance(self.authority, EvidenceAuthority):
            raise ValueError("authority must be an EvidenceAuthority")
        if not isinstance(self.freshness, EvidenceFreshness):
            raise ValueError("freshness must be an EvidenceFreshness")
        _require_aware(self.observed_at, "observed_at")
        object.__setattr__(self, "evidence_id", _stable_id(
            "gap",
            _identity_payload(
                source_operation=self.source_operation,
                entity_id=self.entity_id,
                field_name=self.field_name,
                reason=self.reason,
                authority=self.authority,
                freshness=self.freshness,
            ),
        ))

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_operation": self.source_operation,
            "entity_id": self.entity_id,
            "field_name": self.field_name,
            "reason": self.reason.value,
            "authority": self.authority.value,
            "freshness": self.freshness.value,
            "observed_at": self.observed_at.isoformat(),
        }


EvidenceRecord: TypeAlias = EvidenceItem | EvidenceGap


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    evidence_id: str

    def __post_init__(self) -> None:
        _require_token(self.evidence_id, "evidence_id", _EVIDENCE_ID)

    def to_dict(self) -> dict[str, str]:
        return {"evidence_id": self.evidence_id}


@dataclass(frozen=True, slots=True)
class EvidenceEnvelope:
    items: tuple[EvidenceItem, ...]
    generated_at: datetime
    gaps: tuple[EvidenceGap, ...] = ()
    schema_version: int = EVIDENCE_ENVELOPE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != EVIDENCE_ENVELOPE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported EvidenceEnvelope schema version: {self.schema_version}"
            )
        if not isinstance(self.items, tuple) or not all(
            isinstance(item, EvidenceItem) for item in self.items
        ):
            raise ValueError("items must be a tuple of EvidenceItem values")
        if not isinstance(self.gaps, tuple) or not all(
            isinstance(item, EvidenceGap) for item in self.gaps
        ):
            raise ValueError("gaps must be a tuple of EvidenceGap values")
        _require_aware(self.generated_at, "generated_at")
        records: tuple[EvidenceRecord, ...] = self.items + self.gaps
        ids = tuple(record.evidence_id for record in records)
        if len(ids) != len(set(ids)):
            raise ValueError("evidence IDs must be unique within an envelope")
        object.__setattr__(
            self,
            "items",
            tuple(sorted(self.items, key=lambda item: item.evidence_id)),
        )
        object.__setattr__(
            self,
            "gaps",
            tuple(sorted(self.gaps, key=lambda item: item.evidence_id)),
        )

    def resolve(self, reference: EvidenceReference) -> EvidenceRecord:
        if not isinstance(reference, EvidenceReference):
            raise ValueError("reference must be an EvidenceReference")
        for record in self.items + self.gaps:
            if record.evidence_id == reference.evidence_id:
                return record
        raise ValueError(f"unknown evidence reference: {reference.evidence_id}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at.isoformat(),
            "items": [item.to_dict() for item in self.items],
            "gaps": [gap.to_dict() for gap in self.gaps],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
