from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from server.services import build_services
from webchat.harness.policy import PolicyCode, PolicyError
from webchat.harness.state import ConversationState, CustomerState

from .support import app_config


class ServiceCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_planner_and_grounded_response_use_their_configured_models(self) -> None:
        with TemporaryDirectory() as directory:
            services = build_services(
                app_config(
                    Path(directory) / "chat.sqlite3",
                    planner_model="fast-planner",
                    response_model="strong-response",
                )
            )
            try:
                planner = services.runtime._planning._provider._provider
                responder = services.runtime._grounded_response._provider

                self.assertEqual(planner._config.model, "fast-planner")
                self.assertEqual(responder._config.model, "strong-response")
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
