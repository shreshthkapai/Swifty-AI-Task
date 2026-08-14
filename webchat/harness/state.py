"""Versioned immutable state for one anonymous browser conversation."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import json
from typing import Any, Mapping

from webchat.domain.common import require_aware, require_non_empty

from .actions import PendingAction
from .contracts import (
    FrozenObject,
    freeze_json_object,
    thaw_json_object,
    validate_frozen_json_object,
)


CONVERSATION_STATE_SCHEMA_VERSION = 1
STRUCTURED_MESSAGE_SCHEMA_VERSION = 1
MAX_PRESENTATION_GROUPS = 5
DEFAULT_VERIFICATION_TTL = timedelta(minutes=15)
PAGE_SEARCH_FILTER_FIELDS = frozenset(
    {"query", "make", "body_style", "fuel_type", "max_price_minor", "sort"}
)


def _optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    return require_non_empty(value, field_name)


def _datetime_to_text(value: datetime, field_name: str) -> str:
    require_aware(value, field_name)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _datetime_from_text(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO datetime string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO datetime string") from exc
    return require_aware(parsed, field_name)


def _require_mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return value


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unknown {name} fields: {sorted(unknown)}")


def _parse_tuple(value: object, field_name: str) -> tuple[Any, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class CustomerState:
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    registration: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("first_name", "last_name", "email", "phone", "registration"):
            object.__setattr__(
                self,
                field_name,
                _optional_text(getattr(self, field_name), field_name),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            name: value
            for name in ("first_name", "last_name", "email", "phone", "registration")
            if (value := getattr(self, name)) is not None
        }

    @classmethod
    def from_dict(cls, value: object) -> CustomerState:
        data = _require_mapping(value, "customer")
        allowed = {"first_name", "last_name", "email", "phone", "registration"}
        _reject_unknown(data, allowed, "customer")
        return cls(**{name: data.get(name) for name in allowed})


@dataclass(frozen=True, slots=True)
class PageContext:
    current_url: str | None = None
    page_vehicle_id: str | None = None
    search_filters: FrozenObject | Mapping[str, Any] = field(default_factory=dict)
    observed_at: datetime | None = None
    is_authoritative: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "current_url",
            "page_vehicle_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _optional_text(getattr(self, field_name), field_name),
            )
        if isinstance(self.search_filters, Mapping):
            object.__setattr__(
                self,
                "search_filters",
                freeze_json_object(self.search_filters, field="search_filters"),
            )
        elif not isinstance(self.search_filters, FrozenObject):
            raise ValueError("search_filters must be a JSON object")
        validate_frozen_json_object(self.search_filters, field="search_filters")
        filters = self.search_filters_dict()
        if set(filters) - PAGE_SEARCH_FILTER_FIELDS:
            raise ValueError("search_filters contains an unknown field")
        for name, value in filters.items():
            if name == "max_price_minor":
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not 0 <= value <= 1_000_000_000
                ):
                    raise ValueError(
                        "search_filters max_price_minor must be a non-negative integer"
                    )
            elif not isinstance(value, str) or not value.strip() or len(value) > 100:
                raise ValueError(f"search_filters {name} must be a non-empty string")
        if self.observed_at is not None:
            require_aware(self.observed_at, "observed_at")
        if self.is_authoritative is not False:
            raise ValueError("page context must remain non-authoritative")

    def to_dict(self) -> dict[str, Any]:
        result = {
            name: value
            for name in (
                "current_url",
                "page_vehicle_id",
            )
            if (value := getattr(self, name)) is not None
        }
        if self.observed_at is not None:
            result["observed_at"] = _datetime_to_text(self.observed_at, "observed_at")
        if filters := self.search_filters_dict():
            result["search_filters"] = filters
        result["is_authoritative"] = False
        return result

    def search_filters_dict(self) -> dict[str, Any]:
        return thaw_json_object(self.search_filters)

    @classmethod
    def from_dict(cls, value: object) -> PageContext:
        data = _require_mapping(value, "context")
        allowed = {
            "current_url",
            "page_vehicle_id",
            "search_filters",
            "observed_at",
            "is_authoritative",
        }
        _reject_unknown(data, allowed, "context")
        observed = data.get("observed_at")
        return cls(
            current_url=data.get("current_url"),
            page_vehicle_id=data.get("page_vehicle_id"),
            search_filters=data.get("search_filters", {}),
            observed_at=(
                None
                if observed is None
                else _datetime_from_text(observed, "observed_at")
            ),
            is_authoritative=data.get("is_authoritative", False),
        )


@dataclass(frozen=True, slots=True)
class EntityContext:
    selected_vehicle_id: str | None = None
    selected_dealer_id: str | None = None
    selected_test_drive_slot_id: str | None = None
    selected_workshop_slot_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "selected_vehicle_id",
            "selected_dealer_id",
            "selected_test_drive_slot_id",
            "selected_workshop_slot_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _optional_text(getattr(self, field_name), field_name),
            )

    def to_dict(self) -> dict[str, str]:
        return {
            name: value
            for name in (
                "selected_vehicle_id",
                "selected_dealer_id",
                "selected_test_drive_slot_id",
                "selected_workshop_slot_id",
            )
            if (value := getattr(self, name)) is not None
        }

    @classmethod
    def from_dict(cls, value: object) -> EntityContext:
        data = _require_mapping(value, "entities")
        allowed = {
            "selected_vehicle_id",
            "selected_dealer_id",
            "selected_test_drive_slot_id",
            "selected_workshop_slot_id",
        }
        _reject_unknown(data, allowed, "entities")
        return cls(**{name: data.get(name) for name in allowed})


@dataclass(frozen=True, slots=True)
class VehiclePreferences:
    minimum_price_minor: int | None = None
    maximum_price_minor: int | None = None
    currency: str | None = None
    makes: tuple[str, ...] = ()
    models: tuple[str, ...] = ()
    fuel: str | None = None
    transmission: str | None = None
    body_type: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("minimum_price_minor", "maximum_price_minor"):
            value = getattr(self, field_name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{field_name} must be a non-negative integer or None")
        if (
            self.minimum_price_minor is not None
            and self.maximum_price_minor is not None
            and self.minimum_price_minor > self.maximum_price_minor
        ):
            raise ValueError("minimum_price_minor must not exceed maximum_price_minor")
        if self.currency is not None:
            cleaned_currency = require_non_empty(self.currency, "currency").upper()
            if (
                len(cleaned_currency) != 3
                or not cleaned_currency.isascii()
                or not cleaned_currency.isalpha()
            ):
                raise ValueError("currency must be an ISO 4217 code")
            object.__setattr__(self, "currency", cleaned_currency)
        for field_name in ("makes", "models"):
            values = getattr(self, field_name)
            if not isinstance(values, tuple):
                raise ValueError(f"{field_name} must be a tuple")
            cleaned = tuple(require_non_empty(item, field_name) for item in values)
            if len(set(cleaned)) != len(cleaned):
                raise ValueError(f"{field_name} cannot contain duplicates")
            object.__setattr__(self, field_name, cleaned)
        for field_name in ("fuel", "transmission", "body_type"):
            object.__setattr__(
                self,
                field_name,
                _optional_text(getattr(self, field_name), field_name),
            )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name in (
            "minimum_price_minor",
            "maximum_price_minor",
            "currency",
            "fuel",
            "transmission",
            "body_type",
        ):
            if (value := getattr(self, name)) is not None:
                result[name] = value
        result["makes"] = list(self.makes)
        result["models"] = list(self.models)
        return result

    @classmethod
    def from_dict(cls, value: object) -> VehiclePreferences:
        data = _require_mapping(value, "preferences")
        allowed = {
            "minimum_price_minor",
            "maximum_price_minor",
            "currency",
            "makes",
            "models",
            "fuel",
            "transmission",
            "body_type",
        }
        _reject_unknown(data, allowed, "preferences")
        return cls(
            minimum_price_minor=data.get("minimum_price_minor"),
            maximum_price_minor=data.get("maximum_price_minor"),
            currency=data.get("currency"),
            makes=_parse_tuple(data.get("makes", []), "makes"),
            models=_parse_tuple(data.get("models", []), "models"),
            fuel=data.get("fuel"),
            transmission=data.get("transmission"),
            body_type=data.get("body_type"),
        )


class WorkflowDomain(StrEnum):
    NONE = "none"
    VEHICLES = "vehicles"
    SALES = "sales"
    TEST_DRIVE = "test_drive"
    WORKSHOP = "workshop"
    DEALERSHIP = "dealership"


class WorkflowStage(StrEnum):
    IDLE = "idle"
    DISCOVERY = "discovery"
    REFINING = "refining"
    SELECTING_ENTITY = "selecting_entity"
    SELECTING_SLOT = "selecting_slot"
    COLLECTING_DETAILS = "collecting_details"
    VERIFYING_IDENTITY = "verifying_identity"
    REVIEWING_ACTION = "reviewing_action"
    ACTION_FAILED = "action_failed"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class WorkflowState:
    domain: WorkflowDomain = WorkflowDomain.NONE
    stage: WorkflowStage = WorkflowStage.IDLE
    gathered_fields: FrozenObject | Mapping[str, Any] = field(default_factory=dict)
    missing_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.domain, WorkflowDomain):
            raise ValueError("domain must be a WorkflowDomain")
        if not isinstance(self.stage, WorkflowStage):
            raise ValueError("stage must be a WorkflowStage")
        if isinstance(self.gathered_fields, Mapping):
            object.__setattr__(
                self,
                "gathered_fields",
                freeze_json_object(self.gathered_fields, field="gathered_fields"),
            )
        elif not isinstance(self.gathered_fields, FrozenObject):
            raise ValueError("gathered_fields must be a JSON object")
        validate_frozen_json_object(self.gathered_fields, field="gathered_fields")
        if not isinstance(self.missing_fields, tuple):
            raise ValueError("missing_fields must be a tuple")
        cleaned = tuple(require_non_empty(item, "missing_fields") for item in self.missing_fields)
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("missing_fields cannot contain duplicates")
        object.__setattr__(self, "missing_fields", cleaned)

    def gathered_fields_dict(self) -> dict[str, Any]:
        return thaw_json_object(self.gathered_fields)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain.value,
            "stage": self.stage.value,
            "gathered_fields": self.gathered_fields_dict(),
            "missing_fields": list(self.missing_fields),
        }

    @classmethod
    def from_dict(cls, value: object) -> WorkflowState:
        data = _require_mapping(value, "workflow")
        allowed = {"domain", "stage", "gathered_fields", "missing_fields"}
        _reject_unknown(data, allowed, "workflow")
        try:
            domain = WorkflowDomain(data.get("domain"))
            stage = WorkflowStage(data.get("stage"))
        except (TypeError, ValueError) as exc:
            raise ValueError("workflow contains an unknown domain or stage") from exc
        return cls(
            domain=domain,
            stage=stage,
            gathered_fields=_require_mapping(data.get("gathered_fields"), "gathered_fields"),
            missing_fields=_parse_tuple(data.get("missing_fields"), "missing_fields"),
        )


class PresentationProvenance(StrEnum):
    DEALER_API = "dealer_api"
    PAGE_CONTEXT = "page_context"
    HARNESS = "harness"


@dataclass(frozen=True, slots=True)
class PresentedEntity:
    entity_id: str
    ordinal: int
    snapshot: FrozenObject | Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "entity_id", require_non_empty(self.entity_id, "entity_id"))
        if type(self.ordinal) is not int or self.ordinal < 1:
            raise ValueError("ordinal must be a positive integer")
        if isinstance(self.snapshot, Mapping):
            object.__setattr__(
                self,
                "snapshot",
                freeze_json_object(self.snapshot, field="snapshot"),
            )
        elif not isinstance(self.snapshot, FrozenObject):
            raise ValueError("snapshot must be a JSON object")
        validate_frozen_json_object(self.snapshot, field="snapshot")

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "ordinal": self.ordinal,
            "snapshot": thaw_json_object(self.snapshot),
        }

    @classmethod
    def from_dict(cls, value: object) -> PresentedEntity:
        data = _require_mapping(value, "presented entity")
        _reject_unknown(data, {"entity_id", "ordinal", "snapshot"}, "presented entity")
        return cls(
            entity_id=data.get("entity_id"),
            ordinal=data.get("ordinal"),
            snapshot=_require_mapping(data.get("snapshot"), "snapshot"),
        )


@dataclass(frozen=True, slots=True)
class PresentationGroup:
    group_id: str
    entity_type: str
    entities: tuple[PresentedEntity, ...]
    snapshot_at: datetime
    provenance: PresentationProvenance
    is_authoritative: bool = False

    def __post_init__(self) -> None:
        for field_name in ("group_id", "entity_type"):
            object.__setattr__(
                self,
                field_name,
                require_non_empty(getattr(self, field_name), field_name),
            )
        if not isinstance(self.entities, tuple) or not self.entities:
            raise ValueError("entities must be a non-empty tuple")
        if not all(isinstance(entity, PresentedEntity) for entity in self.entities):
            raise ValueError("entities must contain PresentedEntity values")
        expected_ordinals = tuple(range(1, len(self.entities) + 1))
        if tuple(entity.ordinal for entity in self.entities) != expected_ordinals:
            raise ValueError("entity ordinals must be contiguous in display order")
        if len({entity.entity_id for entity in self.entities}) != len(self.entities):
            raise ValueError("presentation entity IDs must be unique")
        require_aware(self.snapshot_at, "snapshot_at")
        if not isinstance(self.provenance, PresentationProvenance):
            raise ValueError("provenance must be a PresentationProvenance")
        if self.is_authoritative is not False:
            raise ValueError("presentation snapshots must remain non-authoritative")

    def resolve_ordinal(self, ordinal: int) -> PresentedEntity | None:
        if type(ordinal) is not int or ordinal < 1:
            return None
        if ordinal > len(self.entities):
            return None
        return self.entities[ordinal - 1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "entity_type": self.entity_type,
            "entities": [entity.to_dict() for entity in self.entities],
            "snapshot_at": _datetime_to_text(self.snapshot_at, "snapshot_at"),
            "provenance": self.provenance.value,
            "is_authoritative": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> PresentationGroup:
        data = _require_mapping(value, "presentation group")
        allowed = {
            "group_id",
            "entity_type",
            "entities",
            "snapshot_at",
            "provenance",
            "is_authoritative",
        }
        _reject_unknown(data, allowed, "presentation group")
        try:
            provenance = PresentationProvenance(data.get("provenance"))
        except (TypeError, ValueError) as exc:
            raise ValueError("presentation group contains unknown provenance") from exc
        return cls(
            group_id=data.get("group_id"),
            entity_type=data.get("entity_type"),
            entities=tuple(
                PresentedEntity.from_dict(item)
                for item in _parse_tuple(data.get("entities"), "entities")
            ),
            snapshot_at=_datetime_from_text(data.get("snapshot_at"), "snapshot_at"),
            provenance=provenance,
            is_authoritative=data.get("is_authoritative", False),
        )


@dataclass(frozen=True, slots=True)
class VerificationGrant:
    booking_id: str
    granted_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "booking_id", require_non_empty(self.booking_id, "booking_id"))
        require_aware(self.granted_at, "granted_at")
        require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.granted_at:
            raise ValueError("expires_at must be after granted_at")

    @classmethod
    def issue(
        cls,
        booking_id: str,
        *,
        now: datetime,
        ttl: timedelta = DEFAULT_VERIFICATION_TTL,
    ) -> VerificationGrant:
        require_aware(now, "now")
        if not isinstance(ttl, timedelta) or ttl <= timedelta(0):
            raise ValueError("ttl must be a positive timedelta")
        return cls(booking_id=booking_id, granted_at=now, expires_at=now + ttl)

    def is_active(self, now: datetime) -> bool:
        require_aware(now, "now")
        return self.granted_at <= now < self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "booking_id": self.booking_id,
            "granted_at": _datetime_to_text(self.granted_at, "granted_at"),
            "expires_at": _datetime_to_text(self.expires_at, "expires_at"),
        }

    @classmethod
    def from_dict(cls, value: object) -> VerificationGrant:
        data = _require_mapping(value, "verification grant")
        _reject_unknown(data, {"booking_id", "granted_at", "expires_at"}, "verification grant")
        return cls(
            booking_id=data.get("booking_id"),
            granted_at=_datetime_from_text(data.get("granted_at"), "granted_at"),
            expires_at=_datetime_from_text(data.get("expires_at"), "expires_at"),
        )


@dataclass(frozen=True, slots=True)
class ConversationState:
    customer: CustomerState = field(default_factory=CustomerState)
    context: PageContext = field(default_factory=PageContext)
    entities: EntityContext = field(default_factory=EntityContext)
    preferences: VehiclePreferences = field(default_factory=VehiclePreferences)
    workflow: WorkflowState = field(default_factory=WorkflowState)
    pending_action: PendingAction | None = None
    verification_grants: tuple[VerificationGrant, ...] = ()
    presentation_groups: tuple[PresentationGroup, ...] = ()
    schema_version: int = CONVERSATION_STATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != CONVERSATION_STATE_SCHEMA_VERSION
        ):
            raise ValueError(
                f"unsupported ConversationState schema version: {self.schema_version}"
            )
        for field_name, expected in (
            ("customer", CustomerState),
            ("context", PageContext),
            ("entities", EntityContext),
            ("preferences", VehiclePreferences),
            ("workflow", WorkflowState),
        ):
            if not isinstance(getattr(self, field_name), expected):
                raise ValueError(f"{field_name} must be a {expected.__name__}")
        if self.pending_action is not None and not isinstance(self.pending_action, PendingAction):
            raise ValueError("pending_action must be a PendingAction or None")
        if not isinstance(self.verification_grants, tuple) or not all(
            isinstance(grant, VerificationGrant) for grant in self.verification_grants
        ):
            raise ValueError("verification_grants must be a tuple of VerificationGrant values")
        grant_ids = {grant.booking_id for grant in self.verification_grants}
        if len(grant_ids) != len(self.verification_grants):
            raise ValueError("verification_grants must be unique by booking ID")
        if not isinstance(self.presentation_groups, tuple) or not all(
            isinstance(group, PresentationGroup) for group in self.presentation_groups
        ):
            raise ValueError("presentation_groups must be a tuple of PresentationGroup values")
        if len(self.presentation_groups) > MAX_PRESENTATION_GROUPS:
            raise ValueError(f"presentation_groups cannot exceed {MAX_PRESENTATION_GROUPS}")
        group_ids = {group.group_id for group in self.presentation_groups}
        if len(group_ids) != len(self.presentation_groups):
            raise ValueError("presentation group IDs must be unique")

    def with_presentation_group(self, group: PresentationGroup) -> ConversationState:
        if not isinstance(group, PresentationGroup):
            raise ValueError("group must be a PresentationGroup")
        existing = tuple(
            item
            for item in self.presentation_groups
            if item.group_id != group.group_id
        )
        return replace(
            self,
            presentation_groups=(existing + (group,))[-MAX_PRESENTATION_GROUPS:],
        )

    def has_active_grant(self, booking_id: str, *, now: datetime) -> bool:
        cleaned_id = require_non_empty(booking_id, "booking_id")
        require_aware(now, "now")
        return any(
            grant.booking_id == cleaned_id and grant.is_active(now)
            for grant in self.verification_grants
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "customer": self.customer.to_dict(),
            "context": self.context.to_dict(),
            "entities": self.entities.to_dict(),
            "preferences": self.preferences.to_dict(),
            "workflow": self.workflow.to_dict(),
            "pending_action": (
                None if self.pending_action is None else self.pending_action.to_dict()
            ),
            "verification_grants": [grant.to_dict() for grant in self.verification_grants],
            "presentation_groups": [group.to_dict() for group in self.presentation_groups],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_dict(cls, value: object) -> ConversationState:
        data = _require_mapping(value, "ConversationState")
        allowed = {
            "schema_version",
            "customer",
            "context",
            "entities",
            "preferences",
            "workflow",
            "pending_action",
            "verification_grants",
            "presentation_groups",
        }
        _reject_unknown(data, allowed, "ConversationState")
        version = data.get("schema_version")
        if type(version) is not int or version != CONVERSATION_STATE_SCHEMA_VERSION:
            raise ValueError(f"unsupported ConversationState schema version: {version}")
        pending = data.get("pending_action")
        return cls(
            schema_version=version,
            customer=CustomerState.from_dict(data.get("customer")),
            context=PageContext.from_dict(data.get("context")),
            entities=EntityContext.from_dict(data.get("entities")),
            preferences=VehiclePreferences.from_dict(data.get("preferences")),
            workflow=WorkflowState.from_dict(data.get("workflow")),
            pending_action=None if pending is None else PendingAction.from_dict(pending),
            verification_grants=tuple(
                VerificationGrant.from_dict(item)
                for item in _parse_tuple(data.get("verification_grants"), "verification_grants")
            ),
            presentation_groups=tuple(
                PresentationGroup.from_dict(item)
                for item in _parse_tuple(data.get("presentation_groups"), "presentation_groups")
            ),
        )

    @classmethod
    def from_json(cls, value: str) -> ConversationState:
        if not isinstance(value, str):
            raise ValueError("ConversationState JSON must be a string")
        try:
            data = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("ConversationState contains invalid JSON") from exc
        return cls.from_dict(data)


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class EntityReference:
    entity_type: str
    entity_id: str

    def __post_init__(self) -> None:
        for field_name in ("entity_type", "entity_id"):
            object.__setattr__(
                self,
                field_name,
                require_non_empty(getattr(self, field_name), field_name),
            )

    def to_dict(self) -> dict[str, str]:
        return {"entity_type": self.entity_type, "entity_id": self.entity_id}

    @classmethod
    def from_dict(cls, value: object) -> EntityReference:
        data = _require_mapping(value, "entity reference")
        _reject_unknown(data, {"entity_type", "entity_id"}, "entity reference")
        return cls(entity_type=data.get("entity_type"), entity_id=data.get("entity_id"))


@dataclass(frozen=True, slots=True)
class ActionReference:
    action_id: str
    action_type: str

    def __post_init__(self) -> None:
        for field_name in ("action_id", "action_type"):
            object.__setattr__(
                self,
                field_name,
                require_non_empty(getattr(self, field_name), field_name),
            )

    def to_dict(self) -> dict[str, str]:
        return {"action_id": self.action_id, "action_type": self.action_type}

    @classmethod
    def from_dict(cls, value: object) -> ActionReference:
        data = _require_mapping(value, "action reference")
        _reject_unknown(data, {"action_id", "action_type"}, "action reference")
        return cls(action_id=data.get("action_id"), action_type=data.get("action_type"))


@dataclass(frozen=True, slots=True)
class MessageBlock:
    kind: str
    payload: FrozenObject | Mapping[str, Any] = field(default_factory=dict)
    entity_references: tuple[EntityReference, ...] = ()
    action_references: tuple[ActionReference, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", require_non_empty(self.kind, "kind"))
        if isinstance(self.payload, Mapping):
            object.__setattr__(self, "payload", freeze_json_object(self.payload, field="payload"))
        elif not isinstance(self.payload, FrozenObject):
            raise ValueError("payload must be a JSON object")
        validate_frozen_json_object(self.payload, field="payload")
        if not isinstance(self.entity_references, tuple) or not all(
            isinstance(reference, EntityReference) for reference in self.entity_references
        ):
            raise ValueError("entity_references must contain EntityReference values")
        if not isinstance(self.action_references, tuple) or not all(
            isinstance(reference, ActionReference) for reference in self.action_references
        ):
            raise ValueError("action_references must contain ActionReference values")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "payload": thaw_json_object(self.payload),
            "entity_references": [item.to_dict() for item in self.entity_references],
            "action_references": [item.to_dict() for item in self.action_references],
        }

    @classmethod
    def from_dict(cls, value: object) -> MessageBlock:
        data = _require_mapping(value, "message block")
        allowed = {"kind", "payload", "entity_references", "action_references"}
        _reject_unknown(data, allowed, "message block")
        return cls(
            kind=data.get("kind"),
            payload=_require_mapping(data.get("payload"), "payload"),
            entity_references=tuple(
                EntityReference.from_dict(item)
                for item in _parse_tuple(data.get("entity_references"), "entity_references")
            ),
            action_references=tuple(
                ActionReference.from_dict(item)
                for item in _parse_tuple(data.get("action_references"), "action_references")
            ),
        )


@dataclass(frozen=True, slots=True)
class StructuredMessage:
    message_id: str
    client_turn_id: str
    role: MessageRole
    created_at: datetime
    text: str | None = None
    blocks: tuple[MessageBlock, ...] = ()
    schema_version: int = STRUCTURED_MESSAGE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != STRUCTURED_MESSAGE_SCHEMA_VERSION
        ):
            raise ValueError(
                f"unsupported StructuredMessage schema version: {self.schema_version}"
            )
        for field_name in ("message_id", "client_turn_id"):
            object.__setattr__(
                self,
                field_name,
                require_non_empty(getattr(self, field_name), field_name),
            )
        if not isinstance(self.role, MessageRole):
            raise ValueError("role must be a MessageRole")
        require_aware(self.created_at, "created_at")
        object.__setattr__(self, "text", _optional_text(self.text, "text"))
        if not isinstance(self.blocks, tuple) or not all(
            isinstance(block, MessageBlock) for block in self.blocks
        ):
            raise ValueError("blocks must be a tuple of MessageBlock values")
        if self.text is None and not self.blocks:
            raise ValueError("a message must contain text or blocks")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "message_id": self.message_id,
            "client_turn_id": self.client_turn_id,
            "role": self.role.value,
            "created_at": _datetime_to_text(self.created_at, "created_at"),
            "blocks": [block.to_dict() for block in self.blocks],
        }
        if self.text is not None:
            result["text"] = self.text
        return result

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_dict(cls, value: object) -> StructuredMessage:
        data = _require_mapping(value, "StructuredMessage")
        allowed = {
            "schema_version",
            "message_id",
            "client_turn_id",
            "role",
            "created_at",
            "text",
            "blocks",
        }
        _reject_unknown(data, allowed, "StructuredMessage")
        version = data.get("schema_version")
        if type(version) is not int or version != STRUCTURED_MESSAGE_SCHEMA_VERSION:
            raise ValueError(f"unsupported StructuredMessage schema version: {version}")
        try:
            role = MessageRole(data.get("role"))
        except (TypeError, ValueError) as exc:
            raise ValueError("StructuredMessage contains an unknown role") from exc
        return cls(
            schema_version=version,
            message_id=data.get("message_id"),
            client_turn_id=data.get("client_turn_id"),
            role=role,
            created_at=_datetime_from_text(data.get("created_at"), "created_at"),
            text=data.get("text"),
            blocks=tuple(
                MessageBlock.from_dict(item)
                for item in _parse_tuple(data.get("blocks"), "blocks")
            ),
        )

    @classmethod
    def from_json(cls, value: str) -> StructuredMessage:
        if not isinstance(value, str):
            raise ValueError("StructuredMessage JSON must be a string")
        try:
            data = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("StructuredMessage contains invalid JSON") from exc
        return cls.from_dict(data)
