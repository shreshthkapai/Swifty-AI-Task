"""Vehicle discovery, details, comparison, availability, and offer workflows."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from webchat.domain.common import Money, Page
from webchat.domain.dealer import DealerAdapter
from webchat.domain.vehicles import (
    OfferSearch,
    Vehicle,
    VehicleAvailabilityStatus,
    VehicleSearch,
    VehicleSort,
)

from ..contracts import ReadCommandName
from ..policy import PolicyEngine
from ..render import DeclarativeRenderer
from ..state import (
    ConversationState,
    EntityContext,
    VehiclePreferences,
    WorkflowDomain,
    WorkflowStage,
)
from .common import (
    CommandOutcome,
    enum_value,
    money_from_minor,
    optional,
    presentation_group,
    workflow_state,
)
from .references import resolve_dealership_for_command


async def execute_vehicle_read(
    dealer: DealerAdapter,
    name: ReadCommandName,
    arguments: Mapping[str, Any],
    *,
    state: ConversationState,
    now: datetime,
    renderer: DeclarativeRenderer,
    id_factory: Callable[[], str],
    policy: PolicyEngine,
) -> CommandOutcome | None:
    del policy
    if name is ReadCommandName.SEARCH_VEHICLES:
        search_arguments = dict(arguments)
        dealership_id, failure = await resolve_dealership_for_command(
            dealer,
            arguments,
            state=state,
            domain=WorkflowDomain.VEHICLES,
            renderer=renderer,
            required=False,
        )
        if failure is not None:
            return failure
        search_arguments["dealership_id"] = dealership_id
        refinement = search_arguments.pop("refinement", None)
        if refinement is not None and search_arguments.get("max_price_minor") is None:
            ceiling = await _refinement_ceiling(dealer, refinement, state)
            if ceiling is not None:
                search_arguments["max_price_minor"] = ceiling.amount_minor
                search_arguments["currency"] = ceiling.currency
        search = _vehicle_search(search_arguments, state)
        page = await dealer.search_vehicles(search)
        return _search_result(page, search, state=state, now=now, renderer=renderer, id_factory=id_factory)

    if name is ReadCommandName.GET_VEHICLE_DETAILS:
        vehicle_id = _vehicle_id(arguments, state)
        if vehicle_id is None:
            return CommandOutcome(
                workflow_state(state, domain=WorkflowDomain.VEHICLES, stage=WorkflowStage.SELECTING_ENTITY),
                (renderer.notice("Please choose a vehicle first.", code="vehicle_required"),),
            )
        details = await dealer.get_vehicle(vehicle_id)
        next_state = replace(
            workflow_state(state, domain=WorkflowDomain.VEHICLES, stage=WorkflowStage.SELECTING_ENTITY),
            entities=replace(state.entities, selected_vehicle_id=vehicle_id),
        )
        return CommandOutcome(
            next_state,
            (
                renderer.vehicle_cards((details.vehicle,)),
                renderer.records(
                    "vehicle_details",
                    ({"vehicle_id": vehicle_id, "description": details.vehicle.description, "highlights": list(details.highlights)},),
                    entity_type="vehicle",
                    entity_ids=(vehicle_id,),
                ),
                renderer.link(label="View on website", href=f"/?vehicle={vehicle_id}", entity_id=vehicle_id),
            ),
        )

    if name is ReadCommandName.COMPARE_VEHICLES:
        ids = arguments.get("vehicle_ids") or _recent_vehicle_ids(state, limit=3)
        if not isinstance(ids, list | tuple) or len(ids) < 2:
            return CommandOutcome(
                state,
                (renderer.notice("Please choose at least two vehicles to compare.", code="comparison_requires_vehicles"),),
            )
        details = [await dealer.get_vehicle(vehicle_id) for vehicle_id in ids[:3]]
        return CommandOutcome(state, (renderer.comparison(tuple(item.vehicle for item in details)),))

    if name is ReadCommandName.CHECK_VEHICLE_AVAILABILITY:
        vehicle_id = _vehicle_id(arguments, state)
        if vehicle_id is None:
            return CommandOutcome(
                state,
                (renderer.notice("Please choose a vehicle first.", code="vehicle_required"),),
            )
        availability = await dealer.get_vehicle_availability(vehicle_id)
        choices: list[tuple[str, str, str | None]] = []
        if availability.can_book_test_drive:
            choices.append(("find_test_drive_slots", "Find test-drive slots", vehicle_id))
        if availability.can_register_interest:
            choices.append(("register_interest", "Register interest", vehicle_id))
        if availability.can_enquire:
            choices.append(("sales_enquiry", "Send a sales enquiry", vehicle_id))
        blocks = [
            renderer.records(
                "availability",
                ({
                    "vehicle_id": vehicle_id,
                    "status": availability.status.value,
                    "can_book_test_drive": availability.can_book_test_drive,
                    "can_register_interest": availability.can_register_interest,
                    "can_enquire": availability.can_enquire,
                },),
                entity_type="vehicle",
                entity_ids=(vehicle_id,),
            )
        ]
        if choices:
            blocks.append(renderer.actions(tuple(choices)))
        return CommandOutcome(
            replace(state, entities=replace(state.entities, selected_vehicle_id=vehicle_id)),
            tuple(blocks),
        )

    if name is ReadCommandName.LIST_NEW_CAR_OFFERS:
        offers = await dealer.list_offers(
            OfferSearch(make=arguments.get("make"), product_type=arguments.get("product_type"))
        )
        records = tuple(
            {
                "id": offer.id,
                "make": offer.make,
                "model": offer.model,
                "title": offer.title,
                "product_type": offer.product_type,
                "monthly_price": offer.monthly_price.amount_minor,
                "upfront_price": offer.upfront_price.amount_minor,
                "currency": offer.monthly_price.currency,
                "apr_percent": None if offer.apr_percent is None else str(offer.apr_percent),
                "term_months": offer.term_months,
                "annual_mileage": offer.annual_mileage,
                "expires_on": offer.expires_on.isoformat(),
                "description": offer.description,
            }
            for offer in offers
        )
        return CommandOutcome(
            workflow_state(state, domain=WorkflowDomain.VEHICLES, stage=WorkflowStage.DISCOVERY),
            (renderer.records("offer_cards", records, entity_type="offer", entity_ids=tuple(offer.id for offer in offers)),),
        )
    return None


def _vehicle_search(arguments: Mapping[str, Any], state: ConversationState) -> VehicleSearch:
    preferences = state.preferences
    make = optional(arguments, "make", preferences.makes[0] if preferences.makes else None)
    model = optional(arguments, "model", preferences.models[0] if preferences.models else None)
    currency = optional(arguments, "currency", preferences.currency or "GBP")
    return VehicleSearch(
        query=arguments.get("query"),
        make=make,
        model=model,
        fuel_type=optional(arguments, "fuel_type", preferences.fuel),
        transmission=optional(arguments, "transmission", preferences.transmission),
        body_style=optional(arguments, "body_style", preferences.body_type),
        availability=enum_value(VehicleAvailabilityStatus, arguments.get("availability"), "availability"),
        dealership_id=optional(arguments, "dealership_id", state.entities.selected_dealer_id),
        min_price=money_from_minor(optional(arguments, "min_price_minor", preferences.minimum_price_minor), currency),
        max_price=money_from_minor(optional(arguments, "max_price_minor", preferences.maximum_price_minor), currency),
        max_mileage=arguments.get("max_mileage"),
        min_year=arguments.get("min_year"),
        sort=enum_value(VehicleSort, arguments.get("sort"), "sort", VehicleSort.NEWEST),
        page=arguments.get("page") or 1,
        page_size=arguments.get("page_size") or 10,
    )


def _search_result(
    page: Page[Vehicle],
    search: VehicleSearch,
    *,
    state: ConversationState,
    now: datetime,
    renderer: DeclarativeRenderer,
    id_factory: Callable[[], str],
) -> CommandOutcome:
    preferences = VehiclePreferences(
        minimum_price_minor=None if search.min_price is None else search.min_price.amount_minor,
        maximum_price_minor=None if search.max_price is None else search.max_price.amount_minor,
        currency=(search.min_price or search.max_price).currency if search.min_price or search.max_price else None,
        makes=() if search.make is None else (search.make,),
        models=() if search.model is None else (search.model,),
        fuel=search.fuel_type,
        transmission=search.transmission,
        body_type=search.body_style,
    )
    next_state = workflow_state(
        replace(state, preferences=preferences),
        domain=WorkflowDomain.VEHICLES,
        stage=WorkflowStage.REFINING,
        gathered={"last_vehicle_search": _search_snapshot(search)},
    )
    if not page.items:
        return CommandOutcome(
            next_state,
            (renderer.notice("I couldn't find matching vehicles. Try relaxing one of the filters.", code="no_vehicle_results"),),
        )
    cards = renderer.vehicle_cards(page.items)
    group = presentation_group(
        group_id=id_factory(),
        entity_type="vehicle",
        records=tuple((item.id, {
            "make": item.make,
            "model": item.model,
            "price_minor": None if item.price is None else item.price.amount_minor,
            "currency": None if item.price is None else item.price.currency,
        }) for item in page.items),
        block=cards,
        now=now,
    )
    next_state = next_state.with_presentation_group(group)
    blocks = [cards]
    if page.page < page.total_pages:
        blocks.append(renderer.actions((("show_more", "Show more", group.group_id),)))
    return CommandOutcome(next_state, tuple(blocks))


def _search_snapshot(search: VehicleSearch) -> dict[str, Any]:
    return {
        "query": search.query, "make": search.make, "model": search.model,
        "fuel_type": search.fuel_type, "transmission": search.transmission,
        "body_style": search.body_style,
        "availability": None if search.availability is None else search.availability.value,
        "dealership_id": search.dealership_id,
        "min_price_minor": None if search.min_price is None else search.min_price.amount_minor,
        "max_price_minor": None if search.max_price is None else search.max_price.amount_minor,
        "currency": (search.min_price or search.max_price).currency if search.min_price or search.max_price else None,
        "max_mileage": search.max_mileage, "min_year": search.min_year,
        "sort": search.sort.value, "page": search.page, "page_size": search.page_size,
    }


def _vehicle_id(arguments: Mapping[str, Any], state: ConversationState) -> str | None:
    return arguments.get("vehicle_id") or state.entities.selected_vehicle_id or state.context.page_vehicle_id


def _recent_vehicle_ids(state: ConversationState, *, limit: int) -> tuple[str, ...]:
    for group in reversed(state.presentation_groups):
        if group.entity_type == "vehicle":
            return tuple(item.entity_id for item in group.entities[:limit])
    return ()


async def _refinement_ceiling(
    dealer: DealerAdapter,
    refinement: str,
    state: ConversationState,
):
    if refinement == "cheaper_than_selected" and state.entities.selected_vehicle_id:
        selected = await dealer.get_vehicle(state.entities.selected_vehicle_id)
        price = selected.vehicle.price
        if price is not None and price.amount_minor > 0:
            return type(price)(price.amount_minor - 1, price.currency)
    for group in reversed(state.presentation_groups):
        if group.entity_type != "vehicle":
            continue
        prices = []
        currency = None
        for entity in group.entities:
            snapshot = entity.to_dict()["snapshot"]
            amount = snapshot.get("price_minor")
            if isinstance(amount, int) and amount > 0:
                prices.append(amount)
                currency = snapshot.get("currency")
        if prices and isinstance(currency, str):
            return Money(min(prices) - 1, currency)
    return None
