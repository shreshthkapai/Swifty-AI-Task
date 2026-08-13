from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, Mock
import unittest

from webchat.domain.common import (
    Address,
    BookingStatus,
    CustomerIdentity,
    Department,
    Money,
    Page,
)
from webchat.domain.dealer import DealerAdapter
from webchat.domain.dealerships import DealerLocation, OpeningHours, OpeningPeriod
from webchat.domain.sales import (
    TestDriveBooking,
    TestDriveSlot,
)
from webchat.domain.vehicles import (
    Vehicle,
    VehicleAvailability,
    VehicleAvailabilityStatus,
)
from webchat.domain.workshop import WorkshopBooking, WorkshopBookingDetails
from webchat.harness.actions import (
    PendingAction,
    PendingActionState,
    PendingActionType,
    PendingRequestType,
)
from webchat.harness.contracts import (
    PreparationCommand,
    PreparationCommandName,
    ReadCommand,
    ReadCommandName,
    ResponseStrategy,
    TurnPlan,
    TurnScope,
)
from webchat.harness.planning import PlanningEngine, TurnRequest
from webchat.harness.runtime import HarnessRuntime
from webchat.harness.state import (
    ActionReference,
    ConversationState,
    CustomerState,
    EntityContext,
    PageContext,
    WorkflowDomain,
    WorkflowStage,
    WorkflowState,
)
from webchat.providers.base import PlanningResult, ProviderUsage


NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
CUSTOMER = CustomerIdentity("Jamie", "Taylor", "jamie@example.com", "07700900123")


def location() -> DealerLocation:
    return DealerLocation(
        id="northstar-manchester", name="Northstar Manchester",
        address=Address(("101 Kingsway",), "Manchester", "M20 2YY", "UK"),
        phone="0161 555 0101", email="manchester@example.com",
        latitude=Decimal("53.424"), longitude=Decimal("-2.231"),
        brands=("BMW",),
    )


def vehicle(
    vehicle_id: str = "veh-003",
    *,
    status: VehicleAvailabilityStatus = VehicleAvailabilityStatus.AVAILABLE,
    price: Money | None = Money(4_299_500, "GBP"),
) -> Vehicle:
    return Vehicle(
        id=vehicle_id, dealership_id="northstar-manchester",
        dealership_name="Northstar Manchester", dealership_town="Manchester",
        make="BMW", model="X3", variant="xDrive20d M Sport", year=2024,
        price=price, monthly_price=None, mileage=8_000, fuel_type="Diesel",
        transmission="Automatic", colour="Blue", body_style="SUV",
        availability=status, registration="MA24 XYZ", description="Dealer description",
        images=("/x3.jpg",), updated_at=NOW,
    )


def availability(
    status: VehicleAvailabilityStatus = VehicleAvailabilityStatus.AVAILABLE,
) -> VehicleAvailability:
    return VehicleAvailability(
        vehicle_id="veh-003", status=status, can_enquire=True,
        can_book_test_drive=status is VehicleAvailabilityStatus.AVAILABLE,
        can_register_interest=status is VehicleAvailabilityStatus.RESERVED,
        next_test_drive_slot=None,
    )


def drive_slot(slot_id: str = "td-slot-1") -> TestDriveSlot:
    return TestDriveSlot(
        id=slot_id, dealership_id="northstar-manchester", vehicle_id="veh-003",
        starts_at=NOW + timedelta(days=1), dealership_name="Northstar Manchester",
        vehicle_label="BMW X3",
    )


def drive_booking() -> TestDriveBooking:
    from webchat.domain.sales import TestDriveBookingRequest

    request = TestDriveBookingRequest("td-slot-1", CUSTOMER)
    return TestDriveBooking(
        id="tdb-1", reference="TEST-1", request=request,
        dealership_id="northstar-manchester", vehicle_id="veh-003",
        status=BookingStatus.CONFIRMED, created_at=NOW,
    )


def workshop_booking(status: BookingStatus = BookingStatus.CONFIRMED) -> WorkshopBooking:
    return WorkshopBooking(
        id="wsb-1", reference="WORK-1", slot_id="ws-slot-1",
        dealership_id="northstar-manchester", service_type_id="service-mot",
        customer=CUSTOMER, registration="AB12 CDE", mileage=50_000, notes=None,
        status=status, created_at=NOW, updated_at=NOW,
        cancelled_at=NOW if status is BookingStatus.CANCELLED else None,
    )


