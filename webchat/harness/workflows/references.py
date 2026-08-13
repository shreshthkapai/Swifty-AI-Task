"""Dealer-independent resolution of customer-facing dealership references."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from webchat.domain.dealer import DealerAdapter
from webchat.domain.dealerships import DealerLocation

from ..render import DeclarativeRenderer
from ..state import ConversationState, WorkflowDomain, WorkflowStage
from .common import CommandOutcome, workflow_state


@dataclass(frozen=True, slots=True)
class DealershipResolution:
    reference: str | None
    location: DealerLocation | None
    candidates: tuple[DealerLocation, ...]


def _normalise(value: str) -> str:
    return " ".join(value.split()).casefold()


async def resolve_dealership_reference(
    dealer: DealerAdapter,
    *,
    dealership_id: str | None,
    dealership_query: str | None,
    selected_id: str | None,
) -> DealershipResolution:
    locations = await dealer.list_dealerships()
    reference = dealership_id or dealership_query or selected_id
    if reference is None or not reference.strip():
        return DealershipResolution(None, None, locations)

    normalised = _normalise(reference)
    id_matches = tuple(
        location for location in locations if _normalise(location.id) == normalised
    )
    if len(id_matches) == 1:
        return DealershipResolution(reference, id_matches[0], id_matches)

    named_matches = tuple(
        location
        for location in locations
        if normalised
        in {
            _normalise(location.name),
            _normalise(location.address.town),
        }
    )
    if len(named_matches) == 1:
        return DealershipResolution(reference, named_matches[0], named_matches)
    return DealershipResolution(
        reference,
        None,
        named_matches or locations,
    )


async def resolve_dealership_for_command(
    dealer: DealerAdapter,
    arguments: Mapping[str, Any],
    *,
    state: ConversationState,
    domain: WorkflowDomain,
    renderer: DeclarativeRenderer,
    required: bool,
) -> tuple[str | None, CommandOutcome | None]:
    dealership_id = arguments.get("dealership_id")
    dealership_query = arguments.get("dealership_query")
    selected_id = state.entities.selected_dealer_id
    if not required and not (dealership_id or dealership_query or selected_id):
        return None, None
    resolution = await resolve_dealership_reference(
        dealer,
        dealership_id=dealership_id,
        dealership_query=dealership_query,
        selected_id=selected_id,
    )
    if resolution.location is not None:
        return resolution.location.id, None
    return None, _unresolved_outcome(
        state,
        resolution,
        domain=domain,
        renderer=renderer,
    )


def _unresolved_outcome(
    state: ConversationState,
    resolution: DealershipResolution,
    *,
    domain: WorkflowDomain,
    renderer: DeclarativeRenderer,
) -> CommandOutcome:
    text = (
        "Please choose a dealership."
        if resolution.reference is None
        else "I couldn't uniquely match that dealership. Please choose one."
    )
    blocks = [
        renderer.notice(
            text,
            code=(
                "dealership_required"
                if resolution.reference is None
                else "dealership_not_resolved"
            ),
        )
    ]
    if resolution.candidates:
        records = tuple(
            {
                "id": item.id,
                "name": item.name,
                "town": item.address.town,
                "postcode": item.address.postcode,
            }
            for item in resolution.candidates
        )
        blocks.append(
            renderer.records(
                "dealerships",
                records,
                entity_type="dealership",
                entity_ids=tuple(item.id for item in resolution.candidates),
                action_type="select_dealership",
            )
        )
    return CommandOutcome(
        workflow_state(
            state,
            domain=domain,
            stage=WorkflowStage.COLLECTING_DETAILS,
            missing=("dealership_id",),
        ),
        tuple(blocks),
    )
