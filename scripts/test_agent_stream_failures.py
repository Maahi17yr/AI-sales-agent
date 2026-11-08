"""Mocked AG-UI error stream regressions; no Bedrock or customer data."""
import unittest
from unittest.mock import patch

import httpx

from core.api import server


class StreamFailureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=server.app, client=("127.0.0.1", 54321)),
            base_url="http://127.0.0.1:8000",
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_model_failure_emits_sanitized_terminal_event_without_retry(self):
        calls = []

        class FakeAgent:
            def __init__(self, **kwargs):
                pass

            async def run(self, _input):
                calls.append("started")
                raise RuntimeError("sk_test_BEDROCK_INTERNAL_SECRET")
                yield  # makes this an async generator

        payload = {
            "threadId": "test-thread", "runId": "test-run",
            "messages": [], "state": {}, "tools": [],
            "context": [], "forwardedProps": {},
        }
        with patch.object(server, "_customer_session", return_value="test-session"), \
             patch.object(server, "make_agent", return_value=object()), \
             patch.object(server, "StrandsAgent", FakeAgent), \
             patch.object(server, "log_token_usage", return_value=None):
            response = await self.client.post("/agent", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('"type":"RUN_ERROR"', response.text)
        self.assertNotIn("sk_test_BEDROCK_INTERNAL_SECRET", response.text)
        self.assertEqual(calls, ["started"])
        self.assertTrue(response.text.endswith("\n\n"))

    async def test_usage_observability_failure_cannot_break_checkout_state(self):
        class FakeAgent:
            def __init__(self, **kwargs):
                pass

            async def run(self, _input):
                if False:
                    yield None

        payload = {
            "threadId": "test-thread", "runId": "test-run",
            "messages": [], "state": {}, "tools": [],
            "context": [], "forwardedProps": {},
        }
        with patch.object(server, "_customer_session", return_value="test-session"), \
             patch.object(server, "make_agent", return_value=object()), \
             patch.object(server, "StrandsAgent", FakeAgent), \
             patch.object(server, "get_checkout_state", return_value={"pending": False}), \
             patch.object(server, "log_token_usage",
                          side_effect=ValueError("SECRET_OBSERVABILITY")):
            response = await self.client.post("/agent", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('"type":"CHECKOUT_STATE"', response.text)
        self.assertNotIn("SECRET_OBSERVABILITY", response.text)


if __name__ == "__main__":
    unittest.main()
