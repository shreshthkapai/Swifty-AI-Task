"""Deterministic validation and repair contracts for grounded responses."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from webchat.domain.common import require_non_empty

from .evidence import EvidenceGap

if TYPE_CHECKING:
    from .grounded_response import GroundedClaim, GroundedResponseRequest


class GroundedValidationCode(StrEnum):
    UNKNOWN_EVIDENCE_REFERENCE = "unknown_evidence_reference"
    DEALER_CLAIM_WITHOUT_EVIDENCE = "dealer_claim_without_evidence"
    EVIDENCE_VALUE_MISSING = "evidence_value_missing"
    EVIDENCE_VALUE_MISMATCH = "evidence_value_mismatch"
    SPECIFICATION_WITHOUT_EVIDENCE = "specification_without_evidence"
    UNKNOWN_PRESENTED_AS_KNOWN = "unknown_presented_as_known"
    UNKNOWN_NOT_DISCLOSED = "unknown_not_disclosed"
    GENERAL_GUIDANCE_USES_DEALER_EVIDENCE = "general_guidance_uses_dealer_evidence"
    FOCUSED_ENTITY_WITHOUT_EVIDENCE = "focused_entity_without_evidence"
    INVALID_OUTPUT = "invalid_output"


@dataclass(frozen=True, slots=True)
class GroundedValidationIssue:
    code: GroundedValidationCode
    claim_index: int | None = None
    evidence_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, GroundedValidationCode):
            raise ValueError("code must be a GroundedValidationCode")
        if self.claim_index is not None and (
            type(self.claim_index) is not int or self.claim_index < 0
        ):
            raise ValueError("claim_index must be a non-negative integer or None")
        if self.evidence_id is not None:
            require_non_empty(self.evidence_id, "evidence_id")


class GroundedResponseValidationError(ValueError):
    def __init__(self, issues: tuple[GroundedValidationIssue, ...]) -> None:
        if not isinstance(issues, tuple) or not issues or not all(
            isinstance(item, GroundedValidationIssue) for item in issues
        ):
            raise ValueError("issues must contain GroundedValidationIssue values")
        self.issues = issues
        super().__init__(",".join(item.code.value for item in issues))


@dataclass(frozen=True, slots=True)
class GroundedRepairContext:
    violations: tuple[GroundedValidationCode, ...]
    attempt: int = 1

    def __post_init__(self) -> None:
        if self.attempt != 1:
            raise ValueError("only one grounded-response repair attempt is allowed")
        if not isinstance(self.violations, tuple) or not self.violations or not all(
            isinstance(item, GroundedValidationCode) for item in self.violations
        ):
            raise ValueError("violations must contain GroundedValidationCode values")
        object.__setattr__(self, "violations", tuple(sorted(set(self.violations))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "violations": [item.value for item in self.violations],
        }


class GroundedResponseValidator:
    """Validate provider claims against the exact turn evidence."""

    def validate(
        self,
        request: GroundedResponseRequest,
        claims: tuple[GroundedClaim, ...],
        focused_entity_id: str | None = None,
    ) -> None:
        from .grounded_response import (
            GroundedClaim,
            GroundedClaimKind,
            GroundedResponseRequest,
        )

        if not isinstance(request, GroundedResponseRequest):
            raise ValueError("request must be a GroundedResponseRequest")
        if not isinstance(claims, tuple) or not claims or not all(
            isinstance(item, GroundedClaim) for item in claims
        ):
            raise GroundedResponseValidationError((
                GroundedValidationIssue(GroundedValidationCode.INVALID_OUTPUT),
            ))

        issues: list[GroundedValidationIssue] = []
        normalized_text = tuple(item.text.casefold() for item in claims)
        if len(normalized_text) != len(set(normalized_text)):
            issues.append(GroundedValidationIssue(GroundedValidationCode.INVALID_OUTPUT))

        records = {
            item.evidence_id: item
            for item in request.evidence.items + request.evidence.gaps
        }
        disclosed_gaps: set[str] = set()
        factual_kinds = {
            GroundedClaimKind.SUPPORTED_FACT,
            GroundedClaimKind.EVIDENCE_BASED_INFERENCE,
        }

        if focused_entity_id is not None:
            supported_focus = focused_entity_id in request.focusable_entity_ids
            if supported_focus:
                supported_focus = any(
                    claim.kind in factual_kinds
                    and any(
                        evidence_id in records
                        and records[evidence_id].entity_id == focused_entity_id
                        and not isinstance(records[evidence_id], EvidenceGap)
                        for evidence_id in (
                            reference.evidence_id for reference in claim.evidence
                        )
                    )
                    for claim in claims
                )
            if not supported_focus:
                issues.append(GroundedValidationIssue(
                    GroundedValidationCode.FOCUSED_ENTITY_WITHOUT_EVIDENCE,
                    evidence_id=focused_entity_id,
                ))

        for index, claim in enumerate(claims):
            references = tuple(sorted(
                item.evidence_id for item in claim.evidence
            ))
            bindings = {
                item.reference.evidence_id: item for item in claim.bindings
            }

            for evidence_id in references:
                if evidence_id not in records:
                    issues.append(GroundedValidationIssue(
                        GroundedValidationCode.UNKNOWN_EVIDENCE_REFERENCE,
                        index,
                        evidence_id,
                    ))
            for evidence_id in sorted(bindings):
                if evidence_id not in records or evidence_id not in references:
                    issues.append(GroundedValidationIssue(
                        GroundedValidationCode.SPECIFICATION_WITHOUT_EVIDENCE,
                        index,
                        evidence_id,
                    ))

            resolved = tuple(
                (evidence_id, records[evidence_id])
                for evidence_id in references
                if evidence_id in records
            )
            if claim.kind in factual_kinds:
                if not references:
                    issues.append(GroundedValidationIssue(
                        GroundedValidationCode.DEALER_CLAIM_WITHOUT_EVIDENCE,
                        index,
                    ))
                for evidence_id, record in resolved:
                    if isinstance(record, EvidenceGap):
                        issues.append(GroundedValidationIssue(
                            GroundedValidationCode.UNKNOWN_PRESENTED_AS_KNOWN,
                            index,
                            evidence_id,
                        ))
                        continue
                    binding = bindings.get(evidence_id)
                    if binding is None:
                        issues.append(GroundedValidationIssue(
                            GroundedValidationCode.EVIDENCE_VALUE_MISSING,
                            index,
                            evidence_id,
                        ))
                    elif (
                        type(binding.value) is not type(record.value)
                        or binding.value != record.value
                    ):
                        issues.append(GroundedValidationIssue(
                            GroundedValidationCode.EVIDENCE_VALUE_MISMATCH,
                            index,
                            evidence_id,
                        ))
            elif claim.kind is GroundedClaimKind.LIMITATION_UNKNOWN:
                for evidence_id, record in resolved:
                    if isinstance(record, EvidenceGap):
                        disclosed_gaps.add(evidence_id)
                    else:
                        issues.append(GroundedValidationIssue(
                            GroundedValidationCode.UNKNOWN_PRESENTED_AS_KNOWN,
                            index,
                            evidence_id,
                        ))
                if bindings:
                    issues.append(GroundedValidationIssue(
                        GroundedValidationCode.UNKNOWN_PRESENTED_AS_KNOWN,
                        index,
                    ))
            elif claim.kind is GroundedClaimKind.GENERAL_GUIDANCE and (
                references or bindings
            ):
                issues.append(GroundedValidationIssue(
                    GroundedValidationCode.GENERAL_GUIDANCE_USES_DEALER_EVIDENCE,
                    index,
                ))

        for reference in request.missing_facts:
            if reference.evidence_id not in disclosed_gaps:
                issues.append(GroundedValidationIssue(
                    GroundedValidationCode.UNKNOWN_NOT_DISCLOSED,
                    evidence_id=reference.evidence_id,
                ))

        if issues:
            raise GroundedResponseValidationError(tuple(sorted(
                issues,
                key=lambda item: (
                    item.code.value,
                    -1 if item.claim_index is None else item.claim_index,
                    "" if item.evidence_id is None else item.evidence_id,
                ),
            )))
