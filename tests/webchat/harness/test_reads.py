from datetime import date, timedelta
from decimal import Decimal
import unittest

from webchat.domain.common import Money
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
from webchat.harness.state import ConversationState

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
        return await harness.handle(TurnRequest(
            current_input="Dealership information request",
            state=ConversationState(),
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
                self.assertEqual({block.kind for block in result.blocks}, expected_blocks)

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


if __name__ == "__main__":
    unittest.main()
