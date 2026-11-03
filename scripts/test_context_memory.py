"""Naija Bites context/memory regression tests (isolated, no AWS or Paystack)."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

# Use an isolated local DB, never the production Neon database.
os.environ["DATABASE_URL"] = ""
os.environ["NAIJA_BITES_ENV"] = "development"

import httpx
from data.database import models
from core.api.server import app
from core.memory import preferences
from core.memory.context import (
    MAX_CHAT_MESSAGES, MIN_RECENT_MESSAGES, SUMMARY_SYSTEM_PROMPT,
    bounded_chat_messages, make_conversation_manager,
)
from core.memory.token_metrics import token_usage, log_token_usage


class BoundedContextTests(unittest.TestCase):
    def test_request_history_is_bounded_without_mutating_saved_chat(self):
        source = list(range(100))
        bounded = bounded_chat_messages(source)
        self.assertEqual(len(bounded), MAX_CHAT_MESSAGES)
        self.assertEqual(bounded, source[-MAX_CHAT_MESSAGES:])
        self.assertEqual(len(source), 100)
        self.assertEqual(bounded_chat_messages([1, 2]), [1, 2])

    def test_summarizer_has_proactive_budget_and_sensitive_data_guardrails(self):
        with patch("strands.agent.conversation_manager.SummarizingConversationManager") as cls:
            make_conversation_manager()
            cfg = cls.call_args.kwargs
        self.assertEqual(cfg["preserve_recent_messages"], MIN_RECENT_MESSAGES)
        self.assertLess(cfg["proactive_compression"]["compression_threshold"], 0.8)
        self.assertIn("delivery addresses", SUMMARY_SYSTEM_PROMPT)
        self.assertIn("payment details", SUMMARY_SYSTEM_PROMPT)

    def test_agent_uses_only_approved_preferences_and_bounded_context(self):
        from core.intelligence import agent as agent_module

        with (
            patch.dict(os.environ, {"BEDROCK_MODEL_ID": "test-model"}),
            patch.object(agent_module.repository, "get_session", return_value={"customer_id": 1}),
            patch.object(agent_module.CartService, "view_cart", return_value={"items": []}),
            patch.object(agent_module, "BedrockModel"),
            patch.object(agent_module, "make_session_manager"),
            patch.object(agent_module, "make_conversation_manager") as manager,
            patch.object(agent_module, "make_cart_tools", return_value=[]),
            patch.object(agent_module, "make_order_tools", return_value=[]),
            patch.object(agent_module, "make_preference_tools", return_value=[]),
            patch.object(agent_module.preferences, "get_preferences",
                         return_value={"favorite_food": "Party Jollof Rice"}) as approved,
            patch.object(agent_module, "Agent") as agent_factory,
        ):
            agent_module.make_agent("customer-1")
            cfg = agent_factory.call_args.kwargs
            self.assertIn("Party Jollof Rice", cfg["system_prompt"])
            self.assertIn("UNTRUSTED DATA", cfg["system_prompt"])
            self.assertIs(cfg["conversation_manager"], manager.return_value)
            approved.assert_called_once_with("customer-1")
            approved.return_value = {}
            agent_module.make_agent("customer-1")
            without_approval = agent_factory.call_args.kwargs["system_prompt"]
            self.assertNotIn("Party Jollof Rice", without_approval)

    def test_token_metrics_use_actual_sdk_counts_and_no_content(self):
        metrics = SimpleNamespace(
            latest_agent_invocation=SimpleNamespace(usage={
                "inputTokens": 1200, "outputTokens": 75, "totalTokens": 1275,
                "cacheReadInputTokens": 300,
                "customer_email": "secret@example.test",
            }),
            accumulated_usage={"totalTokens": 999999},
            latest_context_size=1300,
        )
        agent = SimpleNamespace(event_loop_metrics=metrics)
        result = token_usage(agent)
        self.assertEqual(result["input_tokens"], 1200)
        self.assertEqual(result["output_tokens"], 75)
        self.assertEqual(result["cache_read_tokens"], 300)
        self.assertEqual(result["context_tokens"], 1300)
        self.assertNotIn("customer_email", result)
        logger = Mock()
        log_token_usage(logger, agent)
        msg = str(logger.info.call_args)
        self.assertNotIn("secret@example.test", msg)
        self.assertEqual(token_usage(SimpleNamespace(event_loop_metrics=None)), None)


class ApprovedMemoryAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(models, "DB_PATH", Path(self.temp.name) / "memory.db")
        self.db_patch.start()
        models.create_tables()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 54321)),
            base_url="http://127.0.0.1:8000",
        )
        self.email = "memory-owner@example.test"
        self.password = "Fictional-Password-1234"
        res = await self.client.post("/auth/register", json={
            "name": "Memory Customer", "email": self.email, "password": self.password,
        })
        self.assertEqual(res.status_code, 201, res.text)
        await self.login(self.email)
        auth = (await self.client.get("/auth/me")).json()["customer"]
        self.session_id = f"customer-{auth['id']}"
        # This endpoint securely binds the session to the authenticated user.
        self.assertEqual((await self.client.get("/memory/preferences")).status_code, 200)

    async def asyncTearDown(self):
        await self.client.aclose()
        self.db_patch.stop()
        self.temp.cleanup()

    async def login(self, email):
        res = await self.client.post("/auth/login", json={"email": email, "password": self.password})
        self.assertEqual(res.status_code, 200, res.text)

    async def test_only_explicit_approval_saves_and_memory_is_customer_scoped(self):
        self.assertEqual((await self.client.get("/memory/preferences")).json()["preferences"], {})
        proposed = preferences.request_preference(self.session_id, "favorite_food", "Party Jollof Rice")
        self.assertEqual((await self.client.get("/memory/preferences")).json()["preferences"], {})
        pending = (await self.client.get("/memory/pending")).json()
        self.assertTrue(pending["pending"])
        self.assertEqual(pending["proposal"]["request_id"], proposed["request_id"])

        declined = await self.client.post("/memory/decision", json={
            "request_id": proposed["request_id"], "approved": False,
        })
        self.assertEqual(declined.status_code, 200, declined.text)
        self.assertEqual((await self.client.get("/memory/preferences")).json()["preferences"], {})

        proposed = preferences.request_preference(self.session_id, "favorite_food", "Party Jollof Rice")
        approved = await self.client.post("/memory/decision", json={
            "request_id": proposed["request_id"], "approved": True,
        })
        self.assertEqual(approved.status_code, 200, approved.text)
        expected = {"favorite_food": "Party Jollof Rice"}
        self.assertEqual((await self.client.get("/memory/preferences")).json()["preferences"], expected)
        self.assertFalse((await self.client.get("/memory/pending")).json()["pending"])

        # Database preference survives signing out and returning to a new chat.
        await self.client.post("/auth/logout")
        await self.login(self.email)
        self.assertEqual((await self.client.get("/memory/preferences")).json()["preferences"], expected)

        other_email = "other-memory-customer@example.test"
        res = await self.client.post("/auth/register", json={
            "name": "Other Customer", "email": other_email, "password": self.password,
        })
        self.assertEqual(res.status_code, 201, res.text)
        await self.login(other_email)
        self.assertEqual((await self.client.get("/memory/preferences")).json()["preferences"], {})
        self.assertFalse((await self.client.get("/memory/pending")).json()["pending"])

        await self.login(self.email)
        removed = await self.client.delete("/memory/preferences/favorite_food")
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertTrue(removed.json()["deleted"])
        self.assertEqual((await self.client.get("/memory/preferences")).json()["preferences"], {})

    async def test_memory_routes_require_auth_and_reject_invalid_preference(self):
        await self.client.post("/auth/logout")
        self.assertEqual((await self.client.get("/memory/preferences")).status_code, 401)
        self.assertEqual((await self.client.get("/memory/pending")).status_code, 401)
        self.assertEqual((await self.client.delete("/memory/preferences/favorite_food")).status_code, 401)
        await self.login(self.email)
        bad = await self.client.delete("/memory/preferences/password")
        self.assertEqual(bad.status_code, 400)


if __name__ == "__main__":
    unittest.main()
