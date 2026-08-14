from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
import unittest

from webchat.domain.common import Money, Page
from webchat.domain.dealerships import BusinessInformation
from webchat.domain.vehicles import VehicleDetails, VehicleOffer
from webchat.domain.workshop import WorkshopService
from webchat.harness.contracts import (
    ReadCommand,
    ReadCommandName,
    ResponseStrategy,
    TurnPlan,
    TurnScope,
)
from webchat.harness.planning import TurnRequest
from webchat.harness.state import (
    ConversationState,
    WorkflowDomain,
    WorkflowStage,
    WorkflowState,
)
from webchat.harness.workflows.references import resolve_dealership_reference

from tests.webchat.harness.test_mutations import workshop_slot
from tests.webchat.harness.test_runtime import (
    NOW,
    availability,
    dealer,
    location,
    runtime,
    vehicle,
)


class ReadWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def _read(self, name, arguments, strategy, configure):
        fake = dealer()
        configure(fake)
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(name, arguments),),
            strategy,
        )
        harness, _ = runtime(fake, plan)
        if name in {
            ReadCommandName.GET_VEHICLE_DETAILS,
            ReadCommandName.COMPARE_VEHICLES,
            ReadCommandName.CHECK_VEHICLE_AVAILABILITY,
            ReadCommandName.LIST_NEW_CAR_OFFERS,
        }:
            domain = WorkflowDomain.VEHICLES
        elif name in {
            ReadCommandName.LIST_WORKSHOP_SERVICES,
            ReadCommandName.LIST_WORKSHOP_LOCATIONS,
            ReadCommandName.FIND_WORKSHOP_SLOTS,
        }:
            domain = WorkflowDomain.WORKSHOP
        else:
            domain = WorkflowDomain.DEALERSHIP
        return await harness.handle(TurnRequest(
            current_input="Dealership information request",
            state=ConversationState(
                workflow=WorkflowState(domain, WorkflowStage.DISCOVERY)
            ),
            now=NOW,
        ))

    async def test_vehicle_detail_comparison_availability_and_offers(self) -> None:
        offer = VehicleOffer(
            id="offer-1", make="BMW", model="iX1", title="BMW iX1 PCP",
            product_type="PCP", monthly_price=Money(49_900, "GBP"),
            upfront_price=Money(299_900, "GBP"), apr_percent=Decimal("4.9"),
            term_months=48, annual_mileage=8_000,
            expires_on=date(2026, 12, 31), description="Published offer", image_url=None,
        )
        cases = (
            (
                ReadCommandName.GET_VEHICLE_DETAILS, {"vehicle_id": "veh-003"},
                ResponseStrategy.VEHICLE_DETAILS,
                lambda fake: setattr(fake.get_vehicle, "return_value", VehicleDetails(vehicle(), ("Large boot",))),
                {"vehicle_cards", "vehicle_details", "link"},
            ),
            (
                ReadCommandName.COMPARE_VEHICLES, {"vehicle_ids": ["veh-003", "veh-004"]},
                ResponseStrategy.COMPARISON,
                lambda fake: setattr(fake.get_vehicle, "side_effect", [
                    VehicleDetails(vehicle("veh-003"), ()), VehicleDetails(vehicle("veh-004"), ())
                ]),
                {"comparison"},
            ),
            (
                ReadCommandName.CHECK_VEHICLE_AVAILABILITY, {"vehicle_id": "veh-003"},
                ResponseStrategy.AVAILABILITY_RESULT,
                lambda fake: setattr(fake.get_vehicle_availability, "return_value", availability()),
                {"availability", "actions"},
            ),
            (
                ReadCommandName.LIST_NEW_CAR_OFFERS, {}, ResponseStrategy.OFFER_RESULTS,
                lambda fake: setattr(fake.list_offers, "return_value", (offer,)),
                {"offer_cards"},
            ),
        )
        for name, arguments, strategy, configure, expected_blocks in cases:
            with self.subTest(command=name):
                result = await self._read(name, arguments, strategy, configure)
                self.assertEqual(
                    {block.kind for block in result.blocks},
                    expected_blocks | {"text"},
                )
                self.assertTrue(result.evidence)

    async def test_vehicle_search_excludes_one_exact_vehicle_without_excluding_its_model(self) -> None:
        fake = dealer()
        first = replace(vehicle("veh-volvo-1"), make="Volvo", model="XC40")
        second = replace(vehicle("veh-volvo-2"), make="Volvo", model="XC40")
        fake.search_vehicles.return_value = Page((first, second), 1, 10, 2, 1)
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(ReadCommandName.SEARCH_VEHICLES, {
                "fuel_type": "Electric",
                "body_style": "SUV",
                "exclude_vehicle_ids": ["veh-volvo-1"],
            }),),
            ResponseStrategy.SEARCH_RESULTS,
        )
        harness, _ = runtime(fake, plan)

        result = await harness.handle(TurnRequest(
            current_input="Anything except that exact one?",
            state=ConversationState(),
            now=NOW,
        ))

        cards = next(block for block in result.blocks if block.kind == "vehicle_cards")
        self.assertEqual(
            [item["id"] for item in cards.to_dict()["payload"]["vehicles"]],
            ["veh-volvo-2"],
        )
        saved_search = result.state.workflow.gathered_fields_dict()["last_vehicle_search"]
        self.assertEqual(saved_search["exclude_vehicle_ids"], ["veh-volvo-1"])

    async def test_vehicle_search_can_exclude_a_whole_model_without_excluding_make(self) -> None:
        fake = dealer()
        xc40 = replace(vehicle("veh-xc40"), make="Volvo", model="XC40")
        ex30 = replace(vehicle("veh-ex30"), make="Volvo", model="EX30")
        fake.search_vehicles.return_value = Page((xc40, ex30), 1, 10, 2, 1)
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(ReadCommandName.SEARCH_VEHICLES, {
                "fuel_type": "Electric",
                "body_style": "SUV",
                "exclude_models": ["XC40"],
            }),),
            ResponseStrategy.SEARCH_RESULTS,
        )
        harness, _ = runtime(fake, plan)

        result = await harness.handle(TurnRequest(
            current_input="Anything other than the XC40?",
            state=ConversationState(),
            now=NOW,
        ))

        cards = next(block for block in result.blocks if block.kind == "vehicle_cards")
        self.assertEqual(
            [item["id"] for item in cards.to_dict()["payload"]["vehicles"]],
            ["veh-ex30"],
        )

    async def test_workshop_catalogue_locations_and_slots(self) -> None:
        service = WorkshopService(
            "service-mot", "MOT", "Annual inspection", 60, Money(5_499, "GBP")
        )
        cases = (
            (
                ReadCommandName.LIST_WORKSHOP_SERVICES, {}, ResponseStrategy.WORKSHOP_DETAILS,
                lambda fake: setattr(fake.list_service_types, "return_value", (service,)),
                "workshop_services",
            ),
            (
                ReadCommandName.LIST_WORKSHOP_LOCATIONS, {}, ResponseStrategy.WORKSHOP_DETAILS,
                lambda fake: setattr(fake.list_workshop_locations, "return_value", (location(),)),
                "dealerships",
            ),
            (
                ReadCommandName.FIND_WORKSHOP_SLOTS, {"dealership_id": "northstar-manchester", "service_type_id": "service-mot"},
                ResponseStrategy.SLOT_RESULTS,
                lambda fake: setattr(fake.list_workshop_slots, "return_value", (workshop_slot(),)),
                "slot_choices",
            ),
        )
        for name, arguments, strategy, configure, expected_block in cases:
            with self.subTest(command=name):
                result = await self._read(name, arguments, strategy, configure)
                self.assertIn(expected_block, {block.kind for block in result.blocks})
                self.assertTrue(result.evidence)

    async def test_dealership_list_details_and_business_notices(self) -> None:
        information = BusinessInformation(
            "Northstar Motors", "GBP", "United Kingdom", "Finance subject to status",
            18, "Valuations are indicative", "privacy@example.com",
        )
        cases = (
            (
                ReadCommandName.LIST_DEALERSHIPS, {}, ResponseStrategy.DEALERSHIP_DETAILS,
                lambda fake: setattr(fake.list_dealerships, "return_value", (location(),)),
                "dealerships",
            ),
            (
                ReadCommandName.GET_DEALERSHIP_DETAILS, {"dealership_id": "northstar-manchester"},
                ResponseStrategy.DEALERSHIP_DETAILS,
                lambda fake: setattr(fake.get_dealership, "return_value", location()),
                "dealerships",
            ),
            (
                ReadCommandName.GET_BUSINESS_INFORMATION, {}, ResponseStrategy.BUSINESS_INFORMATION,
                lambda fake: setattr(fake.get_business_information, "return_value", information),
                "business_information",
            ),
        )
        for name, arguments, strategy, configure, expected_block in cases:
            with self.subTest(command=name):
                result = await self._read(name, arguments, strategy, configure)
                self.assertIn(expected_block, {block.kind for block in result.blocks})
                self.assertTrue(result.evidence)

    async def test_customer_facing_dealership_reference_resolves_to_stable_id(self) -> None:
        fake = dealer()
        manchester = location()
        liverpool = replace(
            manchester,
            id="northstar-liverpool",
            name="Northstar Liverpool",
            address=replace(manchester.address, town="Liverpool"),
            phone="0151 555 0199",
        )
        fake.list_dealerships.return_value = (manchester, liverpool)
        fake.get_dealership.return_value = liverpool
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (
                ReadCommand.from_mapping(
                    ReadCommandName.GET_DEALERSHIP_DETAILS,
                    {"dealership_query": "Liverpool"},
                ),
            ),
            ResponseStrategy.DEALERSHIP_DETAILS,
        )
        harness, _ = runtime(fake, plan)

        result = await harness.handle(
            TurnRequest(
                current_input="What's the Liverpool parts phone number?",
                state=ConversationState(),
                now=NOW,
            )
        )

        fake.get_dealership.assert_awaited_once_with("northstar-liverpool")
        self.assertEqual(
            result.state.entities.selected_dealer_id,
            "northstar-liverpool",
        )
        self.assertIn(
            "Liverpool",
            {getattr(item, "value", None) for item in result.evidence},
        )

    async def test_reference_resolver_does_not_guess_unknown_location(self) -> None:
        fake = dealer()
        fake.list_dealerships.return_value = (location(),)

        resolution = await resolve_dealership_reference(
            fake,
            dealership_id=None,
            dealership_query="Springfield",
            selected_id=None,
        )

        self.assertIsNone(resolution.location)
        self.assertEqual(resolution.candidates, (location(),))


if __name__ == "__main__":
    unittest.main()
