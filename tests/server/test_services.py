from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from server.services import build_services
from webchat.harness.policy import PolicyCode, PolicyError
from webchat.harness.state import ConversationState, CustomerState

from .support import app_config


class ServiceCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_uses_one_conversational_provider_and_model(self) -> None:
        with TemporaryDirectory() as directory:
            services = build_services(
                app_config(Path(directory) / "chat.sqlite3", model="conversation-model")
            )
            try:
                provider = services.runtime._conversation._provider

                self.assertEqual(provider._config.model, "conversation-model")
                self.assertFalse(hasattr(services.runtime, "_planning"))
                self.assertFalse(hasattr(services.runtime, "_grounded_response"))
            finally:
                await services.aclose()

    async def test_northstar_customer_validation_is_injected_into_the_harness(self) -> None:
        with TemporaryDirectory() as directory:
            services = build_services(app_config(Path(directory) / "chat.sqlite3"))
            try:
                state = ConversationState(
                    customer=CustomerState(
                        first_name="Jane",
                        last_name="Doe",
                        email="jane.doe@example.com",
                    )
                )
                with self.assertRaises(PolicyError) as raised:
                    services.runtime._policy.customer_identity(
                        {"phone": "0077012345"},
                        state=state,
                    )

                self.assertEqual(
                    raised.exception.code,
                    PolicyCode.INVALID_CUSTOMER_DETAILS,
                )
                self.assertEqual(raised.exception.fields, ("phone",))
            finally:
                await services.aclose()
