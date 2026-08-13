"""Conservative zero-model routing for unambiguous turn inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re

from webchat.domain.common import require_aware

from .actions import PendingActionState
from .state import ActionReference, ConversationState


class DeterministicRouteKind(StrEnum):
    UI_ACTION = "ui_action"
    CONFIRM_PENDING_ACTION = "confirm_pending_action"
    CANCEL_PENDING_ACTION = "cancel_pending_action"
    RETRY_FAILED_ACTION = "retry_failed_action"
    SHOW_MORE_RESULTS = "show_more_results"
    DOMAIN_REDIRECT = "domain_redirect"


@dataclass(frozen=True, slots=True)
class DeterministicRoute:
    kind: DeterministicRouteKind
    action_reference: ActionReference | None = None


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
        if normalized in {
            "what size is the moon",
            "how big is the moon",
            "who won the world cup",
        }:
            return DeterministicRoute(kind=DeterministicRouteKind.DOMAIN_REDIRECT)
        return None
