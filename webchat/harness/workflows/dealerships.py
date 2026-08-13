"""Dealership discovery, contact, hours, business-information, and messaging workflows."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from webchat.domain.common import Department
from webchat.domain.dealer import DealerAdapter
from webchat.domain.dealerships import ContactMethod

from ..actions import PendingActionType, PendingRequestType
from ..contracts import PreparationCommandName, ReadCommandName
from ..policy import PolicyCode, PolicyEngine, PolicyError
from ..render import DeclarativeRenderer
from ..state import ConversationState, CustomerState, WorkflowDomain, WorkflowStage
from .common import (
    CommandOutcome,
    customer_payload,
    enum_value,
    missing_information,
    prepare_action,
    workflow_state,
)


async def execute_dealership_read(
    dealer: DealerAdapter,
    name: ReadCommandName,
    arguments: Mapping[str, Any],
    *,
    state: ConversationState,
    now: datetime,
    renderer: DeclarativeRenderer,
    id_factory: Callable[[], str],
) -> CommandOutcome | None:
    del now, id_factory
    if name is ReadCommandName.LIST_DEALERSHIPS:
        locations = await dealer.list_dealerships()
        return CommandOutcome(
            workflow_state(state, domain=WorkflowDomain.DEALERSHIP, stage=WorkflowStage.DISCOVERY),
            (_locations_block(locations, renderer),),
        )

    if name is ReadCommandName.GET_DEALERSHIP_DETAILS:
        dealership_id = arguments.get("dealership_id") or state.entities.selected_dealer_id
        if not dealership_id:
            return missing_information(state, ("dealership_id",), domain=WorkflowDomain.DEALERSHIP, renderer=renderer)
        item = await dealer.get_dealership(dealership_id)
        next_state = replace(
            workflow_state(state, domain=WorkflowDomain.DEALERSHIP, stage=WorkflowStage.COMPLETED),
            entities=replace(state.entities, selected_dealer_id=item.id),
        )
        return CommandOutcome(next_state, (_locations_block((item,), renderer),))

    if name is ReadCommandName.GET_DEALERSHIP_HOURS:
        dealership_id = arguments.get("dealership_id") or state.entities.selected_dealer_id
        if not dealership_id:
            return missing_information(state, ("dealership_id",), domain=WorkflowDomain.DEALERSHIP, renderer=renderer)
        department = enum_value(Department, arguments.get("department"), "department")
        if department is Department.GENERAL:
            department = None
        hours = await dealer.get_opening_hours(dealership_id, department)
        regular = tuple({
            "kind": "regular", "department": period.department.value,
            "day_of_week": period.day_of_week,
            "opens_at": None if period.opens_at is None else period.opens_at.isoformat(timespec="minutes"),
            "closes_at": None if period.closes_at is None else period.closes_at.isoformat(timespec="minutes"),
            "closed": period.is_closed,
        } for period in hours.regular if department is None or period.department is department)
        holidays = tuple({
            "kind": "holiday", "date": period.date.isoformat(), "label": period.label,
            "department": period.department.value,
            "opens_at": None if period.opens_at is None else period.opens_at.isoformat(timespec="minutes"),
            "closes_at": None if period.closes_at is None else period.closes_at.isoformat(timespec="minutes"),
            "closed": period.is_closed,
        } for period in hours.holidays if department is None or period.department is department)
        block = renderer.records(
            "opening_hours", regular + holidays,
            entity_type="dealership", entity_ids=tuple(dealership_id for _ in regular + holidays),
        )
        return CommandOutcome(
            replace(state, entities=replace(state.entities, selected_dealer_id=dealership_id)),
            (block,),
        )

    if name is ReadCommandName.GET_BUSINESS_INFORMATION:
        info = await dealer.get_business_information()
        records = ({
            "organisation": info.organisation, "currency": info.currency,
            "market": info.market, "finance_notice": info.finance_notice,
            "finance_minimum_age": info.finance_minimum_age,
            "part_exchange_notice": info.part_exchange_notice,
            "privacy_contact": info.privacy_contact,
        },)
        return CommandOutcome(
            state,
            (renderer.records("business_information", records, entity_type="business_information", entity_ids=(info.organisation,)),),
        )
    return None


async def execute_dealership_preparation(
    dealer: DealerAdapter,
    name: PreparationCommandName,
    arguments: Mapping[str, Any],
    *,
    state: ConversationState,
    now: datetime,
    renderer: DeclarativeRenderer,
    id_factory: Callable[[], str],
    policy: PolicyEngine,
) -> CommandOutcome | None:
    del dealer
    if name is not PreparationCommandName.PREPARE_DEALERSHIP_MESSAGE:
        return None
    dealership_id = arguments.get("dealership_id") or state.entities.selected_dealer_id
    department = enum_value(
        Department,
        arguments.get("department"),
        "department",
        Department.GENERAL,
    )
    subject = arguments.get("subject") or f"Customer message for {department.value}"
    required = {
        "dealership_id": dealership_id,
        "message": arguments.get("message"),
    }
    missing = tuple(field for field, value in required.items() if not value)
    if missing:
        return missing_information(state, missing, domain=WorkflowDomain.DEALERSHIP, renderer=renderer)
    try:
        customer = policy.customer_identity(arguments, state=state)
    except PolicyError as exc:
        if exc.code in {PolicyCode.MISSING_CUSTOMER_DETAILS, PolicyCode.INVALID_CUSTOMER_DETAILS}:
            return missing_information(state, exc.fields, domain=WorkflowDomain.DEALERSHIP, renderer=renderer)
        raise
    state = replace(
        state,
        customer=CustomerState(customer.first_name, customer.last_name, customer.email, customer.phone, state.customer.registration),
    )
    return prepare_action(
        state, action_type=PendingActionType.DEALERSHIP_MESSAGE,
        request_type=PendingRequestType.DEALERSHIP_MESSAGE,
        payload={
            "dealership_id": dealership_id,
            "department": department.value,
            "subject": subject, "message": arguments.get("message"),
            "customer": customer_payload(customer),
            "preferred_contact_method": enum_value(ContactMethod, arguments.get("preferred_contact_method"), "preferred_contact_method", ContactMethod.EMAIL).value,
        },
        now=now, id_factory=id_factory, renderer=renderer, domain=WorkflowDomain.DEALERSHIP,
    )


def _locations_block(locations, renderer):
    records = tuple({
        "id": item.id, "name": item.name,
        "address": [*item.address.lines, item.address.town, item.address.postcode],
        "phone": item.phone, "email": item.email, "brands": list(item.brands),
        "latitude": str(item.latitude), "longitude": str(item.longitude),
    } for item in locations)
    return renderer.records(
        "dealerships", records, entity_type="dealership",
        entity_ids=tuple(item.id for item in locations), action_type="select_dealership",
    )
