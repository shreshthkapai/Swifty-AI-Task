import unittest

from server.config import AppConfig


def valid_environment() -> dict[str, str]:
    return {
        "CHAT_ENVIRONMENT": "local",
        "CHAT_PROVIDER": "openai",
        "CHAT_MODEL": "response-model",
        "CHAT_PLANNER_MODEL": "fast-planner-model",
        "OPENAI_API_KEY": "provider-secret",
        "NORTHSTAR_BASE_URL": "http://dealership-platform:4010",
        "NORTHSTAR_PUBLIC_BASE_URL": "http://localhost:4010",
        "NORTHSTAR_API_KEY": "dealer-secret",
        "CHAT_DATABASE_PATH": "/data/chat.sqlite3",
        "CHAT_ALLOWED_ORIGIN": "http://localhost:4173",
        "CHAT_RETENTION_DAYS": "7",
        "CHAT_PROVIDER_TIMEOUT_SECONDS": "20",
        "CHAT_DATABASE_TIMEOUT_SECONDS": "5",
        "CHAT_MAX_BODY_BYTES": "16384",
        "CHAT_MAX_MESSAGE_CHARS": "4000",
    }


class AppConfigTests(unittest.TestCase):
    def test_loads_explicit_server_configuration(self) -> None:
        config = AppConfig.from_env(valid_environment())

        self.assertEqual(config.provider, "openai")
        self.assertEqual(config.response_model, "response-model")
        self.assertEqual(config.planner_model, "fast-planner-model")
        self.assertEqual(config.northstar.base_url, "http://dealership-platform:4010")
        self.assertEqual(config.northstar.public_base_url, "http://localhost:4010")
        self.assertEqual(config.allowed_origin, "http://localhost:4173")
        self.assertEqual(config.retention_seconds, 7 * 24 * 60 * 60)
        self.assertFalse(config.cookie_secure)
        self.assertNotIn("provider-secret", repr(config))
        self.assertNotIn("dealer-secret", repr(config))

    def test_planner_model_defaults_to_response_model(self) -> None:
        values = valid_environment()
        del values["CHAT_PLANNER_MODEL"]

        config = AppConfig.from_env(values)

        self.assertEqual(config.response_model, "response-model")
        self.assertEqual(config.planner_model, "response-model")

    def test_production_uses_secure_cookie(self) -> None:
        values = valid_environment()
        values["CHAT_ENVIRONMENT"] = "production"
        values["CHAT_ALLOWED_ORIGIN"] = "https://www.northstar.example"

        self.assertTrue(AppConfig.from_env(values).cookie_secure)

    def test_provider_timeout_defaults_to_thirty_seconds(self) -> None:
        values = valid_environment()
        del values["CHAT_PROVIDER_TIMEOUT_SECONDS"]

        self.assertEqual(AppConfig.from_env(values).provider_timeout_seconds, 30.0)

    def test_rejects_missing_secrets_unknown_provider_and_non_origin_cors(self) -> None:
        cases = (
            ("OPENAI_API_KEY", None, "OPENAI_API_KEY"),
            ("NORTHSTAR_API_KEY", "", "NORTHSTAR_API_KEY"),
            ("CHAT_PROVIDER", "unknown", "CHAT_PROVIDER"),
            ("CHAT_ALLOWED_ORIGIN", "http://localhost:4173/path", "origin"),
            ("CHAT_RETENTION_DAYS", "0", "CHAT_RETENTION_DAYS"),
            ("CHAT_MAX_BODY_BYTES", "nan", "CHAT_MAX_BODY_BYTES"),
        )
        for key, value, message in cases:
            with self.subTest(key=key, value=value):
                values = valid_environment()
                if value is None:
                    del values[key]
                else:
                    values[key] = value
                with self.assertRaisesRegex(ValueError, message):
                    AppConfig.from_env(values)

    def test_constructor_rejects_invalid_operational_bounds(self) -> None:
        baseline = AppConfig.from_env(valid_environment())
        fields = (
            ("response_model", 123),
            ("planner_model", ""),
            ("openai_api_key", None),
            ("database_path", "chat.sqlite3"),
            ("retention_days", 0),
            ("provider_timeout_seconds", float("nan")),
            ("database_timeout_seconds", -1),
            ("max_body_bytes", 0),
            ("max_message_chars", True),
        )
        for name, value in fields:
            with self.subTest(name=name, value=value):
                kwargs = {
                    field_name: getattr(baseline, field_name)
                    for field_name in baseline.__dataclass_fields__
                }
                kwargs[name] = value
                with self.assertRaisesRegex(ValueError, name):
                    AppConfig(**kwargs)
