import unittest

from webchat.domain.common import Money
from webchat.domain.sales import TestDriveSlot
from webchat.domain.vehicles import VehicleDetails
from webchat.domain.workshop import WorkshopService
from webchat.harness.contracts import (
    ReadCommandName,
    ResponseMode,
    ResponseStrategy,
    TurnPlan,
    TurnScope,
)
from webchat.harness.evidence import EvidenceItem
from webchat.harness.policy import PolicyEngine
from webchat.harness.render import DeclarativeRenderer
from webchat.harness.state import ConversationState
from webchat.harness.workflows.dealerships import execute_dealership_read
from webchat.harness.workflows.sales import execute_sales_read
from webchat.harness.workflows.vehicles import execute_vehicle_read
from webchat.harness.workflows.workshop import execute_workshop_read

from tests.webchat.harness.test_runtime import (
    NOW,
    availability,
    dealer,
    location,
    vehicle,
)


def _ids():
    values = (f"id-{index}" for index in range(1, 20))
    return lambda: next(values)


class TurnResponseOwnershipContractTests(unittest.TestCase):
    def test_turn_plan_carries_structural_response_mode_without_model_prose(self) -> None:
        original = TurnPlan(
            scope=TurnScope.DEALERSHIP_ADJACENT,
            commands=(),
            response_strategy=ResponseStrategy.GENERAL_GUIDANCE,
            response_mode=ResponseMode.GROUNDED_ANSWER,
        )

        serialized = original.to_dict()

        self.assertEqual(serialized["response_mode"], "grounded_answer")
        self.assertEqual(
            set(serialized),
            {
                "schema_version",
                "scope",
                "commands",
                "response_strategy",
                "response_mode",
            },
        )
        self.assertEqual(TurnPlan.from_dict(serialized), original)


class WorkflowEvidenceOwnershipTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.renderer = DeclarativeRenderer(id_factory=_ids())
        self.policy = PolicyEngine()

    async def test_vehicle_details_return_domain_evidence_and_supporting_blocks_only(self) -> None:
        fake = dealer()
        fake.get_vehicle.return_value = VehicleDetails(vehicle(), ("Large boot",))

        outcome = await execute_vehicle_read(
            fake,
            ReadCommandName.GET_VEHICLE_DETAILS,
            {"vehicle_id": "veh-003"},
            state=ConversationState(),
            now=NOW,
            renderer=self.renderer,
            id_factory=_ids(),
            policy=self.policy,
        )

        self.assertNotIn("text", {block.kind for block in outcome.blocks})
        self.assertEqual(
            {block.kind for block in outcome.blocks},
            {"vehicle_cards", "vehicle_details", "link"},
        )
        facts = {
            (item.entity_id, item.field_name): item.value
            for item in outcome.evidence
            if isinstance(item, EvidenceItem)
        }
        self.assertEqual(facts[("veh-003", "model")], "X3")
        self.assertEqual(facts[("veh-003", "highlights[0]")], "Large boot")

    async def test_sales_slots_return_live_slot_evidence_and_choices_only(self) -> None:
        fake = dealer()
        fake.get_vehicle_availability.return_value = availability()
        slot = TestDriveSlot(
            id="slot-1",
            dealership_id="northstar-manchester",
            vehicle_id="veh-003",
            starts_at=NOW,
            dealership_name="Northstar Manchester",
            vehicle_label="BMW X3",
        )
        fake.list_test_drive_slots.return_value = (slot,)

        outcome = await execute_sales_read(
            fake,
            ReadCommandName.FIND_TEST_DRIVE_SLOTS,
            {"vehicle_id": "veh-003"},
            state=ConversationState(),
            now=NOW,
            renderer=self.renderer,
            id_factory=_ids(),
            policy=self.policy,
        )

        self.assertEqual(tuple(block.kind for block in outcome.blocks), ("slot_choices",))
        facts = {item.field_name: item.value for item in outcome.evidence}
        self.assertEqual(facts["starts_at"], NOW.isoformat())
        self.assertEqual(facts["vehicle_label"], "BMW X3")

    async def test_empty_sales_slot_result_is_explicit_evidence_not_inferred_from_notice(self) -> None:
        fake = dealer()
        fake.get_vehicle_availability.return_value = availability()
        fake.list_test_drive_slots.return_value = ()

        outcome = await execute_sales_read(
            fake,
            ReadCommandName.FIND_TEST_DRIVE_SLOTS,
            {"vehicle_id": "veh-003"},
            state=ConversationState(),
            now=NOW,
            renderer=self.renderer,
            id_factory=_ids(),
            policy=self.policy,
        )

        facts = {
            (item.entity_id, item.field_name): item.value
            for item in outcome.evidence
            if isinstance(item, EvidenceItem)
        }
        self.assertEqual(facts.get(("test_drive_slot_search", "result_count")), 0)

    async def test_workshop_services_return_stable_service_evidence_and_records_only(self) -> None:
        fake = dealer()
        fake.list_service_types.return_value = (
            WorkshopService(
                "service-mot",
                "MOT",
                "Annual inspection",
                60,
                Money(5_499, "GBP"),
            ),
        )

        outcome = await execute_workshop_read(
            fake,
            ReadCommandName.LIST_WORKSHOP_SERVICES,
            {},
            state=ConversationState(),
            now=NOW,
            renderer=self.renderer,
            id_factory=_ids(),
            policy=self.policy,
        )

        self.assertEqual(tuple(block.kind for block in outcome.blocks), ("workshop_services",))
        facts = {item.field_name: item.value for item in outcome.evidence}
        self.assertEqual(facts["name"], "MOT")
        self.assertEqual(facts["duration_minutes"], 60)
        self.assertEqual(facts["price_from.amount_minor"], 5_499)

    async def test_dealership_details_return_stable_location_evidence_and_records_only(self) -> None:
        fake = dealer()
        fake.get_dealership.return_value = location()

        outcome = await execute_dealership_read(
            fake,
            ReadCommandName.GET_DEALERSHIP_DETAILS,
            {"dealership_id": "northstar-manchester"},
            state=ConversationState(),
            now=NOW,
            renderer=self.renderer,
            id_factory=_ids(),
            policy=self.policy,
        )

        self.assertEqual(tuple(block.kind for block in outcome.blocks), ("dealerships",))
        facts = {item.field_name: item.value for item in outcome.evidence}
        self.assertEqual(facts["name"], "Northstar Manchester")
        self.assertEqual(facts["phone"], "0161 555 0101")


if __name__ == "__main__":
    unittest.main()
