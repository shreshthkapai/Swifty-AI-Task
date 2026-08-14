"""Typed domain records to provider-neutral evidence; never reads rendered UI blocks."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping

from webchat.domain.common import Money, Page
from webchat.domain.dealerships import BusinessInformation, DealerLocation, OpeningHours
from webchat.domain.sales import TestDriveSlot
from webchat.domain.vehicles import (
    Vehicle,
    VehicleAvailability,
    VehicleDetails,
    VehicleOffer,
    VehicleSearch,
)
from webchat.domain.workshop import (
    WorkshopBookingDetails,
    WorkshopService,
    WorkshopSlot,
)

from ..evidence import (
    EvidenceAuthority,
    EvidenceFreshness,
    EvidenceGap,
    EvidenceGapReason,
    EvidenceItem,
    EvidenceRecord,
)


def _scalar(value: Any) -> bool | int | float | str:
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, (datetime, Decimal)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    if isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"unsupported evidence scalar: {type(value).__name__}")


def _items(
    source_operation: str,
    entity_id: str,
    values: Mapping[str, Any],
    *,
    observed_at: datetime,
    freshness: EvidenceFreshness,
    authority: EvidenceAuthority = EvidenceAuthority.DEALER,
) -> tuple[EvidenceItem, ...]:
    return tuple(
        EvidenceItem(
            source_operation=source_operation,
            entity_id=entity_id,
            field_name=field_name,
            value=_scalar(value),
            authority=authority,
            freshness=freshness,
            observed_at=observed_at,
        )
        for field_name, value in values.items()
        if value is not None
    )


def _money(
    source_operation: str,
    entity_id: str,
    field_name: str,
    value: Money | None,
    *,
    observed_at: datetime,
    freshness: EvidenceFreshness,
) -> tuple[EvidenceRecord, ...]:
    if value is None:
        return (
            EvidenceGap(
                source_operation=source_operation,
                entity_id=entity_id,
                field_name=field_name,
                reason=EvidenceGapReason.NOT_RETURNED,
                authority=EvidenceAuthority.DEALER,
                freshness=freshness,
                observed_at=observed_at,
            ),
        )
    return _items(
        source_operation,
        entity_id,
        {
            f"{field_name}.amount_minor": value.amount_minor,
            f"{field_name}.currency": value.currency,
        },
        observed_at=observed_at,
        freshness=freshness,
    )


def vehicle_evidence(
    vehicle: Vehicle,
    *,
    source_operation: str,
) -> tuple[EvidenceRecord, ...]:
    observed_at = vehicle.updated_at
    freshness = EvidenceFreshness.SNAPSHOT
    facts: tuple[EvidenceRecord, ...] = _items(
        source_operation,
        vehicle.id,
        {
            "dealership_id": vehicle.dealership_id,
            "dealership_name": vehicle.dealership_name,
            "dealership_town": vehicle.dealership_town,
            "make": vehicle.make,
            "model": vehicle.model,
            "variant": vehicle.variant,
            "year": vehicle.year,
            "mileage": vehicle.mileage,
            "fuel_type": vehicle.fuel_type,
            "transmission": vehicle.transmission,
            "colour": vehicle.colour,
            "body_style": vehicle.body_style,
            "availability": vehicle.availability,
            "description": vehicle.description,
        },
        observed_at=observed_at,
        freshness=freshness,
    )
    return (
        facts
        + _money(source_operation, vehicle.id, "price", vehicle.price, observed_at=observed_at, freshness=freshness)
        + _money(source_operation, vehicle.id, "monthly_price", vehicle.monthly_price, observed_at=observed_at, freshness=freshness)
    )


def vehicle_details_evidence(
    details: VehicleDetails,
    *,
    source_operation: str = "get_vehicle_details",
) -> tuple[EvidenceRecord, ...]:
    highlights = _items(
        source_operation,
        details.vehicle.id,
        {f"highlights[{index}]": value for index, value in enumerate(details.highlights)},
        observed_at=details.vehicle.updated_at,
        freshness=EvidenceFreshness.SNAPSHOT,
    )
    return vehicle_evidence(details.vehicle, source_operation=source_operation) + highlights


def vehicle_search_evidence(
    page: Page[Vehicle],
    search: VehicleSearch,
    *,
    observed_at: datetime,
    exclusions: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[EvidenceRecord, ...]:
    constraint_values = {
        "query": search.query,
        "make": search.make,
        "model": search.model,
        "fuel_type": search.fuel_type,
        "transmission": search.transmission,
        "body_style": search.body_style,
        "availability": search.availability,
        "dealership_id": search.dealership_id,
        "min_price.amount_minor": None if search.min_price is None else search.min_price.amount_minor,
        "min_price.currency": None if search.min_price is None else search.min_price.currency,
        "max_price.amount_minor": None if search.max_price is None else search.max_price.amount_minor,
        "max_price.currency": None if search.max_price is None else search.max_price.currency,
        "max_mileage": search.max_mileage,
        "min_year": search.min_year,
        "sort": search.sort,
        "page": search.page,
        "page_size": search.page_size,
    }
    query = _items(
        "search_vehicles",
        "vehicle_search",
        constraint_values,
        observed_at=observed_at,
        freshness=EvidenceFreshness.LIVE,
        authority=EvidenceAuthority.HARNESS,
    )
    exclusion_facts = _items(
        "search_vehicles",
        "vehicle_search",
        {
            f"{field_name}[{index}]": item
            for field_name, values in (exclusions or {}).items()
            for index, item in enumerate(values)
        },
        observed_at=observed_at,
        freshness=EvidenceFreshness.LIVE,
        authority=EvidenceAuthority.HARNESS,
    )
    result = _items(
        "search_vehicles",
        "vehicle_search",
        {"result_count": page.total_items, "total_pages": page.total_pages},
        observed_at=observed_at,
        freshness=EvidenceFreshness.LIVE,
    )
    vehicles = tuple(
        fact
        for item in page.items
        for fact in vehicle_evidence(item, source_operation="search_vehicles")
    )
    return query + exclusion_facts + result + vehicles


def availability_evidence(
    value: VehicleAvailability,
    *,
    observed_at: datetime,
) -> tuple[EvidenceRecord, ...]:
    facts = _items(
        "check_vehicle_availability",
        value.vehicle_id,
        {
            "status": value.status,
            "can_enquire": value.can_enquire,
            "can_book_test_drive": value.can_book_test_drive,
            "can_register_interest": value.can_register_interest,
        },
        observed_at=observed_at,
        freshness=EvidenceFreshness.LIVE,
    )
    if value.next_test_drive_slot is None:
        return facts
    return facts + _items(
        "check_vehicle_availability",
        value.vehicle_id,
        {
            "next_test_drive_slot.id": value.next_test_drive_slot.id,
            "next_test_drive_slot.starts_at": value.next_test_drive_slot.starts_at,
        },
        observed_at=observed_at,
        freshness=EvidenceFreshness.LIVE,
    )


def offer_evidence(value: VehicleOffer, *, observed_at: datetime) -> tuple[EvidenceRecord, ...]:
    facts = _items(
        "list_new_car_offers",
        value.id,
        {
            "make": value.make,
            "model": value.model,
            "title": value.title,
            "product_type": value.product_type,
            "apr_percent": value.apr_percent,
            "term_months": value.term_months,
            "annual_mileage": value.annual_mileage,
            "expires_on": value.expires_on,
            "description": value.description,
        },
        observed_at=observed_at,
        freshness=EvidenceFreshness.SNAPSHOT,
    )
    return (
        facts
        + _money("list_new_car_offers", value.id, "monthly_price", value.monthly_price, observed_at=observed_at, freshness=EvidenceFreshness.SNAPSHOT)
        + _money("list_new_car_offers", value.id, "upfront_price", value.upfront_price, observed_at=observed_at, freshness=EvidenceFreshness.SNAPSHOT)
    )


def test_drive_slot_evidence(value: TestDriveSlot, *, observed_at: datetime) -> tuple[EvidenceRecord, ...]:
    return _items(
        "find_test_drive_slots",
        value.id,
        {
            "dealership_id": value.dealership_id,
            "vehicle_id": value.vehicle_id,
            "starts_at": value.starts_at,
            "dealership_name": value.dealership_name,
            "vehicle_label": value.vehicle_label,
        },
        observed_at=observed_at,
        freshness=EvidenceFreshness.LIVE,
    )


def result_count_evidence(
    source_operation: str,
    entity_id: str,
    count: int,
    *,
    observed_at: datetime,
    freshness: EvidenceFreshness,
) -> tuple[EvidenceRecord, ...]:
    return _items(
        source_operation,
        entity_id,
        {"result_count": count},
        observed_at=observed_at,
        freshness=freshness,
    )


def workshop_service_evidence(value: WorkshopService, *, observed_at: datetime) -> tuple[EvidenceRecord, ...]:
    facts = _items(
        "list_workshop_services",
        value.id,
        {
            "name": value.name,
            "description": value.description,
            "duration_minutes": value.duration_minutes,
        },
        observed_at=observed_at,
        freshness=EvidenceFreshness.STABLE,
    )
    return facts + _money(
        "list_workshop_services",
        value.id,
        "price_from",
        value.price_from,
        observed_at=observed_at,
        freshness=EvidenceFreshness.STABLE,
    )


def workshop_slot_evidence(value: WorkshopSlot, *, observed_at: datetime) -> tuple[EvidenceRecord, ...]:
    facts = _items(
        "find_workshop_slots",
        value.id,
        {
            "dealership_id": value.dealership_id,
            "service_type_id": value.service_type_id,
            "starts_at": value.starts_at,
            "dealership_name": value.dealership_name,
            "service_name": value.service_name,
            "duration_minutes": value.duration_minutes,
        },
        observed_at=observed_at,
        freshness=EvidenceFreshness.LIVE,
    )
    return facts + _money(
        "find_workshop_slots",
        value.id,
        "price_from",
        value.price_from,
        observed_at=observed_at,
        freshness=EvidenceFreshness.LIVE,
    )


def location_evidence(
    value: DealerLocation,
    *,
    source_operation: str,
    observed_at: datetime,
) -> tuple[EvidenceRecord, ...]:
    return _items(
        source_operation,
        value.id,
        {
            "name": value.name,
            "address.lines": ", ".join(value.address.lines),
            "address.town": value.address.town,
            "address.postcode": value.address.postcode,
            "address.country": value.address.country,
            "phone": value.phone,
            "email": value.email,
            "latitude": value.latitude,
            "longitude": value.longitude,
            **{f"brands[{index}]": brand for index, brand in enumerate(value.brands)},
        },
        observed_at=observed_at,
        freshness=EvidenceFreshness.STABLE,
    )


def opening_hours_evidence(value: OpeningHours, *, observed_at: datetime) -> tuple[EvidenceRecord, ...]:
    facts: list[EvidenceRecord] = list(_items(
        "get_dealership_hours",
        value.dealership_id,
        {"timezone": value.timezone},
        observed_at=observed_at,
        freshness=EvidenceFreshness.STABLE,
    ))
    for index, period in enumerate(value.regular):
        facts.extend(_items(
            "get_dealership_hours",
            value.dealership_id,
            {
                f"regular[{index}].department": period.department,
                f"regular[{index}].day_of_week": period.day_of_week,
                f"regular[{index}].opens_at": period.opens_at,
                f"regular[{index}].closes_at": period.closes_at,
                f"regular[{index}].closed": period.is_closed,
            },
            observed_at=observed_at,
            freshness=EvidenceFreshness.STABLE,
        ))
    for index, period in enumerate(value.holidays):
        facts.extend(_items(
            "get_dealership_hours",
            value.dealership_id,
            {
                f"holidays[{index}].date": period.date,
                f"holidays[{index}].label": period.label,
                f"holidays[{index}].department": period.department,
                f"holidays[{index}].opens_at": period.opens_at,
                f"holidays[{index}].closes_at": period.closes_at,
                f"holidays[{index}].closed": period.is_closed,
            },
            observed_at=observed_at,
            freshness=EvidenceFreshness.STABLE,
        ))
    return tuple(facts)


def business_information_evidence(value: BusinessInformation, *, observed_at: datetime) -> tuple[EvidenceRecord, ...]:
    return _items(
        "get_business_information",
        value.organisation,
        {
            "organisation": value.organisation,
            "currency": value.currency,
            "market": value.market,
            "finance_notice": value.finance_notice,
            "finance_minimum_age": value.finance_minimum_age,
            "part_exchange_notice": value.part_exchange_notice,
            "privacy_contact": value.privacy_contact,
        },
        observed_at=observed_at,
        freshness=EvidenceFreshness.STABLE,
    )


def workshop_booking_evidence(
    value: WorkshopBookingDetails,
    *,
    observed_at: datetime,
) -> tuple[EvidenceRecord, ...]:
    booking = value.booking
    facts = _items(
        "retrieve_workshop_booking",
        booking.id,
        {
            "reference": booking.reference,
            "slot_id": booking.slot_id,
            "dealership_id": booking.dealership_id,
            "service_type_id": booking.service_type_id,
            "mileage": booking.mileage,
            "status": booking.status,
            "starts_at": value.starts_at,
            "service_type_name": value.service_type_name,
            "dealership_name": value.dealership.name,
        },
        observed_at=observed_at,
        freshness=EvidenceFreshness.LIVE,
    )
    return facts + location_evidence(
        value.dealership,
        source_operation="retrieve_workshop_booking",
        observed_at=observed_at,
    )
