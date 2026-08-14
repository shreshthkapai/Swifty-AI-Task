from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from server.services import build_services

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
