"""Deterministic, budgeted context compilation for one planning call."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
import json
import re
from typing import Any

from webchat.domain.common import require_aware
from webchat.domain.errors import DealerFailure

from .state import ConversationState, PageContext, StructuredMessage, WorkflowDomain
from .tool_gate import TOOL_GATE_POLICY_VERSION, ToolSelection


CONTEXT_COMPILER_POLICY_VERSION = 1
DEFAULT_MAX_CONTEXT_CHARS = 12_000
DEFAULT_MAX_ESTIMATED_TOKENS = 3_000
DEFAULT_MAX_HISTORY_MESSAGES = 6
MAX_PRIOR_FAILURES = 3


class ContextPriority(IntEnum):
    REQUIRED = 0
    ACTIVE_TASK = 1
    RELEVANT = 2
    SNAPSHOT = 3
    HISTORY = 4


class ContextAuthority(StrEnum):
    CURRENT_INPUT = "current_input"
    HARNESS_STATE = "harness_state"
    REFRESHABLE_OBSERVATION = "refreshable_observation"
    HISTORICAL_SNAPSHOT = "historical_snapshot"
    HISTORICAL_MESSAGE = "historical_message"


class CompilerInclusionReason(StrEnum):
    REQUIRED = "required"
    ACTIVE_TASK = "active_task"
    RELEVANT_CONTEXT = "relevant_context"
    RECENT_SNAPSHOT = "recent_snapshot"
    RECENT_HISTORY = "recent_history"


class CompilerOmissionReason(StrEnum):
    EMPTY = "empty"
    NOT_RELEVANT = "not_relevant"
    BUDGET_EXCEEDED = "budget_exceeded"
    HISTORY_LIMIT = "history_limit"


@dataclass(frozen=True, slots=True)
class ContextDiagnosticItem:
    name: str
    priority: ContextPriority
    reason: CompilerInclusionReason | CompilerOmissionReason
    chars: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "priority": int(self.priority),
            "reason": self.reason.value,
            "chars": self.chars,
        }


@dataclass(frozen=True, slots=True)
class CompilerDiagnostics:
    selected: tuple[ContextDiagnosticItem, ...]
    omitted: tuple[ContextDiagnosticItem, ...]
    compiled_chars: int
    estimated_tokens: int
    max_chars: int
    max_estimated_tokens: int
    compiler_policy_version: int = CONTEXT_COMPILER_POLICY_VERSION
    tool_gate_policy_version: int = TOOL_GATE_POLICY_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "compiler_policy_version": self.compiler_policy_version,
            "tool_gate_policy_version": self.tool_gate_policy_version,
            "compiled_chars": self.compiled_chars,
            "estimated_tokens": self.estimated_tokens,
            "max_chars": self.max_chars,
            "max_estimated_tokens": self.max_estimated_tokens,
            "selected_order": [item.name for item in self.selected],
            "selected": [item.to_dict() for item in self.selected],
            "omitted": [item.to_dict() for item in self.omitted],
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class CompiledContext:
    serialized: str
    diagnostics: CompilerDiagnostics


@dataclass(frozen=True, slots=True)
class _CandidateSection:
    name: str
    authority: ContextAuthority
    priority: ContextPriority
    inclusion_reason: CompilerInclusionReason
    content: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "authority": self.authority.value,
            "content": self.content,
        }


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _time_text(value: datetime) -> str:
    require_aware(value, "now")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_CANDIDATE = re.compile(r"(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w)")
_UK_REGISTRATION = re.compile(r"\b[A-Z]{2}\d{2}\s?[A-Z]{3}\b", re.IGNORECASE)
_SENSITIVE_WORKFLOW_FIELDS = {
    "address",
    "email",
    "first_name",
    "last_name",
    "name",
    "phone",
    "postcode",
    "registration",
}
_VEHICLE_CONTEXT_SIGNAL = re.compile(
    r"\b(vehicle|car|cars|bmw|mini|suv|hatchback|saloon|automatic|manual|"
    r"petrol|diesel|electric|budget|cheaper|test drive)\b",
    re.IGNORECASE,
)


def _redact_historical(text: str, state: ConversationState) -> str:
    redacted = text
    known_values = (
        state.customer.first_name,
        state.customer.last_name,
        state.customer.email,
        state.customer.phone,
        state.customer.registration,
    )
    for value in sorted((item for item in known_values if item), key=len, reverse=True):
        redacted = re.sub(re.escape(value), "[redacted]", redacted, flags=re.IGNORECASE)
    redacted = _EMAIL.sub("[redacted-email]", redacted)
    redacted = _PHONE_CANDIDATE.sub(
        lambda match: (
            "[redacted-phone]"
            if sum(character.isdigit() for character in match.group()) >= 9
            else match.group()
        ),
        redacted,
    )
    return _UK_REGISTRATION.sub("[redacted-registration]", redacted)


def _redact_persisted_value(value: Any, state: ConversationState) -> Any:
    if isinstance(value, str):
        return _redact_historical(value, state)
    if isinstance(value, list):
        return [_redact_persisted_value(item, state) for item in value]
    if isinstance(value, dict):
        return {
            key: _redact_persisted_value(item, state)
            for key, item in value.items()
        }
    return value


class ContextCompiler:
    """Build the smallest replayable planner context from authoritative state."""

    def __init__(
        self,
        *,
        max_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
        max_estimated_tokens: int = DEFAULT_MAX_ESTIMATED_TOKENS,
        max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES,
    ) -> None:
        for name, value, minimum in (
            ("max_chars", max_chars, 1_024),
            ("max_estimated_tokens", max_estimated_tokens, 256),
            ("max_history_messages", max_history_messages, 1),
        ):
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} must be an integer of at least {minimum}")
        self._max_chars = max_chars
        self._max_estimated_tokens = max_estimated_tokens
        self._max_history_messages = max_history_messages

    def compile(
        self,
        *,
        current_input: str,
        now: datetime,
        state: ConversationState,
        tool_selection: ToolSelection,
        recent_messages: tuple[StructuredMessage, ...] = (),
        prior_failures: tuple[DealerFailure, ...] = (),
        page_observation: PageContext | None = None,
    ) -> CompiledContext:
        if not isinstance(current_input, str) or not current_input.strip():
            raise ValueError("current_input must be a non-blank string")
        require_aware(now, "now")
        if not isinstance(state, ConversationState):
            raise ValueError("state must be a ConversationState")
        if not isinstance(tool_selection, ToolSelection):
            raise ValueError("tool_selection must be a ToolSelection")
        if not isinstance(recent_messages, tuple) or not all(
            isinstance(item, StructuredMessage) for item in recent_messages
        ):
            raise ValueError("recent_messages must be a tuple of StructuredMessage values")
        if not isinstance(prior_failures, tuple) or not all(
            isinstance(item, DealerFailure) for item in prior_failures
        ):
            raise ValueError("prior_failures must be a tuple of DealerFailure values")
        if page_observation is not None and not isinstance(page_observation, PageContext):
            raise ValueError("page_observation must be a PageContext or None")

        candidates, pre_omitted = self._candidates(
            current_input=current_input,
            now=now,
            state=state,
            tool_selection=tool_selection,
            recent_messages=recent_messages,
            prior_failures=prior_failures,
            page_observation=page_observation,
        )
        selected_sections: list[dict[str, Any]] = []
        selected: list[ContextDiagnosticItem] = []
        omitted = list(pre_omitted)
        effective_limit = min(
            self._max_chars,
            self._max_estimated_tokens * 4,
        )
        for candidate in candidates:
            encoded_section = _canonical_json(candidate.to_dict())
            trial = _canonical_json(
                {
                    "compiler_policy_version": CONTEXT_COMPILER_POLICY_VERSION,
                    "sections": [*selected_sections, candidate.to_dict()],
                }
            )
            if len(trial) > effective_limit:
                if candidate.priority is ContextPriority.REQUIRED:
                    raise ValueError("required planning context exceeds configured budget")
                omitted.append(
                    ContextDiagnosticItem(
                        name=candidate.name,
                        priority=candidate.priority,
                        reason=CompilerOmissionReason.BUDGET_EXCEEDED,
                        chars=len(encoded_section),
                    )
                )
                continue
            selected_sections.append(candidate.to_dict())
            selected.append(
                ContextDiagnosticItem(
                    name=candidate.name,
                    priority=candidate.priority,
                    reason=candidate.inclusion_reason,
                    chars=len(encoded_section),
                )
            )

        serialized = _canonical_json(
            {
                "compiler_policy_version": CONTEXT_COMPILER_POLICY_VERSION,
                "sections": selected_sections,
            }
        )
        diagnostics = CompilerDiagnostics(
            selected=tuple(selected),
            omitted=tuple(omitted),
            compiled_chars=len(serialized),
            estimated_tokens=(len(serialized) + 3) // 4,
            max_chars=self._max_chars,
            max_estimated_tokens=self._max_estimated_tokens,
        )
        return CompiledContext(serialized=serialized, diagnostics=diagnostics)

    def _candidates(
        self,
        *,
        current_input: str,
        now: datetime,
        state: ConversationState,
        tool_selection: ToolSelection,
        recent_messages: tuple[StructuredMessage, ...],
        prior_failures: tuple[DealerFailure, ...],
        page_observation: PageContext | None,
    ) -> tuple[list[_CandidateSection], list[ContextDiagnosticItem]]:
        candidates = [
            _CandidateSection(
                name="turn",
                authority=ContextAuthority.CURRENT_INPUT,
                priority=ContextPriority.REQUIRED,
                inclusion_reason=CompilerInclusionReason.REQUIRED,
                content={"current_input": current_input, "server_time": _time_text(now)},
            ),
            _CandidateSection(
                name="tool_visibility",
                authority=ContextAuthority.HARNESS_STATE,
                priority=ContextPriority.REQUIRED,
                inclusion_reason=CompilerInclusionReason.REQUIRED,
                content={
                    "tool_gate_policy_version": tool_selection.policy_version,
                    "allowed_commands": list(tool_selection.included_names),
                    "action_controls": list(tool_selection.action_controls),
                    "visibility_is_execution_authority": False,
                },
            ),
            _CandidateSection(
                name="workflow",
                authority=ContextAuthority.HARNESS_STATE,
                priority=ContextPriority.ACTIVE_TASK,
                inclusion_reason=CompilerInclusionReason.ACTIVE_TASK,
                content=self._sanitized_workflow(state),
            ),
            _CandidateSection(
                name="selected_entities",
                authority=ContextAuthority.HARNESS_STATE,
                priority=ContextPriority.ACTIVE_TASK,
                inclusion_reason=CompilerInclusionReason.ACTIVE_TASK,
                content=state.entities.to_dict(),
            ),
            self._customer_presence(state),
        ]
        omitted: list[ContextDiagnosticItem] = []
        if state.pending_action is not None:
            action = state.pending_action
            candidates.append(
                _CandidateSection(
                    name="pending_action",
                    authority=ContextAuthority.HARNESS_STATE,
                    priority=ContextPriority.ACTIVE_TASK,
                    inclusion_reason=CompilerInclusionReason.ACTIVE_TASK,
                    content={
                        "action_id": action.action_id,
                        "action_type": action.action_type.value,
                        "state": action.state.value,
                        "expires_at": _time_text(action.expires_at),
                        "is_expired": action.is_expired(now),
                        "attempt_count": action.attempt_count,
                    },
                )
            )
        else:
            omitted.append(
                self._empty_omission("pending_action", ContextPriority.ACTIVE_TASK)
            )
        active_grants = tuple(
            grant
            for grant in state.verification_grants
            if grant.is_active(now)
        )
        if active_grants:
            candidates.append(
                _CandidateSection(
                    name="authorization",
                    authority=ContextAuthority.HARNESS_STATE,
                    priority=ContextPriority.ACTIVE_TASK,
                    inclusion_reason=CompilerInclusionReason.ACTIVE_TASK,
                    content={
                        "active_workshop_booking_grants": [
                            {
                                "booking_id": grant.booking_id,
                                "expires_at": _time_text(grant.expires_at),
                            }
                            for grant in sorted(
                                active_grants,
                                key=lambda item: item.booking_id,
                            )
                        ]
                    },
                )
            )
        else:
            omitted.append(
                self._empty_omission("authorization", ContextPriority.ACTIVE_TASK)
            )
        if prior_failures:
            candidates.append(self._failure_section(prior_failures, state))
        else:
            omitted.append(
                self._empty_omission("prior_failures", ContextPriority.ACTIVE_TASK)
            )
        preferences = state.preferences.to_dict()
        preferences_present = any(
            value not in (None, [], ()) for value in preferences.values()
        )
        preferences_relevant = (
            state.workflow.domain
            in {
                WorkflowDomain.VEHICLES,
                WorkflowDomain.SALES,
                WorkflowDomain.TEST_DRIVE,
            }
            or _VEHICLE_CONTEXT_SIGNAL.search(current_input) is not None
        )
        if preferences_present and preferences_relevant:
            candidates.append(
                _CandidateSection(
                    name="preferences",
                    authority=ContextAuthority.HARNESS_STATE,
                    priority=ContextPriority.RELEVANT,
                    inclusion_reason=CompilerInclusionReason.RELEVANT_CONTEXT,
                    content=preferences,
                )
            )
        elif not preferences_present:
            omitted.append(
                self._empty_omission("preferences", ContextPriority.RELEVANT)
            )
        else:
            omitted.append(
                ContextDiagnosticItem(
                    name="preferences",
                    priority=ContextPriority.RELEVANT,
                    reason=CompilerOmissionReason.NOT_RELEVANT,
                    chars=len(_canonical_json(preferences)),
                )
            )
        page = (page_observation or state.context).to_dict()
        if any(key != "is_authoritative" for key in page):
            candidates.append(
                _CandidateSection(
                    name="page_observation",
                    authority=ContextAuthority.REFRESHABLE_OBSERVATION,
                    priority=ContextPriority.RELEVANT,
                    inclusion_reason=CompilerInclusionReason.RELEVANT_CONTEXT,
                    content=_redact_persisted_value(page, state),
                )
            )
        else:
            omitted.append(
                self._empty_omission("page_observation", ContextPriority.RELEVANT)
            )
        for group in reversed(state.presentation_groups):
            candidates.append(
                _CandidateSection(
                    name=f"presentation:{group.group_id}",
                    authority=ContextAuthority.HISTORICAL_SNAPSHOT,
                    priority=ContextPriority.SNAPSHOT,
                    inclusion_reason=CompilerInclusionReason.RECENT_SNAPSHOT,
                    content=_redact_persisted_value(group.to_dict(), state),
                )
            )
        if not state.presentation_groups:
            omitted.append(
                self._empty_omission("presentation_groups", ContextPriority.SNAPSHOT)
            )
        ordered_messages = sorted(
            recent_messages,
            key=lambda item: (item.created_at.astimezone(UTC), item.message_id),
        )
        if len(ordered_messages) > self._max_history_messages:
            omitted.append(
                ContextDiagnosticItem(
                    name="recent_history:older_messages",
                    priority=ContextPriority.HISTORY,
                    reason=CompilerOmissionReason.HISTORY_LIMIT,
                    chars=0,
                )
            )
        selected_messages = ordered_messages[-self._max_history_messages :]
        if selected_messages:
            candidates.append(self._history_section(selected_messages, state))
        else:
            omitted.append(
                self._empty_omission("recent_history", ContextPriority.HISTORY)
            )
        return candidates, omitted

    @staticmethod
    def _empty_omission(
        name: str,
        priority: ContextPriority,
    ) -> ContextDiagnosticItem:
        return ContextDiagnosticItem(
            name=name,
            priority=priority,
            reason=CompilerOmissionReason.EMPTY,
            chars=0,
        )

    @staticmethod
    def _sanitized_workflow(state: ConversationState) -> dict[str, Any]:
        workflow = state.workflow.to_dict()
        gathered = workflow["gathered_fields"]
        safe_fields: dict[str, Any] = {}
        sensitive_known: list[str] = []
        for name, value in gathered.items():
            if name.casefold() in _SENSITIVE_WORKFLOW_FIELDS:
                if value is not None:
                    sensitive_known.append(name)
                continue
            safe_fields[name] = _redact_persisted_value(value, state)
        workflow["gathered_fields"] = safe_fields
        if sensitive_known:
            workflow["sensitive_fields_known"] = sorted(sensitive_known)
        return workflow

    @staticmethod
    def _customer_presence(state: ConversationState) -> _CandidateSection:
        names = ("first_name", "last_name", "email", "phone", "registration")
        known = sorted(name for name in names if getattr(state.customer, name) is not None)
        missing = sorted(set(names) - set(known))
        return _CandidateSection(
            name="customer_presence",
            authority=ContextAuthority.HARNESS_STATE,
            priority=ContextPriority.ACTIVE_TASK,
            inclusion_reason=CompilerInclusionReason.ACTIVE_TASK,
            content={"known_fields": known, "missing_fields": missing},
        )

    @staticmethod
    def _failure_section(
        failures: tuple[DealerFailure, ...],
        state: ConversationState,
    ) -> _CandidateSection:
        normalized = []
        for failure in failures[-MAX_PRIOR_FAILURES:]:
            item: dict[str, Any] = {
                "kind": failure.kind.value,
                "retryable": failure.retryable,
                "field_violations": [
                    {"field": violation.field, "code": violation.code}
                    for violation in failure.field_violations
                ],
            }
            if failure.resource is not None:
                item["resource"] = _redact_historical(failure.resource, state)
            normalized.append(item)
        return _CandidateSection(
            name="prior_failures",
            authority=ContextAuthority.HARNESS_STATE,
            priority=ContextPriority.ACTIVE_TASK,
            inclusion_reason=CompilerInclusionReason.ACTIVE_TASK,
            content={"failures": normalized},
        )

    @staticmethod
    def _history_section(
        messages: list[StructuredMessage],
        state: ConversationState,
    ) -> _CandidateSection:
        content = []
        for message in messages:
            item: dict[str, Any] = {
                "message_id": message.message_id,
                "role": message.role.value,
                "created_at": _time_text(message.created_at),
                "block_kinds": [block.kind for block in message.blocks],
            }
            if message.text is not None:
                item["text"] = _redact_historical(message.text, state)
            content.append(item)
        return _CandidateSection(
            name="recent_history",
            authority=ContextAuthority.HISTORICAL_MESSAGE,
            priority=ContextPriority.HISTORY,
            inclusion_reason=CompilerInclusionReason.RECENT_HISTORY,
            content={"messages": content},
        )
