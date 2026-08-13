from datetime import UTC, datetime
import unittest

from webchat.domain.common import Money
from webchat.domain.vehicles import Vehicle, VehicleAvailabilityStatus
from webchat.harness.render import DeclarativeRenderer


NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def vehicle(vehicle_id: str, *, price: Money | None) -> Vehicle:
    return Vehicle(
        id=vehicle_id,
        dealership_id="northstar-manchester",
        dealership_name="Northstar Manchester",
        dealership_town="Manchester",
        make="BMW",
        model="X3",
        variant="xDrive20d M Sport",
        year=2024,
        price=price,
        monthly_price=None,
        mileage=8_000,
        fuel_type="Diesel",
        transmission="Automatic",
        colour="Blue",
        body_style="SUV",
        availability=VehicleAvailabilityStatus.AVAILABLE,
        registration="MA24 XYZ",
        description="Dealer-authored description.",
        images=("/assets/vehicles/x3.jpg",),
        updated_at=NOW,
    )


class DeclarativeRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        identifiers = iter(("action-1", "action-2", "action-3"))
        self.renderer = DeclarativeRenderer(id_factory=lambda: next(identifiers))

    def test_vehicle_cards_preserve_unknown_price_and_issue_stable_references(self) -> None:
        block = self.renderer.vehicle_cards((vehicle("veh-019", price=None),))

        self.assertEqual(block.kind, "vehicle_cards")
        payload = block.to_dict()["payload"]
        self.assertEqual(payload["schema_version"], 1)
        self.assertIsNone(payload["vehicles"][0]["price"]["amount_minor"])
        self.assertEqual(payload["vehicles"][0]["price"]["display"], "Price on request")
        self.assertEqual(block.entity_references[0].entity_id, "veh-019")
        self.assertEqual(block.action_references[0].action_type, "select_vehicle")

    def test_comparison_uses_only_returned_vehicle_fields(self) -> None:
        priced = vehicle("veh-003", price=Money(4_299_500, "GBP"))
        unknown = vehicle("veh-019", price=None)

        block = self.renderer.comparison((priced, unknown))

        rows = block.to_dict()["payload"]["rows"]
        price_row = next(row for row in rows if row["label"] == "Price")
        self.assertEqual(price_row["values"], ["£42,995", "Price on request"])
        self.assertNotIn("score", block.to_dict()["payload"])

    def test_notice_and_actions_are_declarative_not_executable_callbacks(self) -> None:
        notice = self.renderer.notice("Finance is subject to status.", code="finance_notice")
        actions = self.renderer.actions(
            (("register_interest", "Register interest", "veh-007"),)
        )

        self.assertEqual(notice.to_dict()["payload"]["code"], "finance_notice")
        self.assertEqual(actions.kind, "actions")
        self.assertEqual(actions.action_references[0].action_type, "register_interest")
        self.assertEqual(actions.to_dict()["payload"]["actions"][0]["entity_id"], "veh-007")


if __name__ == "__main__":
    unittest.main()
