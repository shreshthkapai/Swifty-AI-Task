import unittest
from datetime import UTC, datetime, timedelta

from webchat.domain.errors import DealerErrorKind, DealerFailure
from webchat.harness.actions import (
    PendingAction,
    PendingRequestType,
    PendingActionState,
    PendingActionType,
)
from webchat.harness.contracts import FrozenObject


class PendingActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

    def make_action(self, **overrides: object) -> PendingAction:
        values: dict[str, object] = {
            "action_id": "action-1",
            "action_type": PendingActionType.TEST_DRIVE_BOOKING,
            "request_type": PendingRequestType.TEST_DRIVE_BOOKING,
            "request_payload": {
                "slot_id": "td-slot-1",
                "customer": {"first_name": "Alex", "phone": "07123456789"},
            },
            "state": PendingActionState.AWAITING_CONFIRMATION,
            "idempotency_key": "idem-1",
            "created_at": self.now,
            "expires_at": self.now + timedelta(minutes=10),
        }
        values.update(overrides)
        return PendingAction.from_mapping(**values)

    def test_request_payload_is_canonical_immutable_data(self) -> None:
        action = self.make_action()

        self.assertEqual(
            action.request_payload_dict(),
            {
                "customer": {"first_name": "Alex", "phone": "07123456789"},
                "slot_id": "td-slot-1",
            },
        )
        with self.assertRaises(AttributeError):
            action.request_payload = ()

    def test_action_round_trip_preserves_execution_identity_and_failure(self) -> None:
        action = self.make_action(
            state=PendingActionState.FAILED,
            attempt_count=2,
            last_failure=DealerFailure(
                kind=DealerErrorKind.SLOT_UNAVAILABLE,
                resource="td-slot-1",
            ),
        )

        restored = PendingAction.from_dict(action.to_dict())

        self.assertEqual(restored, action)
        self.assertEqual(restored.idempotency_key, "idem-1")
        self.assertEqual(restored.last_failure.kind, DealerErrorKind.SLOT_UNAVAILABLE)

    def test_confirmed_action_state_survives_persistence_round_trip(self) -> None:
        action = self.make_action(state=PendingActionState.CONFIRMED)

        restored = PendingAction.from_dict(action.to_dict())

        self.assertIs(restored.state, PendingActionState.CONFIRMED)

    def test_action_rejects_naive_or_reversed_expiry(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self.make_action(created_at=datetime(2026, 8, 13, 12, 0))
        with self.assertRaisesRegex(ValueError, "after created_at"):
            self.make_action(expires_at=self.now)

    def test_action_expiry_uses_explicit_time(self) -> None:
        action = self.make_action()

        self.assertFalse(action.is_expired(self.now + timedelta(minutes=9)))
        self.assertTrue(action.is_expired(self.now + timedelta(minutes=10)))

    def test_action_rejects_noncanonical_or_invalid_execution_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-negative"):
            self.make_action(attempt_count=-1)
        with self.assertRaisesRegex(ValueError, "JSON"):
            self.make_action(request_payload={"slot_id": object()})
        with self.assertRaisesRegex(ValueError, "PendingActionState"):
            self.make_action(state="awaiting_confirmation")

    def test_action_rejects_request_type_that_does_not_match_action_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.make_action(request_type=PendingRequestType.WORKSHOP_BOOKING)

    def test_direct_action_construction_rejects_malformed_frozen_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "canonical frozen JSON"):
            PendingAction(
                action_id="action-1",
                action_type=PendingActionType.TEST_DRIVE_BOOKING,
                request_type=PendingRequestType.TEST_DRIVE_BOOKING,
                request_payload=FrozenObject((("z", 1), ("a", 2))),
                state=PendingActionState.AWAITING_CONFIRMATION,
                idempotency_key="idem-1",
                created_at=self.now,
                expires_at=self.now + timedelta(minutes=10),
            )


if __name__ == "__main__":
    unittest.main()