class FakeProvider:
    def __init__(self, plan: TurnPlan) -> None:
        self.plan_value = plan
        self.requests = []

    async def plan(self, request):
        self.requests.append(request)
        return PlanningResult(
            plan=self.plan_value,
            usage=ProviderUsage(100, 20, 120),
            latency_ms=10,
            provider="fake",
            model="fixture",
        )


def dealer() -> Mock:
    return Mock(spec=DealerAdapter)


def runtime(dealer_fake: Mock, plan: TurnPlan) -> tuple[HarnessRuntime, FakeProvider]:
    provider = FakeProvider(plan)
    identifiers = (f"id-{index}" for index in range(1, 100))
    return (
        HarnessRuntime(
            dealer=dealer_fake,
            planning=PlanningEngine(provider),
            id_factory=lambda: next(identifiers),
        ),
        provider,
    )


class HarnessRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_vehicle_search_updates_preferences_and_renders_unknown_price(self) -> None:
        fake = dealer()
        fake.search_vehicles.return_value = Page((vehicle("veh-019", price=None),), 1, 10, 1, 1)
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(ReadCommandName.SEARCH_VEHICLES, {
                "make": "BMW", "max_price_minor": 3_000_000, "currency": "GBP"
            }),),
            ResponseStrategy.SEARCH_RESULTS,
        )
        harness, provider = runtime(fake, plan)

        result = await harness.handle(TurnRequest(
            current_input="Find a BMW under £30k", state=ConversationState(), now=NOW
        ))

        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(result.model_calls, 1)
        self.assertEqual(result.state.preferences.makes, ("BMW",))
        self.assertEqual(result.state.preferences.maximum_price_minor, 3_000_000)
        cards = next(block for block in result.blocks if block.kind == "vehicle_cards")
        self.assertEqual(cards.to_dict()["payload"]["vehicles"][0]["price"]["display"], "Price on request")
        self.assertEqual(result.state.presentation_groups[-1].entities[0].entity_id, "veh-019")

    async def test_cheaper_refinement_uses_presented_dealer_price_as_exclusive_ceiling(self) -> None:
        fake = dealer()
        fake.search_vehicles.side_effect = [
            Page((vehicle("veh-003", price=Money(4_000_000, "GBP")),), 1, 10, 1, 1),
            Page((vehicle("veh-004", price=Money(3_500_000, "GBP")),), 1, 10, 1, 1),
        ]
        first_plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(ReadCommandName.SEARCH_VEHICLES, {"make": "BMW"}),),
            ResponseStrategy.SEARCH_RESULTS,
        )
        first_runtime, _ = runtime(fake, first_plan)
        first = await first_runtime.handle(TurnRequest(
            current_input="Show me BMWs", state=ConversationState(), now=NOW
        ))
        refine_plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(ReadCommandName.SEARCH_VEHICLES, {"refinement": "lower_max_price"}),),
            ResponseStrategy.SEARCH_RESULTS,
        )
        refine_runtime, _ = runtime(fake, refine_plan)

        refined = await refine_runtime.handle(TurnRequest(
            current_input="Actually cheaper", state=first.state, now=NOW
        ))

        search = fake.search_vehicles.await_args_list[1].args[0]
        self.assertEqual(search.max_price, Money(3_999_999, "GBP"))
        self.assertEqual(refined.state.preferences.maximum_price_minor, 3_999_999)

    async def test_dealer_incompatible_cross_field_arguments_fail_before_adapter_call(self) -> None:
        fake = dealer()
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(ReadCommandName.SEARCH_VEHICLES, {
                "min_price_minor": 5_000_000,
                "max_price_minor": 3_000_000,
                "currency": "GBP",
            }),),
            ResponseStrategy.SEARCH_RESULTS,
        )
        harness, _ = runtime(fake, plan)

        result = await harness.handle(TurnRequest(
            current_input="At least £50k but under £30k", state=ConversationState(), now=NOW
        ))

        fake.search_vehicles.assert_not_awaited()
        self.assertEqual(result.blocks[0].to_dict()["payload"]["code"], "invalid_request")

    async def test_prepare_test_drive_collects_typed_request_without_writing(self) -> None:
        fake = dealer()
        fake.get_vehicle_availability.return_value = availability()
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (PreparationCommand.from_mapping(
                PreparationCommandName.PREPARE_TEST_DRIVE_BOOKING,
                {"slot_id": "td-slot-1"},
            ),),
            ResponseStrategy.ACTION_PREPARED,
        )
        harness, _ = runtime(fake, plan)
        state = ConversationState(
            customer=CustomerState("Jamie", "Taylor", "jamie@example.com", "07700900123"),
            entities=EntityContext(
                selected_vehicle_id="veh-003", selected_test_drive_slot_id="td-slot-1"
            ),
        )

        result = await harness.handle(TurnRequest(
            current_input="Book that test drive", state=state, now=NOW
        ))

        self.assertEqual(result.state.pending_action.action_type, PendingActionType.TEST_DRIVE_BOOKING)
        self.assertEqual(result.state.pending_action.state, PendingActionState.AWAITING_CONFIRMATION)
        fake.book_test_drive.assert_not_called()
        self.assertIn("customer", result.state.pending_action.request_payload_dict())
        self.assertIn("confirmation", {block.kind for block in result.blocks})

    async def test_confirm_revalidates_live_slot_and_double_confirm_writes_once(self) -> None:
        fake = dealer()
        fake.get_vehicle_availability.return_value = availability()
        fake.list_test_drive_slots.return_value = (drive_slot(),)
        fake.book_test_drive.return_value = drive_booking()
        inert = TurnPlan(TurnScope.IN_DOMAIN, (), ResponseStrategy.ACKNOWLEDGEMENT)
        harness, provider = runtime(fake, inert)
        action = PendingAction.from_mapping(
            action_id="pending-1", action_type=PendingActionType.TEST_DRIVE_BOOKING,
            request_type=PendingRequestType.TEST_DRIVE_BOOKING,
            request_payload={
                "slot_id": "td-slot-1", "vehicle_id": "veh-003",
                "customer": {"first_name": "Jamie", "last_name": "Taylor", "email": "jamie@example.com", "phone": "07700900123"},
            },
            state=PendingActionState.AWAITING_CONFIRMATION,
            idempotency_key="idem-1", created_at=NOW, expires_at=NOW + timedelta(minutes=15),
        )
        state = ConversationState(pending_action=action)
        request = lambda current: TurnRequest(
            current_input=None, action_reference=ActionReference("pending-1", "confirm"),
            state=current, now=NOW,
        )

        first = await harness.handle(request(state))
        second = await harness.handle(request(first.state))

        self.assertEqual(provider.requests, [])
        fake.get_vehicle_availability.assert_awaited_once_with("veh-003")
        fake.book_test_drive.assert_awaited_once()
        self.assertEqual(first.state.pending_action.state, PendingActionState.SUCCEEDED)
        self.assertEqual(second.state, first.state)

    async def test_reserved_vehicle_never_reaches_booking_and_offers_safe_alternatives(self) -> None:
        fake = dealer()
        fake.get_vehicle_availability.return_value = availability(VehicleAvailabilityStatus.RESERVED)
        inert = TurnPlan(TurnScope.IN_DOMAIN, (), ResponseStrategy.ACKNOWLEDGEMENT)
        harness, _ = runtime(fake, inert)
        action = PendingAction.from_mapping(
            action_id="pending-1", action_type=PendingActionType.TEST_DRIVE_BOOKING,
            request_type=PendingRequestType.TEST_DRIVE_BOOKING,
            request_payload={
                "slot_id": "td-slot-1", "vehicle_id": "veh-003",
                "customer": {"first_name": "Jamie", "last_name": "Taylor", "email": "jamie@example.com", "phone": "07700900123"},
            },
            state=PendingActionState.AWAITING_CONFIRMATION,
            idempotency_key="idem-1", created_at=NOW, expires_at=NOW + timedelta(minutes=15),
        )

        result = await harness.handle(TurnRequest(
            current_input="confirm", state=ConversationState(pending_action=action), now=NOW
        ))

        fake.book_test_drive.assert_not_called()
        self.assertEqual(result.state.pending_action.state, PendingActionState.FAILED)
        actions = next(block for block in result.blocks if block.kind == "actions")
        self.assertEqual(
            [item["action_type"] for item in actions.to_dict()["payload"]["actions"]],
            ["register_interest", "sales_enquiry"],
        )
        self.assertEqual(
            {item["entity_id"] for item in actions.to_dict()["payload"]["actions"]},
            {"veh-003"},
        )

    async def test_verified_workshop_lookup_issues_short_lived_booking_grant(self) -> None:
        fake = dealer()
        details = WorkshopBookingDetails(
            workshop_booking(), NOW + timedelta(days=1), "MOT", location()
        )
        fake.lookup_workshop_booking.return_value = details
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(ReadCommandName.RETRIEVE_WORKSHOP_BOOKING, {
                "reference": "WORK-1", "last_name": "Taylor",
                "registration": "AB12 CDE", "phone": "07700900123",
            }),),
            ResponseStrategy.BOOKING_DETAILS,
        )
        harness, _ = runtime(fake, plan)

        result = await harness.handle(TurnRequest(
            current_input="Find my workshop booking", state=ConversationState(), now=NOW
        ))

        self.assertTrue(result.state.has_active_grant("wsb-1", now=NOW))
        self.assertFalse(result.state.has_active_grant("wsb-1", now=NOW + timedelta(hours=1)))
        self.assertIn("booking_details", {block.kind for block in result.blocks})

    async def test_workshop_amendment_without_grant_is_blocked_before_dealer_call(self) -> None:
        fake = dealer()
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (PreparationCommand.from_mapping(
                PreparationCommandName.PREPARE_WORKSHOP_AMENDMENT,
                {"booking_id": "wsb-1", "slot_id": "ws-slot-2"},
            ),),
            ResponseStrategy.ACTION_PREPARED,
        )
        harness, _ = runtime(fake, plan)

        result = await harness.handle(TurnRequest(
            current_input="Move my booking", state=ConversationState(), now=NOW
        ))

        fake.get_workshop_booking.assert_not_called()
        fake.amend_workshop_booking.assert_not_called()
        self.assertIsNone(result.state.pending_action)
        self.assertEqual(result.blocks[0].to_dict()["payload"]["code"], "verification_required")

    async def test_dealership_hours_render_department_and_holiday_data(self) -> None:
        fake = dealer()
        fake.get_opening_hours.return_value = OpeningHours(
            dealership_id="northstar-manchester", timezone="Europe/London",
            regular=(OpeningPeriod(Department.SALES, 5, time(9), time(17)),),
            holidays=(),
        )
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(ReadCommandName.GET_DEALERSHIP_HOURS, {
                "dealership_id": "northstar-manchester", "department": "sales"
            }),),
            ResponseStrategy.DEALERSHIP_DETAILS,
        )
        harness, _ = runtime(fake, plan)

        result = await harness.handle(TurnRequest(
            current_input="Is Manchester sales open Saturday?", state=ConversationState(), now=NOW
        ))

        block = next(block for block in result.blocks if block.kind == "opening_hours")
        item = block.to_dict()["payload"]["items"][0]
        self.assertEqual(item["department"], "sales")
        self.assertEqual(item["day_of_week"], 5)

    async def test_general_dealership_hours_request_reads_all_departments(self) -> None:
        fake = dealer()
        fake.get_opening_hours.return_value = OpeningHours(
            dealership_id="northstar-manchester", timezone="Europe/London",
            regular=(OpeningPeriod(Department.SALES, 5, time(9), time(17)),),
            holidays=(),
        )
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(ReadCommandName.GET_DEALERSHIP_HOURS, {
                "dealership_id": "northstar-manchester", "department": "general"
            }),),
            ResponseStrategy.DEALERSHIP_DETAILS,
        )
        harness, _ = runtime(fake, plan)

        await harness.handle(TurnRequest(
            current_input="When is Manchester open?", state=ConversationState(), now=NOW
        ))

        fake.get_opening_hours.assert_awaited_once_with("northstar-manchester", None)

    async def test_compound_safe_reads_execute_in_declared_order_without_agent_loop(self) -> None:
        fake = dealer()
        fake.search_vehicles.return_value = Page((vehicle(),), 1, 10, 1, 1)
        fake.get_opening_hours.return_value = OpeningHours(
            "northstar-manchester", "Europe/London",
            (OpeningPeriod(Department.SALES, 5, time(9), time(17)),), (),
        )
        plan = TurnPlan(
            TurnScope.MIXED,
            (
                ReadCommand.from_mapping(ReadCommandName.SEARCH_VEHICLES, {"make": "BMW"}),
                ReadCommand.from_mapping(ReadCommandName.GET_DEALERSHIP_HOURS, {
                    "dealership_id": "northstar-manchester", "department": "sales"
                }),
            ),
            ResponseStrategy.SEARCH_RESULTS,
        )
        harness, provider = runtime(fake, plan)

        result = await harness.handle(TurnRequest(
            current_input="Find a BMW and Manchester Saturday hours", state=ConversationState(), now=NOW
        ))

        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(result.executed_commands, ("search_vehicles", "get_dealership_hours"))
        self.assertEqual({block.kind for block in result.blocks}, {"vehicle_cards", "opening_hours"})

    async def test_live_page_vehicle_is_used_and_verified_by_dealer_read(self) -> None:
        fake = dealer()
        from webchat.domain.vehicles import VehicleDetails
        fake.get_vehicle.return_value = VehicleDetails(vehicle(), ("Large boot",))
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand(ReadCommandName.GET_VEHICLE_DETAILS),),
            ResponseStrategy.VEHICLE_DETAILS,
        )
        harness, _ = runtime(fake, plan)
        observation = PageContext(
            current_url="http://localhost:4173/?vehicle=veh-003",
            page_vehicle_id="veh-003", observed_at=NOW,
        )

        result = await harness.handle(TurnRequest(
            current_input="Tell me about this one", state=ConversationState(), now=NOW,
            page_observation=observation,
        ))

        fake.get_vehicle.assert_awaited_once_with("veh-003")
        self.assertEqual(result.state.entities.selected_vehicle_id, "veh-003")

    async def test_stale_confirm_button_cannot_turn_failed_action_into_a_retry(self) -> None:
        fake = dealer()
        inert = TurnPlan(TurnScope.IN_DOMAIN, (), ResponseStrategy.ACKNOWLEDGEMENT)
        harness, _ = runtime(fake, inert)
        failed = replace(
            PendingAction.from_mapping(
                action_id="pending-1", action_type=PendingActionType.TEST_DRIVE_BOOKING,
                request_type=PendingRequestType.TEST_DRIVE_BOOKING,
                request_payload={"slot_id": "slot-1", "vehicle_id": "veh-003", "customer": {
                    "first_name": "Jamie", "last_name": "Taylor", "email": "jamie@example.com", "phone": "07700900123"
                }},
                state=PendingActionState.AWAITING_CONFIRMATION,
                idempotency_key="idem-1", created_at=NOW, expires_at=NOW + timedelta(minutes=15),
            ),
            state=PendingActionState.FAILED,
        )

        result = await harness.handle(TurnRequest(
            current_input=None, action_reference=ActionReference("pending-1", "confirm"),
            state=ConversationState(pending_action=failed), now=NOW,
        ))

        fake.book_test_drive.assert_not_called()
        self.assertEqual(result.state.pending_action.state, PendingActionState.FAILED)
        self.assertEqual(result.blocks[0].to_dict()["payload"]["code"], "retry_required")

    async def test_switching_from_pending_workshop_action_to_vehicle_search_supersedes_it(self) -> None:
        fake = dealer()
        fake.search_vehicles.return_value = Page((vehicle(),), 1, 10, 1, 1)
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(ReadCommandName.SEARCH_VEHICLES, {"make": "BMW"}),),
            ResponseStrategy.SEARCH_RESULTS,
        )
        harness, _ = runtime(fake, plan)
        active = PendingAction.from_mapping(
            action_id="workshop-1", action_type=PendingActionType.WORKSHOP_CANCELLATION,
            request_type=PendingRequestType.WORKSHOP_CANCELLATION,
            request_payload={"booking_id": "wsb-1"},
            state=PendingActionState.AWAITING_CONFIRMATION, idempotency_key="idem-workshop",
            created_at=NOW, expires_at=NOW + timedelta(minutes=15),
        )
        state = ConversationState(
            pending_action=active,
            workflow=WorkflowState(WorkflowDomain.WORKSHOP, WorkflowStage.REVIEWING_ACTION),
        )

        result = await harness.handle(TurnRequest(
            current_input="Forget that, show me BMWs", state=state, now=NOW
        ))

        self.assertIsNone(result.state.pending_action)
        self.assertEqual(result.state.workflow.domain, WorkflowDomain.VEHICLES)


if __name__ == "__main__":
    unittest.main()
