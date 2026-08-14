"""Input and output contracts for one dealership conversation turn."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from webchat.domain.common import require_aware
from webchat.domain.errors import DealerFailure

from .evidence import EvidenceRecord
from .state import (
    ActionReference,
    ConversationState,
    MessageBlock,
    PageContext,
    StructuredMessage,
)


@dataclass(frozen=True, slots=True)
class TurnRequest:
    current_input: str | None
    state: ConversationState
    now: datetime
    recent_messages: tuple[StructuredMessage, ...] = ()
    prior_failures: tuple[DealerFailure, ...] = ()
    action_reference: ActionReference | None = None
    page_observation: PageContext | None = None

    def __post_init__(self) -> None:
        if self.current_input is not None and not isinstance(self.current_input, str):
            raise ValueError("current_input must be a string or None")
        if self.action_reference is not None and self.current_input is not None:
            raise ValueError("a turn accepts either text or an action reference")
        if self.action_reference is None and (
            self.current_input is None or not self.current_input.strip()
        ):
            raise ValueError("a turn requires text or an action reference")
        if not isinstance(self.state, ConversationState):
            raise ValueError("state must be a ConversationState")
        require_aware(self.now, "now")
        if not isinstance(self.recent_messages, tuple) or not all(
            isinstance(item, StructuredMessage) for item in self.recent_messages
        ):
            raise ValueError("recent_messages must contain StructuredMessage values")
        if not isinstance(self.prior_failures, tuple) or not all(
            isinstance(item, DealerFailure) for item in self.prior_failures
        ):
            raise ValueError("prior_failures must contain DealerFailure values")
        if self.action_reference is not None and not isinstance(
            self.action_reference, ActionReference
        ):
            raise ValueError("action_reference must be an ActionReference")
        if self.page_observation is not None and not isinstance(
            self.page_observation, PageContext
        ):
            raise ValueError("page_observation must be a PageContext or None")


@dataclass(frozen=True, slots=True)
class TurnResult:
    state: ConversationState
    blocks: tuple[MessageBlock, ...]
    evidence: tuple[EvidenceRecord, ...] = ()
    executed_commands: tuple[str, ...] = ()
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    provider_latency_ms: float = 0
    provider_failure: str | None = None


def state_for_turn(request: TurnRequest) -> ConversationState:
    """Apply the browser's current page observation without trusting stale page state."""
    observation = request.page_observation
    if observation is None:
        return request.state
    entities = request.state.entities
    page_vehicle_id = observation.page_vehicle_id
    if page_vehicle_id is not None and page_vehicle_id != entities.selected_vehicle_id:
        entities = replace(
            entities,
            selected_vehicle_id=page_vehicle_id,
            selected_dealer_id=None,
            selected_test_drive_slot_id=None,
        )
    return replace(request.state, context=observation, entities=entities)
