import unittest
import math

from webchat.harness import contracts


class TurnPlanContractTests(unittest.TestCase):
    def test_contract_exposes_stable_scope_and_strategy_values(self) -> None:
        self.assertEqual(
            {item.value for item in contracts.TurnScope},
            {"in_domain", "dealership_adjacent", "mixed", "out_of_scope"},
        )
        self.assertIn(
            "domain_redirect",
            {item.value for item in contracts.ResponseStrategy},
        )

    def test_plan_accepts_two_safe_reads(self) -> None:
        plan = contracts.TurnPlan(
            scope=contracts.TurnScope.IN_DOMAIN,
            commands=(
                contracts.ReadCommand(contracts.ReadCommandName.SEARCH_VEHICLES),
                contracts.ReadCommand(contracts.ReadCommandName.GET_DEALERSHIP_HOURS),
            ),
            response_strategy=contracts.ResponseStrategy.SEARCH_RESULTS,
        )

        self.assertEqual(len(plan.commands), 2)

    def test_plan_accepts_one_preparation_and_one_safe_read(self) -> None:
        plan = contracts.TurnPlan(
            scope=contracts.TurnScope.IN_DOMAIN,
            commands=(
                contracts.PreparationCommand(
                    contracts.PreparationCommandName.PREPARE_TEST_DRIVE_BOOKING
                ),
                contracts.ReadCommand(
                    contracts.ReadCommandName.CHECK_VEHICLE_AVAILABILITY
                ),
            ),
            response_strategy=contracts.ResponseStrategy.ACTION_PREPARED,
        )

        self.assertEqual(len(plan.commands), 2)

    def test_plan_rejects_more_than_two_commands(self) -> None:
        with self.assertRaisesRegex(ValueError, "at most two commands"):
            contracts.TurnPlan(
                scope=contracts.TurnScope.IN_DOMAIN,
                commands=(
                    contracts.ReadCommand(contracts.ReadCommandName.SEARCH_VEHICLES),
                    contracts.ReadCommand(
                        contracts.ReadCommandName.GET_VEHICLE_DETAILS
                    ),
                    contracts.ReadCommand(
                        contracts.ReadCommandName.GET_DEALERSHIP_HOURS
                    ),
                ),
                response_strategy=contracts.ResponseStrategy.SEARCH_RESULTS,
            )

    def test_plan_rejects_multiple_preparations(self) -> None:
        with self.assertRaisesRegex(ValueError, "one preparation"):
            contracts.TurnPlan(
                scope=contracts.TurnScope.IN_DOMAIN,
                commands=(
                    contracts.PreparationCommand(
                        contracts.PreparationCommandName.PREPARE_CALLBACK
                    ),
                    contracts.PreparationCommand(
                        contracts.PreparationCommandName.PREPARE_SALES_ENQUIRY
                    ),
                ),
                response_strategy=contracts.ResponseStrategy.ACTION_PREPARED,
            )

    def test_out_of_scope_plan_cannot_call_dealer_commands(self) -> None:
        with self.assertRaisesRegex(ValueError, "out-of-scope"):
            contracts.TurnPlan(
                scope=contracts.TurnScope.OUT_OF_SCOPE,
                commands=(
                    contracts.ReadCommand(contracts.ReadCommandName.SEARCH_VEHICLES),
                ),
                response_strategy=contracts.ResponseStrategy.DOMAIN_REDIRECT,
            )

    def test_command_arguments_are_canonical_and_immutable(self) -> None:
        command = contracts.ReadCommand.from_mapping(
            contracts.ReadCommandName.SEARCH_VEHICLES,
            {"max_price_pence": 3_000_000, "makes": ["BMW", "MINI"]},
        )

        self.assertEqual(
            command.to_dict(),
            {
                "name": "search_vehicles",
                "arguments": {
                    "makes": ["BMW", "MINI"],
                    "max_price_pence": 3_000_000,
                },
            },
        )
        with self.assertRaises(AttributeError):
            command.arguments = ()

    def test_plan_round_trips_through_provider_neutral_data(self) -> None:
        original = contracts.TurnPlan(
            scope=contracts.TurnScope.DEALERSHIP_ADJACENT,
            commands=(),
            response_strategy=contracts.ResponseStrategy.GENERAL_GUIDANCE,
            response_mode=contracts.ResponseMode.GROUNDED_ANSWER,
        )

        restored = contracts.TurnPlan.from_dict(original.to_dict())

        self.assertEqual(restored, original)

    def test_plan_parser_rejects_unknown_fields_and_mutation_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown TurnPlan fields"):
            contracts.TurnPlan.from_dict(
                {
                    "schema_version": 2,
                    "scope": "in_domain",
                    "commands": [],
                    "response_strategy": "missing_information",
                    "execute_now": True,
                }
            )

    def test_direct_construction_rejects_non_command_objects(self) -> None:
        with self.assertRaisesRegex(ValueError, "HarnessCommand"):
            contracts.TurnPlan(
                scope=contracts.TurnScope.IN_DOMAIN,
                commands=("search_vehicles",),
                response_strategy=contracts.ResponseStrategy.SEARCH_RESULTS,
            )

    def test_direct_plan_construction_requires_enum_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "TurnScope"):
            contracts.TurnPlan(
                scope="in_domain",
                commands=(),
                response_strategy=contracts.ResponseStrategy.SEARCH_RESULTS,
            )
        with self.assertRaisesRegex(ValueError, "ResponseStrategy"):
            contracts.TurnPlan(
                scope=contracts.TurnScope.IN_DOMAIN,
                commands=(),
                response_strategy="search_results",
            )

    def test_direct_plan_construction_preserves_immutable_command_tuple(self) -> None:
        with self.assertRaisesRegex(ValueError, "tuple"):
            contracts.TurnPlan(
                scope=contracts.TurnScope.IN_DOMAIN,
                commands=[],
                response_strategy=contracts.ResponseStrategy.SEARCH_RESULTS,
            )

    def test_boolean_schema_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "schema version"):
            contracts.TurnPlan(
                scope=contracts.TurnScope.IN_DOMAIN,
                commands=(),
                response_strategy=contracts.ResponseStrategy.SEARCH_RESULTS,
                schema_version=True,
            )

    def test_direct_plan_construction_rejects_non_string_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "clarification_question"):
            contracts.TurnPlan(
                scope=contracts.TurnScope.IN_DOMAIN,
                commands=(),
                response_strategy=contracts.ResponseStrategy.MISSING_INFORMATION,
                clarification_question=42,
            )
        with self.assertRaisesRegex(ValueError, "response_mode"):
            contracts.TurnPlan(
                scope=contracts.TurnScope.DEALERSHIP_ADJACENT,
                commands=(),
                response_strategy=contracts.ResponseStrategy.GENERAL_GUIDANCE,
                response_mode="grounded_answer",
            )

    def test_text_response_strategies_require_their_text_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "clarification_question"):
            contracts.TurnPlan(
                scope=contracts.TurnScope.IN_DOMAIN,
                commands=(),
                response_strategy=contracts.ResponseStrategy.MISSING_INFORMATION,
            )
        with self.assertRaisesRegex(ValueError, "deterministic mode"):
            contracts.TurnPlan(
                scope=contracts.TurnScope.IN_DOMAIN,
                commands=(),
                response_strategy=contracts.ResponseStrategy.ACKNOWLEDGEMENT,
                response_mode=contracts.ResponseMode.CLARIFICATION,
            )

    def test_action_response_strategy_and_mode_cannot_disagree(self) -> None:
        with self.assertRaisesRegex(ValueError, "action_prepared mode"):
            contracts.TurnPlan(
                scope=contracts.TurnScope.IN_DOMAIN,
                commands=(
                    contracts.PreparationCommand(
                        contracts.PreparationCommandName.PREPARE_CALLBACK
                    ),
                ),
                response_strategy=contracts.ResponseStrategy.ACTION_PREPARED,
                response_mode=contracts.ResponseMode.GROUNDED_ANSWER,
            )

    def test_valid_command_name_does_not_mask_invalid_arguments(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-JSON"):
            contracts.TurnPlan.from_dict(
                {
                    "schema_version": 2,
                    "scope": "in_domain",
                    "commands": [
                        {
                            "name": "search_vehicles",
                            "arguments": {"make": object()},
                        }
                    ],
                    "response_strategy": "search_results",
                    "response_mode": "grounded_answer",
                }
            )

    def test_command_argument_keys_fail_with_a_stable_validation_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "keys must be non-empty strings"):
            contracts.ReadCommand.from_mapping(
                contracts.ReadCommandName.SEARCH_VEHICLES,
                {"make": "BMW", 1: "invalid"},
            )

    def test_direct_command_construction_enforces_enum_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "ReadCommandName"):
            contracts.ReadCommand("search_vehicles")

    def test_command_arguments_reject_non_finite_numbers(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "finite"):
                    contracts.ReadCommand.from_mapping(
                        contracts.ReadCommandName.SEARCH_VEHICLES,
                        {"max_price": value},
                    )
        with self.assertRaisesRegex(ValueError, "unknown command"):
            contracts.TurnPlan.from_dict(
                {
                    "schema_version": 2,
                    "scope": "in_domain",
                    "commands": [
                        {"name": "book_test_drive", "arguments": {"slot_id": "x"}}
                    ],
                    "response_strategy": "action_prepared",
                    "response_mode": "action_prepared",
                }
            )


if __name__ == "__main__":
    unittest.main()
