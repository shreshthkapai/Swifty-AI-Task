import unittest

from webchat.adapters.northstar.config import NorthstarConfig


class NorthstarConfigTestCase(unittest.TestCase):
    def test_defaults_capture_northstar_locale_and_bounded_recovery(self) -> None:
        config = NorthstarConfig(
            base_url="http://localhost:4010/",
            api_key="northstar-secret",
        )

        self.assertEqual(config.base_url, "http://localhost:4010")
        self.assertEqual(config.currency, "GBP")
        self.assertEqual(config.timezone, "Europe/London")
        self.assertEqual(config.country, "United Kingdom")
        self.assertEqual(config.location_cache_ttl_seconds, 300.0)
        self.assertEqual(config.retry_delays_seconds, (0.1, 0.25))

    def test_from_env_requires_server_side_api_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "NORTHSTAR_API_KEY"):
            NorthstarConfig.from_env({"NORTHSTAR_BASE_URL": "http://platform:4010"})

    def test_from_env_uses_configured_platform_url_and_key(self) -> None:
        config = NorthstarConfig.from_env(
            {
                "NORTHSTAR_BASE_URL": "http://platform:4010/",
                "NORTHSTAR_API_KEY": "server-key",
            }
        )

        self.assertEqual(config.base_url, "http://platform:4010")
        self.assertEqual(config.api_key, "server-key")

    def test_invalid_numeric_configuration_is_rejected(self) -> None:
        invalid_values = (
            {"connect_timeout_seconds": 0},
            {"read_timeout_seconds": -1},
            {"write_timeout_seconds": 0},
            {"pool_timeout_seconds": 0},
            {"location_cache_ttl_seconds": 0},
            {"retry_delays_seconds": (0.1, -0.1)},
        )

        for overrides in invalid_values:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                NorthstarConfig(
                    base_url="http://localhost:4010",
                    api_key="server-key",
                    **overrides,
                )

    def test_http_base_url_and_non_empty_key_are_required(self) -> None:
        for base_url, api_key in (
            ("localhost:4010", "server-key"),
            ("ftp://localhost", "server-key"),
            ("http://localhost:4010/api", "server-key"),
            ("http://localhost:4010", " "),
        ):
            with self.subTest(base_url=base_url, api_key=api_key), self.assertRaises(ValueError):
                NorthstarConfig(base_url=base_url, api_key=api_key)

    def test_api_key_is_redacted_from_configuration_representation(self) -> None:
        config = NorthstarConfig("http://localhost:4010", "do-not-log-this-key")

        self.assertNotIn("do-not-log-this-key", repr(config))


if __name__ == "__main__":
    unittest.main()
