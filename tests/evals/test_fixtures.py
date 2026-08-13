from datetime import UTC, datetime
import unittest

from evals.fixtures import FixtureRegistry
from evals.schema import load_corpus
from webchat.domain import (
    DealerAdapter,
    DealerError,
    DealerErrorKind,
    VehicleAvailabilityStatus,
)
from webchat.harness.actions import PendingActionState


NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


class FixtureRegistryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.registry = FixtureRegistry(now=NOW)
        self.corpus = load_corpus("evals/corpus.json")

    def test_registry_covers_every_fixture_named_by_the_corpus(self) -> None:
        states = {scenario.initial_state_fixture for scenario in self.corpus.scenarios}
        dealers = {scenario.dealer_fixture for scenario in self.corpus.scenarios}

        self.assertEqual(states, set(self.registry.state_names))
        self.assertEqual(dealers, set(self.registry.dealer_names))

    def test_representative_states_retain_selection_action_and_grant(self) -> None:
        selected = self.registry.build_state("selected_veh_019")
        pending = self.registry.build_state("confirmed_test_drive_pending")
        verified = self.registry.build_state("verified_booking_001")

        self.assertEqual(selected.entities.selected_vehicle_id, "veh-019")
        self.assertEqual(
            pending.pending_action.state,
            PendingActionState.AWAITING_CONFIRMATION,
        )
        self.assertTrue(verified.has_active_grant("wsb-seeded-001", now=NOW))

    async def test_dealers_are_protocol_shaped_and_isolated(self) -> None:
        first = self.registry.build_dealer("seeded")
        second = self.registry.build_dealer("seeded")

        self.assertIsInstance(first, DealerAdapter)
        await first.list_dealerships()
        self.assertEqual(len(first.snapshot().calls), 1)
        self.assertEqual(second.snapshot().calls, ())

    async def test_seeded_dealer_exposes_known_vehicle_edges(self) -> None:
        dealer = self.registry.build_dealer("seeded")

        self.assertEqual(
            (await dealer.get_vehicle_availability("veh-007")).status,
            VehicleAvailabilityStatus.RESERVED,
        )
        self.assertEqual(
            (await dealer.get_vehicle_availability("veh-013")).status,
            VehicleAvailabilityStatus.SOLD,
        )
        self.assertIsNone((await dealer.get_vehicle("veh-019")).vehicle.price)

    async def test_unknown_dealership_uses_stable_not_found_failure(self) -> None:
        dealer = self.registry.build_dealer("seeded")

        with self.assertRaises(DealerError) as raised:
            await dealer.get_dealership("Liverpool")

        self.assertEqual(raised.exception.kind, DealerErrorKind.NOT_FOUND)
        self.assertEqual(raised.exception.resource, "Liverpool")

    async def test_unknown_vehicle_uses_stable_not_found_failure(self) -> None:
        dealer = self.registry.build_dealer("seeded")

        with self.assertRaises(DealerError) as raised:
            await dealer.get_vehicle("unknown-vehicle")

        self.assertEqual(raised.exception.kind, DealerErrorKind.NOT_FOUND)
        self.assertEqual(raised.exception.resource, "unknown-vehicle")


if __name__ == "__main__":
    unittest.main()
