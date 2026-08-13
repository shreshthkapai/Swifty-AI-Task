import unittest

from webchat.adapters.northstar.errors import map_platform_error
from webchat.domain import DealerErrorKind


def error_payload(
    code: str,
    *,
    retryable: bool = False,
    field_errors: dict[str, str] | None = None,
) -> dict[str, object]:
    return {
        "error": {
            "code": code,
            "message": "Northstar wording is not a harness contract.",
            "fieldErrors": {} if field_errors is None else field_errors,
            "retryable": retryable,
        }
    }


class NorthstarErrorMappingTestCase(unittest.TestCase):
    def test_documented_business_codes_map_to_stable_kinds(self) -> None:
        cases = (
            (404, "NOT_FOUND", DealerErrorKind.NOT_FOUND),
            (404, "BOOKING_NOT_FOUND", DealerErrorKind.VERIFICATION_FAILED),
            (409, "SLOT_UNAVAILABLE", DealerErrorKind.SLOT_UNAVAILABLE),
            (409, "VEHICLE_RESERVED", DealerErrorKind.VEHICLE_RESERVED),
            (409, "VEHICLE_UNAVAILABLE", DealerErrorKind.VEHICLE_UNAVAILABLE),
            (409, "VEHICLE_NOT_RESERVED", DealerErrorKind.VEHICLE_NOT_RESERVED),
            (409, "BOOKING_CANCELLED", DealerErrorKind.BOOKING_CANCELLED),
            (409, "IDEMPOTENCY_CONFLICT", DealerErrorKind.IDEMPOTENCY_CONFLICT),
            (401, "UNAUTHORISED", DealerErrorKind.AUTHENTICATION_FAILED),
        )

        for status, code, expected in cases:
            with self.subTest(code=code):
                error = map_platform_error(
                    status,
                    error_payload(code),
                    resource="workshop_booking",
                )
                self.assertEqual(error.kind, expected)
                self.assertFalse(error.retryable)
                self.assertEqual(error.resource, "workshop_booking")
                self.assertNotIn("Northstar wording", str(error))

    def test_platform_request_defects_are_not_customer_validation(self) -> None:
        for status, code in (
            (400, "IDEMPOTENCY_KEY_REQUIRED"),
            (400, "INVALID_IDEMPOTENCY_KEY"),
            (400, "INVALID_JSON"),
            (413, "REQUEST_TOO_LARGE"),
        ):
            with self.subTest(code=code):
                error = map_platform_error(status, error_payload(code))
                self.assertEqual(error.kind, DealerErrorKind.INVALID_REQUEST)
                self.assertFalse(error.retryable)

    def test_validation_fields_are_translated_to_domain_collection_paths(self) -> None:
        error = map_platform_error(
            422,
            error_payload(
                "VALIDATION_ERROR",
                field_errors={
                    "firstName": "Enter at least two characters.",
                    "phone": "Enter a UK phone number.",
                    "slotId": "Choose a slot.",
                },
            ),
            field_map={
                "firstName": "customer.first_name",
                "phone": "customer.phone",
                "slotId": "slot_id",
            },
        )

        self.assertEqual(error.kind, DealerErrorKind.VALIDATION)
        self.assertEqual(
            tuple((item.field, item.code, item.message) for item in error.field_violations),
            (
                ("customer.first_name", "invalid", "Enter at least two characters."),
                ("customer.phone", "invalid", "Enter a UK phone number."),
                ("slot_id", "invalid", "Choose a slot."),
            ),
        )

    def test_internal_error_is_the_only_retryable_platform_error(self) -> None:
        error = map_platform_error(
            500,
            error_payload("INTERNAL_ERROR", retryable=True),
            resource="vehicle_search",
        )

        self.assertEqual(error.kind, DealerErrorKind.TEMPORARY_FAILURE)
        self.assertTrue(error.retryable)
        self.assertEqual(error.resource, "vehicle_search")

    def test_unknown_code_fails_closed_as_invalid_response(self) -> None:
        error = map_platform_error(418, error_payload("NEW_VENDOR_CODE"))

        self.assertEqual(error.kind, DealerErrorKind.INVALID_RESPONSE)
        self.assertFalse(error.retryable)

    def test_malformed_error_envelopes_fail_closed(self) -> None:
        malformed_payloads: tuple[object, ...] = (
            {},
            {"error": []},
            {"error": {"code": "NOT_FOUND"}},
            {
                "error": {
                    "code": "NOT_FOUND",
                    "message": 7,
                    "fieldErrors": {},
                    "retryable": False,
                }
            },
            {
                "error": {
                    "code": "NOT_FOUND",
                    "message": "missing",
                    "fieldErrors": [],
                    "retryable": False,
                }
            },
            {
                "error": {
                    "code": "NOT_FOUND",
                    "message": "missing",
                    "fieldErrors": {"phone": 4},
                    "retryable": False,
                }
            },
            {
                "error": {
                    "code": "NOT_FOUND",
                    "message": "missing",
                    "fieldErrors": {},
                    "retryable": "false",
                }
            },
        )

        for payload in malformed_payloads:
            with self.subTest(payload=payload):
                error = map_platform_error(404, payload)
                self.assertEqual(error.kind, DealerErrorKind.INVALID_RESPONSE)

    def test_retryable_flag_contradiction_is_an_invalid_response(self) -> None:
        business_marked_retryable = map_platform_error(
            409,
            error_payload("SLOT_UNAVAILABLE", retryable=True),
        )
        internal_marked_non_retryable = map_platform_error(
            500,
            error_payload("INTERNAL_ERROR", retryable=False),
        )

        self.assertEqual(business_marked_retryable.kind, DealerErrorKind.INVALID_RESPONSE)
        self.assertEqual(internal_marked_non_retryable.kind, DealerErrorKind.INVALID_RESPONSE)


if __name__ == "__main__":
    unittest.main()
