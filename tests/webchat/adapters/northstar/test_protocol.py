import inspect
import unittest

import httpx

from webchat.adapters.northstar import NorthstarAdapter, NorthstarClient, NorthstarConfig
from webchat.domain import DealerAdapter


class NorthstarProtocolTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_adapter_implements_all_24_exact_dealer_signatures(self) -> None:
        protocol_methods = {
            name: value
            for name, value in DealerAdapter.__dict__.items()
            if inspect.iscoroutinefunction(value)
        }

        self.assertEqual(len(protocol_methods), 24)
        for name, protocol_method in protocol_methods.items():
            with self.subTest(name=name):
                self.assertTrue(hasattr(NorthstarAdapter, name))
                self.assertEqual(
                    inspect.signature(getattr(NorthstarAdapter, name)),
                    inspect.signature(protocol_method),
                )

    async def test_adapter_conforms_to_runtime_checkable_protocol(self) -> None:
        config = NorthstarConfig("http://northstar.test", "server-secret")
        http = httpx.AsyncClient(
            base_url=config.base_url,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        )
        adapter = NorthstarAdapter(NorthstarClient(config, http))
        self.addAsyncCleanup(adapter.aclose)

        self.assertIsInstance(adapter, DealerAdapter)


if __name__ == "__main__":
    unittest.main()
