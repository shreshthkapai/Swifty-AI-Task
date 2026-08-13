"""Deterministic declarative UI blocks built only from trusted runtime data."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from decimal import Decimal
import secrets
from typing import Any

from webchat.domain.common import Money
from webchat.domain.vehicles import Vehicle

from .state import ActionReference, EntityReference, MessageBlock


UI_BLOCK_SCHEMA_VERSION = 1


def _default_id() -> str:
    return secrets.token_urlsafe(18)


def _money(value: Money | None) -> dict[str, Any]:
    if value is None:
        return {
            "amount_minor": None,
            "currency": None,
            "display": "Price on request",
        }
    symbols = {"GBP": "£", "EUR": "€", "USD": "$"}
    amount = Decimal(value.amount_minor) / Decimal(100)
    decimals = 0 if value.amount_minor % 100 == 0 else 2
    formatted = f"{amount:,.{decimals}f}"
    prefix = symbols.get(value.currency, f"{value.currency} ")
    return {
        "amount_minor": value.amount_minor,
        "currency": value.currency,
        "display": f"{prefix}{formatted}",
    }


def _vehicle_payload(vehicle: Vehicle) -> dict[str, Any]:
    return {
        "id": vehicle.id,
        "label": f"{vehicle.year} {vehicle.make} {vehicle.model} {vehicle.variant}",
        "make": vehicle.make,
        "model": vehicle.model,
        "variant": vehicle.variant,
        "year": vehicle.year,
        "price": _money(vehicle.price),
        "monthly_price": _money(vehicle.monthly_price) if vehicle.monthly_price else None,
        "mileage": vehicle.mileage,
        "fuel_type": vehicle.fuel_type,
        "transmission": vehicle.transmission,
        "body_style": vehicle.body_style,
        "colour": vehicle.colour,
        "availability": vehicle.availability.value,
        "dealership_id": vehicle.dealership_id,
        "dealership_name": vehicle.dealership_name,
        "image_url": vehicle.images[0] if vehicle.images else None,
    }


class DeclarativeRenderer:
    """Create versioned blocks with inert, server-issued action references."""

    def __init__(self, *, id_factory: Callable[[], str] = _default_id) -> None:
        self._id_factory = id_factory

    def text(self, value: str) -> MessageBlock:
        return MessageBlock(
            kind="text",
            payload={"schema_version": UI_BLOCK_SCHEMA_VERSION, "text": value},
        )

    def notice(self, value: str, *, code: str) -> MessageBlock:
        return MessageBlock(
            kind="notice",
            payload={
                "schema_version": UI_BLOCK_SCHEMA_VERSION,
                "code": code,
                "text": value,
            },
        )

    def vehicle_cards(self, vehicles: Sequence[Vehicle]) -> MessageBlock:
        actions = tuple(
            ActionReference(self._id_factory(), "select_vehicle") for _ in vehicles
        )
        return MessageBlock(
            kind="vehicle_cards",
            payload={
                "schema_version": UI_BLOCK_SCHEMA_VERSION,
                "vehicles": [_vehicle_payload(vehicle) for vehicle in vehicles],
                "actions": [
                    {
                        "action_id": action.action_id,
                        "action_type": action.action_type,
                        "entity_id": vehicle.id,
                        "label": "View vehicle",
                    }
                    for vehicle, action in zip(vehicles, actions, strict=True)
                ],
            },
            entity_references=tuple(
                EntityReference("vehicle", vehicle.id) for vehicle in vehicles
            ),
            action_references=actions,
        )

    def comparison(self, vehicles: Sequence[Vehicle]) -> MessageBlock:
        columns = [
            {"vehicle_id": item.id, "label": f"{item.make} {item.model}"}
            for item in vehicles
        ]
        rows = (
            ("Price", [_money(item.price)["display"] for item in vehicles]),
            ("Year", [str(item.year) for item in vehicles]),
            ("Mileage", [f"{item.mileage:,} miles" for item in vehicles]),
            ("Fuel", [item.fuel_type for item in vehicles]),
            ("Transmission", [item.transmission for item in vehicles]),
            ("Body style", [item.body_style for item in vehicles]),
            ("Availability", [item.availability.value for item in vehicles]),
        )
        return MessageBlock(
            kind="comparison",
            payload={
                "schema_version": UI_BLOCK_SCHEMA_VERSION,
                "columns": columns,
                "rows": [
                    {"label": label, "values": values} for label, values in rows
                ],
            },
            entity_references=tuple(
                EntityReference("vehicle", vehicle.id) for vehicle in vehicles
            ),
        )

    def actions(
        self,
        choices: Sequence[tuple[str, str, str | None]],
    ) -> MessageBlock:
        references = tuple(
            ActionReference(self._id_factory(), action_type)
            for action_type, _, _ in choices
        )
        return MessageBlock(
            kind="actions",
            payload={
                "schema_version": UI_BLOCK_SCHEMA_VERSION,
                "actions": [
                    {
                        "action_id": reference.action_id,
                        "action_type": reference.action_type,
                        "label": label,
                        "entity_id": entity_id,
                    }
                    for reference, (_, label, entity_id) in zip(
                        references, choices, strict=True
                    )
                ],
            },
            action_references=references,
        )

    def records(
        self,
        kind: str,
        records: Sequence[dict[str, Any]],
        *,
        entity_type: str,
        entity_ids: Sequence[str],
        action_type: str | None = None,
    ) -> MessageBlock:
        actions = (
            tuple(ActionReference(self._id_factory(), action_type) for _ in records)
            if action_type is not None
            else ()
        )
        payload: dict[str, Any] = {
            "schema_version": UI_BLOCK_SCHEMA_VERSION,
            "items": list(records),
        }
        if actions:
            payload["actions"] = [
                {
                    "action_id": action.action_id,
                    "action_type": action.action_type,
                    "entity_id": entity_id,
                }
                for action, entity_id in zip(actions, entity_ids, strict=True)
            ]
        return MessageBlock(
            kind=kind,
            payload=payload,
            entity_references=tuple(
                EntityReference(entity_type, entity_id) for entity_id in entity_ids
            ),
            action_references=actions,
        )

    def confirmation(
        self,
        *,
        action_id: str,
        action_type: str,
        summary: dict[str, Any],
        expires_at: datetime,
    ) -> MessageBlock:
        confirm = ActionReference(action_id, "confirm")
        cancel = ActionReference(action_id, "cancel")
        return MessageBlock(
            kind="confirmation",
            payload={
                "schema_version": UI_BLOCK_SCHEMA_VERSION,
                "pending_action_id": action_id,
                "pending_action_type": action_type,
                "summary": summary,
                "expires_at": expires_at.isoformat(),
                "actions": [
                    {"action_id": confirm.action_id, "action_type": "confirm", "label": "Confirm"},
                    {"action_id": cancel.action_id, "action_type": "cancel", "label": "Cancel"},
                ],
            },
            action_references=(confirm, cancel),
        )

    def link(self, *, label: str, href: str, entity_id: str | None = None) -> MessageBlock:
        references = (
            () if entity_id is None else (EntityReference("link_target", entity_id),)
        )
        return MessageBlock(
            kind="link",
            payload={
                "schema_version": UI_BLOCK_SCHEMA_VERSION,
                "label": label,
                "href": href,
            },
            entity_references=references,
        )
