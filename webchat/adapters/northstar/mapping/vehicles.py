"""Northstar vehicle inventory, availability, and offer mappings."""

from urllib.parse import urlsplit

from webchat.domain import (
    AvailabilitySlot,
    Money,
    OfferSearch,
    Page,
    Vehicle,
    VehicleAvailability,
    VehicleAvailabilityStatus,
    VehicleDetails,
    VehicleOffer,
    VehicleSearch,
    VehicleSort,
)

from ..config import NorthstarConfig
from ..errors import invalid_request, invalid_response
from .common import (
    JsonObject,
    money_from_minor,
    read_bool,
    read_date,
    read_datetime,
    read_int,
    read_items,
    read_object,
    read_optional_decimal,
    read_str,
    read_string_tuple,
)


_SORT_VALUES = {
    VehicleSort.NEWEST: "newest",
    VehicleSort.PRICE_ASC: "priceAsc",
    VehicleSort.PRICE_DESC: "priceDesc",
    VehicleSort.MILEAGE_ASC: "mileageAsc",
}


def vehicle_search_to_params(search: VehicleSearch, config: NorthstarConfig) -> dict[str, object]:
    for price in (search.min_price, search.max_price):
        if price is not None and price.currency != config.currency:
            raise invalid_request(resource="vehicle_search")
    values = {
        "q": search.query,
        "make": search.make,
        "model": search.model,
        "fuelType": search.fuel_type,
        "transmission": search.transmission,
        "bodyStyle": search.body_style,
        "availability": None if search.availability is None else search.availability.value,
        "dealershipId": search.dealership_id,
        "minPricePence": None if search.min_price is None else search.min_price.amount_minor,
        "maxPricePence": None if search.max_price is None else search.max_price.amount_minor,
        "maxMileage": search.max_mileage,
        "minYear": search.min_year,
        "sort": _SORT_VALUES[search.sort],
        "page": search.page,
        "pageSize": search.page_size,
    }
    return {key: value for key, value in values.items() if value is not None}


def vehicles_from_payload(payload: object, config: NorthstarConfig) -> Page[Vehicle]:
    body = read_object(payload)
    pagination = read_object(body.get("pagination"))
    items = tuple(vehicle_from_payload(item, config) for item in read_items(body))
    try:
        return Page(
            items=items,
            page=read_int(pagination, "page"),
            page_size=read_int(pagination, "pageSize"),
            total_items=read_int(pagination, "totalItems"),
            total_pages=read_int(pagination, "totalPages"),
        )
    except ValueError as error:
        raise invalid_response(resource="vehicle_search") from error


def vehicle_from_payload(payload: object, config: NorthstarConfig) -> Vehicle:
    body = read_object(payload)
    try:
        return Vehicle(
            id=read_str(body, "id"),
            dealership_id=read_str(body, "dealershipId"),
            dealership_name=read_str(body, "dealershipName"),
            dealership_town=read_str(body, "dealershipTown"),
            make=read_str(body, "make"),
            model=read_str(body, "model"),
            variant=read_str(body, "variant"),
            year=read_int(body, "year"),
            price=money_from_minor(body, "pricePence", config.currency),
            monthly_price=money_from_minor(body, "monthlyPricePence", config.currency),
            mileage=read_int(body, "mileage"),
            fuel_type=read_str(body, "fuelType"),
            transmission=read_str(body, "transmission"),
            colour=read_str(body, "colour"),
            body_style=read_str(body, "bodyStyle"),
            availability=VehicleAvailabilityStatus(read_str(body, "availability")),
            registration=read_str(body, "registration"),
            description=read_str(body, "description"),
            images=tuple(_asset_url(value, config) for value in read_string_tuple(body, "images")),
            updated_at=read_datetime(body, "updatedAt"),
        )
    except ValueError as error:
        raise invalid_response(resource="vehicle") from error


def vehicle_details_from_payload(payload: object, config: NorthstarConfig) -> VehicleDetails:
    body = read_object(payload)
    highlights = body.get("highlights")
    if not isinstance(highlights, list) or not all(
        isinstance(item, str) and item for item in highlights
    ):
        raise invalid_response(resource="vehicle")
    try:
        return VehicleDetails(
            vehicle=vehicle_from_payload(body, config),
            highlights=tuple(highlights),
        )
    except ValueError as error:
        raise invalid_response(resource="vehicle") from error


def availability_from_payload(payload: object) -> VehicleAvailability:
    body = read_object(payload)
    next_payload = body.get("nextTestDriveSlot")
    if next_payload is None:
        next_slot = None
    else:
        slot = read_object(next_payload)
        try:
            next_slot = AvailabilitySlot(
                id=read_str(slot, "id"),
                starts_at=read_datetime(slot, "startsAt"),
            )
        except ValueError as error:
            raise invalid_response(resource="vehicle_availability") from error
    try:
        return VehicleAvailability(
            vehicle_id=read_str(body, "vehicleId"),
            status=VehicleAvailabilityStatus(read_str(body, "availability")),
            can_enquire=read_bool(body, "canEnquire"),
            can_book_test_drive=read_bool(body, "canBookTestDrive"),
            can_register_interest=read_bool(body, "canRegisterInterest"),
            next_test_drive_slot=next_slot,
        )
    except ValueError as error:
        raise invalid_response(resource="vehicle_availability") from error


def offer_search_to_params(search: OfferSearch) -> dict[str, str]:
    values = {"make": search.make, "productType": search.product_type}
    return {key: value for key, value in values.items() if value is not None}


def offers_from_payload(payload: object, config: NorthstarConfig) -> tuple[VehicleOffer, ...]:
    return tuple(offer_from_payload(item, config) for item in read_items(payload))


def offer_from_payload(payload: object, config: NorthstarConfig) -> VehicleOffer:
    body = read_object(payload)
    monthly_price = _required_money(body, "monthlyPricePence", config.currency)
    upfront_price = _required_money(body, "upfrontPaymentPence", config.currency)
    image = body.get("image")
    if image is not None and not isinstance(image, str):
        raise invalid_response(resource="offer")
    try:
        return VehicleOffer(
            id=read_str(body, "id"),
            make=read_str(body, "make"),
            model=read_str(body, "model"),
            title=read_str(body, "title"),
            product_type=read_str(body, "productType"),
            monthly_price=monthly_price,
            upfront_price=upfront_price,
            apr_percent=read_optional_decimal(body, "apr"),
            term_months=read_int(body, "termMonths"),
            annual_mileage=read_int(body, "annualMileage"),
            expires_on=read_date(body, "expiresOn"),
            description=read_str(body, "description"),
            image_url=None if image is None else _asset_url(image, config),
        )
    except ValueError as error:
        raise invalid_response(resource="offer") from error


def _required_money(payload: JsonObject, key: str, currency: str) -> Money:
    value = money_from_minor(payload, key, currency)
    if value is None:
        raise invalid_response()
    return value


def _asset_url(value: str, config: NorthstarConfig) -> str:
    if value.startswith("/") and not value.startswith("//"):
        return f"{config.base_url}{value}"
    parsed = urlsplit(value)
    base = urlsplit(config.base_url)
    if parsed.scheme in {"http", "https"} and parsed.netloc == base.netloc:
        return value
    raise invalid_response(resource="asset")
