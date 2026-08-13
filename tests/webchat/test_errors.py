from dataclasses import FrozenInstanceError
import unittest

from webchat.domain.errors import DealerError, DealerErrorKind, DealerFailure, FieldViolation


class DealerErrorKindTestCase(unittest.TestCase):
    def test_error_kinds_preserve_every_recovery_branch(self):
        self.assertEqual(
            {kind.value for kind in DealerErrorKind},
            {
                "validation", "not_found", "verification_failed", "slot_unavailable",
                "vehicle_reserved", "vehicle_unavailable", "vehicle_not_reserved",
                "booking_cancelled", "idempotency_conflict", "authentication_failed",
                "invalid_request", "temporary_failure", "invalid_response",
            },
        )


class DealerFailureTestCase(unittest.TestCase):
    def test_failure_is_immutable_and_requires_tuple_violations(self):
        violation = FieldViolation("phone", "invalid_format", "Enter a valid phone number")
        failure = DealerFailure(DealerErrorKind.VALIDATION, field_violations=(violation,))
        with self.assertRaises(FrozenInstanceError):
            failure.retryable = True
        with self.assertRaises(ValueError):
            DealerFailure(DealerErrorKind.VALIDATION, field_violations=[violation])  # type: ignore[arg-type]

    def test_retryability_is_consistent_with_failure_kind(self):
        with self.assertRaises(ValueError):
            DealerFailure(DealerErrorKind.TEMPORARY_FAILURE, retryable=False)
        with self.assertRaises(ValueError):
            DealerFailure(DealerErrorKind.SLOT_UNAVAILABLE, retryable=True)
        self.assertTrue(DealerFailure(DealerErrorKind.TEMPORARY_FAILURE, retryable=True).retryable)


class DealerExceptionTestCase(unittest.TestCase):
    def test_exception_exposes_only_structured_safe_context(self):
        failure = DealerFailure(
            kind=DealerErrorKind.SLOT_UNAVAILABLE,
            resource="workshop_slot",
        )
        error = DealerError(failure)
        self.assertIs(error.kind, DealerErrorKind.SLOT_UNAVAILABLE)
        self.assertFalse(error.retryable)
        self.assertEqual(error.resource, "workshop_slot")
        self.assertEqual(str(error), "slot_unavailable: workshop_slot")
        self.assertFalse(hasattr(error, "raw_response"))


if __name__ == "__main__":
    unittest.main()
