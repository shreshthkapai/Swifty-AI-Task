import unittest

from webchat.domain.errors import DealerErrorKind, DealerFailure
from webchat.harness.render import DeclarativeRenderer
from webchat.harness.state import ConversationState
from webchat.harness.workflows.common import dealer_recovery


class DealerRecoveryTests(unittest.TestCase):
    def test_every_normalized_dealer_failure_has_a_safe_structured_response(self) -> None:
        renderer = DeclarativeRenderer(id_factory=lambda: "action-1")

        for kind in DealerErrorKind:
            with self.subTest(kind=kind):
                outcome = dealer_recovery(
                    ConversationState(),
                    DealerFailure(
                        kind,
                        retryable=kind is DealerErrorKind.TEMPORARY_FAILURE,
                    ),
                    renderer=renderer,
                )

                self.assertEqual(outcome.blocks[0].kind, "notice")
                self.assertEqual(
                    outcome.blocks[0].to_dict()["payload"]["code"],
                    kind.value,
                )

    def test_recovery_is_selected_by_error_kind_not_exception_wording(self) -> None:
        outcome = dealer_recovery(
            ConversationState(),
            DealerFailure(DealerErrorKind.SLOT_UNAVAILABLE),
            renderer=DeclarativeRenderer(id_factory=lambda: "action-1"),
        )

        self.assertEqual(outcome.blocks[0].to_dict()["payload"]["code"], "slot_unavailable")
        self.assertIn("choose another", outcome.blocks[0].to_dict()["payload"]["text"].lower())


if __name__ == "__main__":
    unittest.main()
