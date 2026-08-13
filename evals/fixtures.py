"""Isolated, dealer-independent fixtures used by the conversation corpus."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from webchat.domain import (
    Address, BookingStatus, BusinessInformation, Callback, CallbackRequest,
    CallbackStatus, ContactMethod, CustomerIdentity, DealerError, DealerErrorKind,
    DealerFailure, DealerLocation, DealershipMessage, DealershipMessageRequest,
    Department, EnquiryType, HolidayOpening, InterestStatus, Money, OfferSearch,
    OpeningHours, OpeningPeriod, Page, PartExchangeRequest, PartExchangeStatus,
    PartExchangeValuation, ReceivedStatus, SalesEnquiry, SalesEnquiryRequest,
    TestDriveBooking, TestDriveBookingRequest, TestDriveSlot, TestDriveSlotSearch,
    Vehicle, VehicleAvailability, VehicleAvailabilityStatus, VehicleDetails,
    VehicleInterest, VehicleInterestRequest, VehicleOffer, VehicleSearch,
    WorkshopBooking, WorkshopBookingAmendment, WorkshopBookingDetails,
    WorkshopBookingLookup, WorkshopBookingRequest, WorkshopCancellationRequest,
    WorkshopService, WorkshopSlot, WorkshopSlotSearch,
)
from webchat.harness.actions import (
    PendingAction, PendingActionState, PendingActionType, PendingRequestType,
)
from webchat.harness.state import (
    ConversationState, CustomerState, EntityContext, PresentationGroup,
    PresentationProvenance, PresentedEntity, VehiclePreferences,
    VerificationGrant, WorkflowDomain, WorkflowStage, WorkflowState,
)


STATE_FIXTURES = frozenset({
    "empty", "customer_known", "selected_vehicle_slot_customer",
    "confirmed_test_drive_pending", "vehicles_001_002_presented",
    "selected_veh_019", "bmw_search_under_40000", "selected_veh_001",
    "selected_veh_007", "selected_veh_013", "selected_vehicle_and_slot",
    "failed_test_drive_idempotency_conflict", "selected_veh_001_customer_known",
    "service_full_selected", "mot_manchester_selected",
    "full_service_bolton_selected", "workshop_slot_selected_no_customer",
    "workshop_slot_customer_known", "confirmed_workshop_pending",
    "verified_booking_001_new_slot_selected", "unverified_booking_id_known",
    "verified_cancelled_booking", "verified_booking_001",
    "confirmed_cancellation_pending", "workshop_booking_pending",
    "restored_selected_veh_001", "workshop_stockport_search",
    "vehicle_results_page_1", "test_drive_slots_presented_customer_known",
    "test_drive_booking_pending", "selected_x3",
})

DEALER_FIXTURES = frozenset({
    "seeded", "bank_holiday", "no_matching_vehicles", "stale_test_drive_slot",
    "idempotent_test_drive", "idempotency_conflict", "bolton_first_week_empty",
    "stale_workshop_slot", "cancelled_booking", "idempotent_cancellation",
    "lookup_amend_cancel_sequence", "vehicle_test_drive_sequence", "seeded_x3",
    "hours_temporary_failure_after_vehicle_search",
})


@dataclass(frozen=True, slots=True)
class DealerCall:
    operation: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DealerSnapshot:
    calls: tuple[DealerCall, ...]
    side_effects: tuple[str, ...]


def _customer() -> CustomerIdentity:
    return CustomerIdentity("Jamie", "Taylor", "jamie@example.com", "07700900123")


def _location(identifier: str, town: str, phone: str) -> DealerLocation:
    return DealerLocation(
        identifier, f"Northstar {town}", (Address(("1 Northstar Way",), town, "M1 1AA", "UK")),
        phone, f"{town.lower()}@northstar.example", Decimal("53.48"),
        Decimal("-2.24"), ("BMW", "MINI", "Volvo"),
    )


def _vehicle(
    identifier: str, make: str, model: str, price_minor: int | None,
    *, status: VehicleAvailabilityStatus = VehicleAvailabilityStatus.AVAILABLE,
    body: str = "SUV", fuel: str = "Petrol", transmission: str = "Automatic",
    now: datetime,
) -> Vehicle:
    return Vehicle(
        identifier, "northstar-manchester", "Northstar Manchester", "Manchester",
        make, model, "Sport", 2024,
        None if price_minor is None else Money(price_minor, "GBP"), None, 8_000,
        fuel, transmission, "Blue", body, status, f"REG {identifier}",
        "Dealer supplied vehicle description", (f"/{identifier}.jpg",), now,
    )


def _presented(
    group_id: str, entity_type: str, identifiers: tuple[str, ...], now: datetime,
) -> PresentationGroup:
    return PresentationGroup(
        group_id, entity_type,
        tuple(PresentedEntity(identifier, index, {
            "id": identifier, "action_id": f"{group_id}:{identifier}",
        })
              for index, identifier in enumerate(identifiers, 1)),
        now, PresentationProvenance.DEALER_API,
    )


def _workflow(
    domain: WorkflowDomain, stage: WorkflowStage = WorkflowStage.DISCOVERY,
    **gathered: Any,
) -> WorkflowState:
    return WorkflowState(domain=domain, stage=stage, gathered_fields=gathered)


def _pending(
    now: datetime, action_type: PendingActionType, request_type: PendingRequestType,
    payload: dict[str, Any], *, state: PendingActionState = PendingActionState.AWAITING_CONFIRMATION,
    failure: DealerFailure | None = None,
) -> PendingAction:
    return PendingAction.from_mapping(
        action_id="pending-action", action_type=action_type, request_type=request_type,
        request_payload=payload, state=state, idempotency_key="idem-corpus-1",
        created_at=now, expires_at=now + timedelta(minutes=30),
        attempt_count=1 if failure else 0, last_failure=failure,
    )


def _test_drive_payload() -> dict[str, Any]:
    return {
        "slot_id": "slot-2", "vehicle_id": "veh-001",
        "customer": {"first_name": "Jamie", "last_name": "Taylor",
                     "email": "jamie@example.com", "phone": "07700900123"},
    }


def _workshop_payload() -> dict[str, Any]:
    return {
        "slot_id": "ws-slot-1", "registration": "AB12 CDE", "mileage": 50_000,
        "customer": {"first_name": "Jamie", "last_name": "Taylor",
                     "email": "jamie@example.com", "phone": "07700900123"},
    }


def build_state(name: str, *, now: datetime) -> ConversationState:
    if name not in STATE_FIXTURES:
        raise ValueError(f"unknown state fixture: {name}")
    customer = CustomerState("Jamie", "Taylor", "jamie@example.com", "07700900123", "AB12 CDE")
    if name == "empty":
        return ConversationState()
    if name == "customer_known":
        return ConversationState(customer=customer)
    selected = {
        "selected_veh_019": "veh-019", "selected_veh_001": "veh-001",
        "selected_veh_007": "veh-007", "selected_veh_013": "veh-013",
        "restored_selected_veh_001": "veh-001", "selected_x3": "veh-001",
    }
    if name in selected:
        return ConversationState(entities=EntityContext(selected_vehicle_id=selected[name]))
    if name == "selected_veh_001_customer_known":
        return ConversationState(customer=customer, entities=EntityContext(selected_vehicle_id="veh-001"))
    if name == "vehicles_001_002_presented":
        return ConversationState(presentation_groups=(_presented("vehicle-results", "vehicle", ("veh-001", "veh-002"), now),))
    if name == "bmw_search_under_40000":
        return ConversationState(
            preferences=VehiclePreferences(maximum_price_minor=4_000_000, currency="GBP", makes=("BMW",)),
            workflow=_workflow(WorkflowDomain.VEHICLES, last_vehicle_search={"make": "BMW", "max_price_minor": 4_000_000, "currency": "GBP", "page": 1}),
            presentation_groups=(_presented("bmw-results", "vehicle", ("veh-001", "veh-002"), now),),
        )
    if name in {"selected_vehicle_and_slot", "selected_vehicle_slot_customer"}:
        return ConversationState(
            customer=customer if name.endswith("customer") else CustomerState(),
            entities=EntityContext(selected_vehicle_id="veh-001", selected_test_drive_slot_id="slot-2"),
        )
    if name in {"confirmed_test_drive_pending", "test_drive_booking_pending"}:
        return ConversationState(customer=customer, pending_action=_pending(
            now, PendingActionType.TEST_DRIVE_BOOKING, PendingRequestType.TEST_DRIVE_BOOKING,
            _test_drive_payload(),
        ))
    if name == "failed_test_drive_idempotency_conflict":
        failure = DealerFailure(DealerErrorKind.IDEMPOTENCY_CONFLICT)
        return ConversationState(customer=customer, pending_action=_pending(
            now, PendingActionType.TEST_DRIVE_BOOKING, PendingRequestType.TEST_DRIVE_BOOKING,
            _test_drive_payload(), state=PendingActionState.FAILED, failure=failure,
        ))
    if name == "service_full_selected":
        return ConversationState(workflow=_workflow(WorkflowDomain.WORKSHOP, service_type_id="full-service"))
    if name in {"mot_manchester_selected", "full_service_bolton_selected", "workshop_stockport_search"}:
        dealer_id, service_id = {
            "mot_manchester_selected": ("northstar-manchester", "mot"),
            "full_service_bolton_selected": ("northstar-bolton", "full-service"),
            "workshop_stockport_search": ("northstar-stockport", "mot"),
        }[name]
        return ConversationState(
            entities=EntityContext(selected_dealer_id=dealer_id),
            workflow=_workflow(WorkflowDomain.WORKSHOP, dealership_id=dealer_id, service_type_id=service_id),
        )
    if name in {"workshop_slot_selected_no_customer", "workshop_slot_customer_known"}:
        return ConversationState(
            customer=customer if name.endswith("customer_known") else CustomerState(registration="AB12 CDE"),
            entities=EntityContext(selected_dealer_id="northstar-manchester", selected_workshop_slot_id="ws-slot-1"),
            workflow=_workflow(
                WorkflowDomain.WORKSHOP,
                service_type_id="mot",
                mileage=50_000,
            ),
        )
    if name == "confirmed_workshop_pending":
        return ConversationState(customer=customer, pending_action=_pending(
            now, PendingActionType.WORKSHOP_BOOKING, PendingRequestType.WORKSHOP_BOOKING,
            _workshop_payload(),
        ))
    if name in {"verified_booking_001", "verified_booking_001_new_slot_selected", "verified_cancelled_booking"}:
        entities = EntityContext(selected_workshop_slot_id="ws-slot-2") if "new_slot" in name else EntityContext()
        return ConversationState(
            entities=entities, verification_grants=(VerificationGrant.issue("wsb-seeded-001", now=now),),
            workflow=_workflow(WorkflowDomain.WORKSHOP, booking_id="wsb-seeded-001"),
        )
    if name == "unverified_booking_id_known":
        return ConversationState(workflow=_workflow(WorkflowDomain.WORKSHOP, booking_id="wsb-seeded-001"))
    if name == "confirmed_cancellation_pending":
        action = _pending(
            now, PendingActionType.WORKSHOP_CANCELLATION, PendingRequestType.WORKSHOP_CANCELLATION,
            {"booking_id": "wsb-seeded-001"},
        )
        return ConversationState(
            pending_action=action, verification_grants=(VerificationGrant.issue("wsb-seeded-001", now=now),),
        )
    if name == "workshop_booking_pending":
        return ConversationState(
            pending_action=_pending(now, PendingActionType.WORKSHOP_BOOKING, PendingRequestType.WORKSHOP_BOOKING, _workshop_payload()),
            workflow=_workflow(WorkflowDomain.WORKSHOP, stage=WorkflowStage.REVIEWING_ACTION),
        )
    if name == "vehicle_results_page_1":
        return ConversationState(
            workflow=_workflow(WorkflowDomain.VEHICLES, last_vehicle_search={"page": 1, "page_size": 2}),
            presentation_groups=(_presented("vehicle-results", "vehicle", ("veh-001", "veh-002"), now),),
        )
    if name == "test_drive_slots_presented_customer_known":
        return ConversationState(
            customer=customer, entities=EntityContext(selected_vehicle_id="veh-001"),
            presentation_groups=(_presented("test-drive-slots", "test_drive_slot", ("slot-1", "slot-2"), now),),
        )
    raise AssertionError(f"state fixture not implemented: {name}")


class RecordingDealer:
    """A fresh, protocol-shaped dealership simulator for one scenario."""

    def __init__(self, fixture: str, *, now: datetime) -> None:
        self.fixture = fixture
        self.now = now
        self._calls: list[DealerCall] = []
        self._side_effects: list[str] = []
        self._idempotent: dict[tuple[str, str], Any] = {}
        self._bookings = {"wsb-seeded-001": self._workshop_booking()}
        self._vehicles = (
            _vehicle("veh-001", "BMW", "X3", 2_899_500, now=now),
            _vehicle("veh-002", "BMW", "3 Series", 3_499_500, body="Saloon", now=now),
            _vehicle("veh-003", "BMW", "X1", 2_499_500, now=now),
            _vehicle("veh-004", "MINI", "Cooper", 1_999_500, body="Hatchback", fuel="Hybrid", now=now),
            _vehicle("veh-007", "BMW", "X5", 4_999_500, status=VehicleAvailabilityStatus.RESERVED, now=now),
            _vehicle("veh-013", "Volvo", "XC60", 4_199_500, status=VehicleAvailabilityStatus.SOLD, now=now),
            _vehicle("veh-019", "BMW", "iX", None, fuel="Electric", now=now),
            _vehicle("veh-020", "Volvo", "XC40", 3_099_500, now=now),
        )

    def snapshot(self) -> DealerSnapshot:
        return DealerSnapshot(tuple(self._calls), tuple(self._side_effects))

    def _record(self, operation: str, **arguments: Any) -> None:
        self._calls.append(DealerCall(operation, arguments))

    def _effect(self, name: str) -> None:
        self._side_effects.append(name)

    def _find_vehicle(self, identifier: str) -> Vehicle:
        vehicle = next(
            (vehicle for vehicle in self._vehicles if vehicle.id == identifier),
            None,
        )
        if vehicle is None:
            raise DealerError(
                DealerFailure(
                    DealerErrorKind.NOT_FOUND,
                    resource=identifier,
                )
            )
        return vehicle

    def _locations(self) -> tuple[DealerLocation, ...]:
        return (
            _location("northstar-manchester", "Manchester", "0161 555 0101"),
            _location("northstar-stockport", "Stockport", "0161 555 0102"),
            _location("northstar-liverpool", "Liverpool", "0151 555 0199"),
            _location("northstar-bolton", "Bolton", "01204 555 0104"),
        )

    def _test_slots(self) -> tuple[TestDriveSlot, ...]:
        ids = ("slot-1", "slot-2", "slot-3")
        if self.fixture == "stale_test_drive_slot":
            ids = ("td-slot-new",)
        return tuple(TestDriveSlot(
            identifier, "northstar-manchester", "veh-001",
            self.now + timedelta(days=1, hours=index), "Northstar Manchester", "BMW X3",
        ) for index, identifier in enumerate(ids, 1))

    def _workshop_slots(self) -> tuple[WorkshopSlot, ...]:
        ids = ("ws-slot-1", "ws-slot-2", "ws-slot-3")
        if self.fixture == "stale_workshop_slot":
            ids = ("ws-slot-new",)
        return tuple(WorkshopSlot(
            identifier, "northstar-manchester", "mot",
            self.now + timedelta(days=2, hours=index), "Northstar Manchester", "MOT",
            60, Money(5_000, "GBP"),
        ) for index, identifier in enumerate(ids, 1))

    def _workshop_booking(self, *, status: BookingStatus = BookingStatus.CONFIRMED) -> WorkshopBooking:
        return WorkshopBooking(
            "wsb-seeded-001", "WORK-10001", "ws-slot-1", "northstar-manchester", "mot",
            _customer(), "AB12 CDE", 50_000, None, status, self.now, self.now,
            self.now if status is BookingStatus.CANCELLED else None,
        )

    async def search_vehicles(self, search: VehicleSearch) -> Page[Vehicle]:
        self._record("search_vehicles", search=search)
        if self.fixture == "no_matching_vehicles":
            items: list[Vehicle] = []
        else:
            items = [item for item in self._vehicles
                     if (search.make is None or item.make.casefold() == search.make.casefold())
                     and (search.fuel_type is None or item.fuel_type.casefold() == search.fuel_type.casefold())
                     and (search.transmission is None or item.transmission.casefold() == search.transmission.casefold())
                     and (search.body_style is None or item.body_style.casefold() == search.body_style.casefold())
                     and (search.max_price is None or item.price is None or item.price.amount_minor <= search.max_price.amount_minor)]
        start = (search.page - 1) * search.page_size
        page_items = tuple(items[start:start + search.page_size])
        total_pages = max(1, (len(items) + search.page_size - 1) // search.page_size)
        return Page(page_items, search.page, search.page_size, len(items), total_pages)

    async def get_vehicle(self, vehicle_id: str) -> VehicleDetails:
        self._record("get_vehicle", vehicle_id=vehicle_id)
        return VehicleDetails(self._find_vehicle(vehicle_id), ("Large boot", "Flexible rear seats"))

    async def get_vehicle_availability(self, vehicle_id: str) -> VehicleAvailability:
        self._record("get_vehicle_availability", vehicle_id=vehicle_id)
        vehicle = self._find_vehicle(vehicle_id)
        return VehicleAvailability(
            vehicle_id, vehicle.availability, True,
            vehicle.availability is VehicleAvailabilityStatus.AVAILABLE,
            vehicle.availability is VehicleAvailabilityStatus.RESERVED, None,
        )

    async def list_offers(self, search: OfferSearch) -> tuple[VehicleOffer, ...]:
        self._record("list_offers", search=search)
        return (VehicleOffer("offer-1", "BMW", "iX1", "BMW iX1 offer", "pcp",
                             Money(49_900, "GBP"), Money(499_900, "GBP"), Decimal("5.9"),
                             48, 8_000, date(2026, 12, 31), "Published offer terms", "/offer.jpg"),)

    async def get_offer(self, offer_id: str) -> VehicleOffer:
        self._record("get_offer", offer_id=offer_id)
        return (await self.list_offers(OfferSearch()))[0]

    async def create_sales_enquiry(self, request: SalesEnquiryRequest, *, idempotency_key: str) -> SalesEnquiry:
        self._record("create_sales_enquiry", request=request, idempotency_key=idempotency_key)
        self._effect("sales_enquiry")
        return SalesEnquiry("enq-1", "ENQ-1", request, ReceivedStatus.RECEIVED, self.now)

    async def list_test_drive_slots(self, search: TestDriveSlotSearch) -> tuple[TestDriveSlot, ...]:
        self._record("list_test_drive_slots", search=search)
        return self._test_slots()

    async def book_test_drive(self, request: TestDriveBookingRequest, *, idempotency_key: str) -> TestDriveBooking:
        self._record("book_test_drive", request=request, idempotency_key=idempotency_key)
        if self.fixture == "idempotency_conflict":
            raise DealerError(DealerFailure(DealerErrorKind.IDEMPOTENCY_CONFLICT))
        key = ("test_drive_booking", idempotency_key)
        if key not in self._idempotent:
            self._effect("test_drive_booking")
            self._idempotent[key] = TestDriveBooking(
                "tdb-1", "TEST-1", request, "northstar-manchester", "veh-001",
                BookingStatus.CONFIRMED, self.now,
            )
        return self._idempotent[key]

    async def register_vehicle_interest(self, request: VehicleInterestRequest, *, idempotency_key: str) -> VehicleInterest:
        self._record("register_vehicle_interest", request=request, idempotency_key=idempotency_key)
        self._effect("vehicle_interest")
        return VehicleInterest("interest-1", "INT-1", request, "northstar-manchester", InterestStatus.REGISTERED, self.now)

    async def request_callback(self, request: CallbackRequest, *, idempotency_key: str) -> Callback:
        self._record("request_callback", request=request, idempotency_key=idempotency_key)
        self._effect("callback")
        return Callback("callback-1", "CALL-1", request, CallbackStatus.REQUESTED, self.now)

    async def value_part_exchange(self, request: PartExchangeRequest, *, idempotency_key: str) -> PartExchangeValuation:
        self._record("value_part_exchange", request=request, idempotency_key=idempotency_key)
        self._effect("part_exchange")
        return PartExchangeValuation("px-1", "PX-1", request, Money(900_000, "GBP"),
                                     Money(1_100_000, "GBP"), PartExchangeStatus.ESTIMATED,
                                     self.now, "Subject to inspection")

    async def list_service_types(self) -> tuple[WorkshopService, ...]:
        self._record("list_service_types")
        return (WorkshopService("mot", "MOT", "Annual MOT", 60, Money(5_000, "GBP")),
                WorkshopService("full-service", "Full service", "Comprehensive service", 180, Money(25_000, "GBP")))

    async def list_workshop_locations(self) -> tuple[DealerLocation, ...]:
        self._record("list_workshop_locations")
        return self._locations()

    async def list_workshop_slots(self, search: WorkshopSlotSearch) -> tuple[WorkshopSlot, ...]:
        self._record("list_workshop_slots", search=search)
        if self.fixture == "bolton_first_week_empty" and search.dealership_id == "northstar-bolton":
            return ()
        return self._workshop_slots()

    async def book_workshop(self, request: WorkshopBookingRequest, *, idempotency_key: str) -> WorkshopBooking:
        self._record("book_workshop", request=request, idempotency_key=idempotency_key)
        self._effect("workshop_booking")
        booking = replace(self._workshop_booking(), slot_id=request.slot_id, customer=request.customer,
                          registration=request.registration, mileage=request.mileage, notes=request.notes)
        self._bookings[booking.id] = booking
        return booking

    async def lookup_workshop_booking(self, lookup: WorkshopBookingLookup) -> WorkshopBookingDetails:
        self._record("lookup_workshop_booking", lookup=lookup)
        if lookup.phone != "07700900123":
            raise DealerError(DealerFailure(DealerErrorKind.VERIFICATION_FAILED))
        booking = self._bookings["wsb-seeded-001"]
        return WorkshopBookingDetails(booking, self.now + timedelta(days=2), "MOT", self._locations()[0])

    async def get_workshop_booking(self, booking_id: str) -> WorkshopBooking:
        self._record("get_workshop_booking", booking_id=booking_id)
        if self.fixture == "cancelled_booking":
            return self._workshop_booking(status=BookingStatus.CANCELLED)
        return self._bookings[booking_id]

    async def amend_workshop_booking(self, amendment: WorkshopBookingAmendment) -> WorkshopBooking:
        self._record("amend_workshop_booking", amendment=amendment)
        self._effect("workshop_amendment")
        current = self._bookings[amendment.booking_id]
        updated = replace(current, slot_id=amendment.slot_id or current.slot_id,
                          mileage=amendment.mileage or current.mileage,
                          notes=amendment.notes if amendment.notes is not None else current.notes,
                          updated_at=self.now)
        self._bookings[updated.id] = updated
        return updated

    async def cancel_workshop_booking(self, request: WorkshopCancellationRequest) -> WorkshopBooking:
        self._record("cancel_workshop_booking", request=request)
        current = self._bookings[request.booking_id]
        if current.status is not BookingStatus.CANCELLED:
            self._effect("workshop_cancellation")
            current = replace(current, status=BookingStatus.CANCELLED, cancelled_at=self.now, updated_at=self.now)
            self._bookings[current.id] = current
        return current

    async def list_dealerships(self) -> tuple[DealerLocation, ...]:
        self._record("list_dealerships")
        return self._locations()

    async def get_dealership(self, dealership_id: str) -> DealerLocation:
        self._record("get_dealership", dealership_id=dealership_id)
        location = next(
            (item for item in self._locations() if item.id == dealership_id),
            None,
        )
        if location is None:
            raise DealerError(
                DealerFailure(
                    DealerErrorKind.NOT_FOUND,
                    resource=dealership_id,
                )
            )
        return location

    async def get_opening_hours(self, dealership_id: str, department: Department | None = None) -> OpeningHours:
        self._record("get_opening_hours", dealership_id=dealership_id, department=department)
        if self.fixture == "hours_temporary_failure_after_vehicle_search":
            raise DealerError(DealerFailure(DealerErrorKind.TEMPORARY_FAILURE, retryable=True))
        departments = (department,) if department else (Department.SALES, Department.SERVICE, Department.PARTS)
        regular = tuple(OpeningPeriod(item, 5, time(9), time(17)) for item in departments)
        holidays: tuple[HolidayOpening, ...] = ()
        if self.fixture == "bank_holiday":
            holiday_date = self.now.date() + timedelta(days=12)
            holidays = tuple(HolidayOpening(
                holiday_date, "Summer bank holiday", item,
                time(10) if item is Department.SALES else None,
                time(16) if item is Department.SALES else None,
            ) for item in departments)
        return OpeningHours(dealership_id, "Europe/London", regular, holidays)

    async def send_dealership_message(self, request: DealershipMessageRequest, *, idempotency_key: str) -> DealershipMessage:
        self._record("send_dealership_message", request=request, idempotency_key=idempotency_key)
        self._effect("dealership_message")
        return DealershipMessage("message-1", "MSG-1", request, ReceivedStatus.RECEIVED, self.now)

    async def get_business_information(self) -> BusinessInformation:
        self._record("get_business_information")
        return BusinessInformation(
            "Northstar Motors", "GBP", "United Kingdom",
            "Finance is subject to status and terms.", 18,
            "Part-exchange values are estimates subject to inspection.",
            "privacy@northstar.example",
        )


@dataclass(frozen=True, slots=True)
class FixtureRegistry:
    now: datetime

    @property
    def state_names(self) -> tuple[str, ...]:
        return tuple(sorted(STATE_FIXTURES))

    @property
    def dealer_names(self) -> tuple[str, ...]:
        return tuple(sorted(DEALER_FIXTURES))

    def build_state(self, name: str) -> ConversationState:
        return build_state(name, now=self.now)

    def build_dealer(self, name: str) -> RecordingDealer:
        if name not in DEALER_FIXTURES:
            raise ValueError(f"unknown dealer fixture: {name}")
        return RecordingDealer(name, now=self.now)
