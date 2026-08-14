"""Provider-neutral contracts for answers constrained to explicit evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import json
import math
from typing import Any

from webchat.domain.common import require_non_empty

from .evidence import (
    EvidenceEnvelope,
    EvidenceGap,
    EvidenceItem,
    EvidenceRecord,
    EvidenceReference,
    EvidenceValue,
)
from .grounded_validation import GroundedRepairContext
from .state import ConversationState, MessageBlock


GROUNDED_RESPONSE_REQUEST_SCHEMA_VERSION = 3
GROUNDED_RESPONSE_RESULT_SCHEMA_VERSION = 3


class GroundedClaimKind(StrEnum):
    SUPPORTED_FACT = "supported_fact"
    EVIDENCE_BASED_INFERENCE = "evidence_based_inference"
    LIMITATION_UNKNOWN = "limitation_unknown"
    GENERAL_GUIDANCE = "general_guidance"


@dataclass(frozen=True, slots=True)
class GroundedEvidenceBinding:
    """A provider's typed copy of one cited factual evidence value."""

    reference: EvidenceReference
    value: EvidenceValue

    def __post_init__(self) -> None:
        if not isinstance(self.reference, EvidenceReference):
            raise ValueError("reference must be an EvidenceReference")
        if not isinstance(self.value, (bool, int, float, str)):
            raise ValueError("value must be a JSON scalar")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("value must be finite")
        if isinstance(self.value, str) and not self.value:
            raise ValueError("value must be a non-empty JSON scalar")

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.reference.evidence_id,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class GroundedResponseState:
    """Small PII-free state projection relevant to answer synthesis."""

    selected_vehicle_id: str | None
    selected_dealer_id: str | None
    workflow_domain: str
    workflow_stage: str
    missing_fields: tuple[str, ...]
    preferences: tuple[tuple[str, object], ...]

    @classmethod
    def from_conversation(cls, state: ConversationState) -> "GroundedResponseState":
        if not isinstance(state, ConversationState):
            raise ValueError("state must be a ConversationState")
        return cls(
            selected_vehicle_id=state.entities.selected_vehicle_id,
            selected_dealer_id=state.entities.selected_dealer_id,
            workflow_domain=state.workflow.domain.value,
            workflow_stage=state.workflow.stage.value,
            missing_fields=state.workflow.missing_fields,
            preferences=tuple(sorted(state.preferences.to_dict().items())),
        )

    def __post_init__(self) -> None:
        for name in ("selected_vehicle_id", "selected_dealer_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, require_non_empty(value, name))
        object.__setattr__(
            self,
            "workflow_domain",
            require_non_empty(self.workflow_domain, "workflow_domain"),
        )
        object.__setattr__(
            self,
            "workflow_stage",
            require_non_empty(self.workflow_stage, "workflow_stage"),
        )
        if not isinstance(self.missing_fields, tuple) or not all(
            isinstance(item, str) and item.strip() for item in self.missing_fields
        ):
            raise ValueError("missing_fields must contain normalized strings")
        if not isinstance(self.preferences, tuple):
            raise ValueError("preferences must be a canonical tuple")
        preference_dict = dict(self.preferences)
        if len(preference_dict) != len(self.preferences):
            raise ValueError("preferences cannot contain duplicate fields")
        try:
            json.dumps(preference_dict, allow_nan=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("preferences must contain JSON values") from exc
        if self.preferences != tuple(sorted(self.preferences)):
            raise ValueError("preferences must use canonical field ordering")

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_vehicle_id": self.selected_vehicle_id,
            "selected_dealer_id": self.selected_dealer_id,
            "workflow_domain": self.workflow_domain,
            "workflow_stage": self.workflow_stage,
            "missing_fields": list(self.missing_fields),
            "preferences": dict(self.preferences),
        }


@dataclass(frozen=True, slots=True)
class GroundedAllowedAction:
    """Declarative next step the responder may mention, never execute."""

    action_type: str
    label: str
    entity_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "action_type", require_non_empty(self.action_type, "action_type")
        )
        object.__setattr__(self, "label", require_non_empty(self.label, "label"))
        if self.entity_id is not None:
            object.__setattr__(
                self, "entity_id", require_non_empty(self.entity_id, "entity_id")
            )

    def to_dict(self) -> dict[str, str | None]:
        return {
            "action_type": self.action_type,
            "label": self.label,
            "entity_id": self.entity_id,
        }


@dataclass(frozen=True, slots=True)
class GroundedClaim:
    text: str
    kind: GroundedClaimKind
    evidence: tuple[EvidenceReference, ...] = ()
    bindings: tuple[GroundedEvidenceBinding, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", require_non_empty(self.text, "text"))
        if not isinstance(self.kind, GroundedClaimKind):
            raise ValueError("kind must be a GroundedClaimKind")
        if not isinstance(self.evidence, tuple) or not all(
            isinstance(item, EvidenceReference) for item in self.evidence
        ):
            raise ValueError("evidence must contain EvidenceReference values")
        ids = tuple(item.evidence_id for item in self.evidence)
        if len(ids) != len(set(ids)):
            raise ValueError("claim evidence references cannot contain duplicates")
        if not isinstance(self.bindings, tuple) or not all(
            isinstance(item, GroundedEvidenceBinding) for item in self.bindings
        ):
            raise ValueError("bindings must contain GroundedEvidenceBinding values")
        binding_ids = tuple(item.reference.evidence_id for item in self.bindings)
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("claim evidence bindings cannot contain duplicates")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "text": self.text,
            "evidence_ids": [item.evidence_id for item in self.evidence],
            "evidence_values": [item.to_dict() for item in self.bindings],
        }


