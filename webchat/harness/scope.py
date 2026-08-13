"""Conservative zero-model routing for unambiguous turn inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re

from webchat.domain.common import require_aware

from .actions import PendingActionState
from .signals import is_obviously_unrelated
from .state import ActionReference, ConversationState


class DeterministicRouteKind(StrEnum):
    UI_ACTION = "ui_action"
    CONFIRM_PENDING_ACTION = "confirm_pending_action"
    CANCEL_PENDING_ACTION = "cancel_pending_action"
    RETRY_FAILED_ACTION = "retry_failed_action"
    SHOW_MORE_RESULTS = "show_more_results"
    SELECT_PRESENTED_ENTITY = "select_presented_entity"
    START_OVER = "start_over"
    DOMAIN_REDIRECT = "domain_redirect"


@dataclass(frozen=True, slots=True)
class DeterministicRoute:
    kind: DeterministicRouteKind
    action_reference: ActionReference | None = None
    ordinal: int | None = None

    def __post_init__(self) -> None:
        if self.ordinal is not None and (type(self.ordinal) is not int or self.ordinal < 1):
            raise ValueError("ordinal must be a positive integer or None")
        if (self.kind is DeterministicRouteKind.SELECT_PRESENTED_ENTITY) != (self.ordinal is not None):
            raise ValueError("only entity-selection routes contain an ordinal")


class ScopeGate:
    """Return a route only when application state makes intent unambiguous."""

    def route(
        self,
        *,
        text: str | None,
        state: ConversationState,
        now: datetime,
        action_reference: ActionReference | None = None,
    ) -> DeterministicRoute | None:
        if not isinstance(state, ConversationState):
            raise ValueError("state must be a ConversationState")
        require_aware(now, "now")
        if action_reference is not None:
            if not isinstance(action_reference, ActionReference):
                raise ValueError("action_reference must be an ActionReference")
            return DeterministicRoute(
                kind=DeterministicRouteKind.UI_ACTION,
                action_reference=action_reference,
            )
        if text is None or not isinstance(text, str) or not text.strip():
            return None

        normalized = re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()
        pending = state.pending_action
        if (
            pending is not None
            and pending.state is PendingActionState.SUCCEEDED
            and normalized in {"confirm", "yes", "yes confirm", "yes please"}
        ):
            return DeterministicRoute(
                kind=DeterministicRouteKind.CONFIRM_PENDING_ACTION
            )
        if pending is not None and not pending.is_expired(now):
            if (
                pending.state is PendingActionState.AWAITING_CONFIRMATION
                and normalized in {"confirm", "yes", "yes confirm", "yes please"}
            ):
                return DeterministicRoute(
                    kind=DeterministicRouteKind.CONFIRM_PENDING_ACTION
                )
            if (
                pending.state
                in {
                    PendingActionState.AWAITING_CONFIRMATION,
                    PendingActionState.CONFIRMED,
                    PendingActionState.FAILED,
                }
                and normalized
                in {"cancel", "cancel that", "never mind", "no", "no thanks"}
            ):
                return DeterministicRoute(
                    kind=DeterministicRouteKind.CANCEL_PENDING_ACTION
                )
            if (
                pending.state is PendingActionState.FAILED
                and normalized in {"retry", "try again"}
            ):
                return DeterministicRoute(
                    kind=DeterministicRouteKind.RETRY_FAILED_ACTION
                )

        if (
            state.presentation_groups
            and normalized in {"show more", "more results", "next page"}
        ):
            return DeterministicRoute(kind=DeterministicRouteKind.SHOW_MORE_RESULTS)
        if (
            normalized in {"start over", "start again", "reset"}
            and (
                state.pending_action is not None
                or state.workflow.domain.value != "none"
                or state.presentation_groups
            )
        ):
            return DeterministicRoute(kind=DeterministicRouteKind.START_OVER)
        if state.presentation_groups:
            ordinal = _selection_ordinal(normalized)
            if (
                ordinal is not None
                and state.presentation_groups[-1].resolve_ordinal(ordinal) is not None
            ):
                return DeterministicRoute(
                    kind=DeterministicRouteKind.SELECT_PRESENTED_ENTITY,
                    ordinal=ordinal,
                )
        has_vehicle_context = bool(
            state.entities.selected_vehicle_id
            or state.context.page_vehicle_id
            or any(group.entity_type == "vehicle" for group in state.presentation_groups)
        )
        contextual_vehicle_question = has_vehicle_context and bool(
            re.search(r"\b(fit|space|boot|inside|carry|seats?)\b", normalized)
        )
        if is_obviously_unrelated(normalized) and not contextual_vehicle_question:
            return DeterministicRoute(kind=DeterministicRouteKind.DOMAIN_REDIRECT)
        return None


_WORD_ORDINALS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
}


def _selection_ordinal(normalized: str) -> int | None:
    match = re.fullmatch(
        r"(?:the )?(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|[1-9][0-9]?(?:st|nd|rd|th)?)(?: one)?",
        normalized,
    )
    if match is None:
        return None
    token = match.group(1)
    if token in _WORD_ORDINALS:
        return _WORD_ORDINALS[token]
    digits = re.match(r"\d+", token)
    return int(digits.group()) if digits is not None else None
