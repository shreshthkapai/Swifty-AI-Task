import math
import unittest

from webchat.harness import contracts


class SemanticCommandContractTests(unittest.TestCase):
    def test_read_command_is_canonical_immutable_provider_neutral_data(self) -> None:
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

    def test_command_parser_accepts_reads_and_preparations_but_not_mutations(self) -> None:
        read = contracts.command_from_dict(
            {"name": "get_vehicle_details", "arguments": {"vehicle_id": "veh-1"}}
        )
        preparation = contracts.command_from_dict(
            {"name": "prepare_callback", "arguments": {"department": "sales"}}
        )

        self.assertIsInstance(read, contracts.ReadCommand)
        self.assertIsInstance(preparation, contracts.PreparationCommand)
        with self.assertRaisesRegex(ValueError, "unknown command"):
            contracts.command_from_dict(
                {"name": "book_test_drive", "arguments": {"slot_id": "slot-1"}}
            )

    def test_command_arguments_reject_invalid_json(self) -> None:
        with self.assertRaisesRegex(ValueError, "keys must be non-empty strings"):
            contracts.ReadCommand.from_mapping(
                contracts.ReadCommandName.SEARCH_VEHICLES,
                {"make": "BMW", 1: "invalid"},
            )
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "finite"):
                    contracts.ReadCommand.from_mapping(
                        contracts.ReadCommandName.SEARCH_VEHICLES,
                        {"max_price": value},
                    )

    def test_direct_construction_requires_typed_command_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "ReadCommandName"):
            contracts.ReadCommand("search_vehicles")


if __name__ == "__main__":
    unittest.main()