@dataclass(frozen=True, slots=True)
class GroundedResponseRequest:
    question: str
    state: GroundedResponseState
    evidence: EvidenceEnvelope
    missing_facts: tuple[EvidenceReference, ...] = ()
    allowed_actions: tuple[GroundedAllowedAction, ...] = ()
    focusable_entity_ids: tuple[str, ...] = ()
    repair: GroundedRepairContext | None = None
    schema_version: int = GROUNDED_RESPONSE_REQUEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != GROUNDED_RESPONSE_REQUEST_SCHEMA_VERSION
        ):
            raise ValueError(
                f"unsupported GroundedResponseRequest schema version: {self.schema_version}"
            )
        object.__setattr__(self, "question", require_non_empty(self.question, "question"))
        if not isinstance(self.state, GroundedResponseState):
            raise ValueError("state must be a GroundedResponseState")
        if not isinstance(self.evidence, EvidenceEnvelope):
            raise ValueError("evidence must be an EvidenceEnvelope")
        if not isinstance(self.missing_facts, tuple) or not all(
            isinstance(item, EvidenceReference) for item in self.missing_facts
        ):
            raise ValueError("missing_facts must contain EvidenceReference values")
        missing_ids = tuple(item.evidence_id for item in self.missing_facts)
        if len(missing_ids) != len(set(missing_ids)):
            raise ValueError("missing_facts cannot contain duplicates")
        for reference in self.missing_facts:
            if not isinstance(self.evidence.resolve(reference), EvidenceGap):
                raise ValueError("missing_facts must reference evidence gaps")
        if not isinstance(self.allowed_actions, tuple) or not all(
            isinstance(item, GroundedAllowedAction) for item in self.allowed_actions
        ):
            raise ValueError("allowed_actions must contain GroundedAllowedAction values")
        action_keys = tuple(
            (item.action_type, item.entity_id) for item in self.allowed_actions
        )
        if len(action_keys) != len(set(action_keys)):
            raise ValueError("allowed_actions cannot contain duplicate targets")
        if not isinstance(self.focusable_entity_ids, tuple):
            raise ValueError("focusable_entity_ids must be a tuple")
        normalized_focus_ids = tuple(
            require_non_empty(item, "focusable_entity_id")
            for item in self.focusable_entity_ids
        )
        if len(normalized_focus_ids) != len(set(normalized_focus_ids)):
            raise ValueError("focusable_entity_ids cannot contain duplicates")
        evidence_entities = {
            item.entity_id for item in self.evidence.items + self.evidence.gaps
        }
        if set(normalized_focus_ids) - evidence_entities:
            raise ValueError("focusable_entity_ids must identify supplied evidence")
        object.__setattr__(self, "focusable_entity_ids", normalized_focus_ids)
        if self.repair is not None and not isinstance(self.repair, GroundedRepairContext):
            raise ValueError("repair must be a GroundedRepairContext or None")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "question": self.question,
            "state": self.state.to_dict(),
            "evidence": self.evidence.to_dict(),
            "missing_facts": [item.evidence_id for item in self.missing_facts],
            "allowed_actions": [item.to_dict() for item in self.allowed_actions],
            "focusable_entity_ids": list(self.focusable_entity_ids),
            "repair": None if self.repair is None else self.repair.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def assemble_evidence(
    records: tuple[EvidenceRecord, ...],
    *,
    generated_at: datetime,
) -> EvidenceEnvelope:
    """Deduplicate command evidence and build one canonical turn envelope."""

    unique = {item.evidence_id: item for item in records}
    items = tuple(item for item in unique.values() if isinstance(item, EvidenceItem))
    gaps = tuple(item for item in unique.values() if isinstance(item, EvidenceGap))
    return EvidenceEnvelope(items=items, gaps=gaps, generated_at=generated_at)


def allowed_actions_from_blocks(
    blocks: tuple[MessageBlock, ...],
) -> tuple[GroundedAllowedAction, ...]:
    """Project inert UI actions without exposing server-issued action IDs."""

    actions: list[GroundedAllowedAction] = []
    seen: set[tuple[str, str | None]] = set()
    for block in blocks:
        issued = {
            (item.action_id, item.action_type) for item in block.action_references
        }
        payload_actions = block.to_dict()["payload"].get("actions", [])
        for item in payload_actions:
            if not isinstance(item, dict):
                continue
            action_type = item.get("action_type")
            if not isinstance(action_type, str) or not action_type.strip():
                continue
            action_id = item.get("action_id")
            if (action_id, action_type) not in issued:
                continue
            entity_id = item.get("entity_id")
            if entity_id is not None and not isinstance(entity_id, str):
                continue
            key = (action_type, entity_id)
            if key in seen:
                continue
            label = item.get("label")
            if not isinstance(label, str) or not label.strip():
                label = action_type.replace("_", " ").capitalize()
            actions.append(GroundedAllowedAction(action_type, label, entity_id))
            seen.add(key)
    return tuple(actions)


def focusable_entity_ids_from_blocks(
    blocks: tuple[MessageBlock, ...],
) -> tuple[str, ...]:
    """Return stable UI entity IDs in presentation order without duplicates."""

    identifiers: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        for reference in block.entity_references:
            if reference.entity_id not in seen:
                identifiers.append(reference.entity_id)
                seen.add(reference.entity_id)
    return tuple(identifiers)
