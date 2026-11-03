"""Offline regression for context summaries, token usage, and opt-in memory.

Uses a disposable SQLite database and mocks Bedrock. No real user data,
AWS requests, or external payment calls.
"""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["DATABASE_URL"] = ""
os.environ["NAIJA_BITES_ENV"] = "development"

import httpx
from core.api.server import app
from core.memory import preferences
from core.memory.context import context_settings, make_conversation_manager
from core.services import agent_usage_service
from data.database import models, repository


class ContextAndMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(models, "DB_PATH", Path(self.temp.name) / "memory.db")
        self.db_patch.start()
        models.create_tables()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 54321)),
            base_url="http://127.0.0.1:8000",
        )
        response = await self.client.post("/auth/register", json={
            "name": "Memory Test", "email": "memory@example.test",
            "password": "Fictional-Password-1234",
        })
        self.assertEqual(response.status_code, 201, response.text)
        login = await self.client.post("/auth/login", json={
            "email": "memory@example.test", "password": "Fictional-Password-1234",
        })
        self.assertEqual(login.status_code, 200, login.text)
        self.customer_id = login.json()["customer"]["id"]
        self.session_id = f"customer-{self.customer_id}"
        response = await self.client.get("/memory/pending")
        self.assertEqual(response.status_code, 200, response.text)

    async def asyncTearDown(self):
        await self.client.aclose()
        self.db_patch.stop()
        self.temp.cleanup()

    async def test_review_gate_and_durable_food_preference(self):
        original = preferences.request_preference(self.session_id, "spice_level", "mild")
        pending = (await self.client.get("/memory/pending")).json()
        self.assertTrue(pending["pending"])
        self.assertEqual(pending["proposal"]["value"], "mild")
        denied = await self.client.post("/memory/decision", json={
            "request_id": original["request_id"], "approved": False,
        })
        self.assertEqual(denied.status_code, 200, denied.text)
        self.assertFalse(denied.json()["saved"])
        self.assertEqual(preferences.get_preferences(self.session_id), {})

        second = preferences.request_preference(self.session_id, "spice_level", "extra spicy")
        accepted = await self.client.post("/memory/decision", json={
            "request_id": second["request_id"], "approved": True,
        })
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertTrue(accepted.json()["saved"])
        self.assertEqual(preferences.get_preferences(self.session_id), {"spice_level": "extra spicy"})
        # A session owned by the same customer sees the approved fact.
        another = f"customer-{self.customer_id}-test-2"
        repository.bind_session_customer(another, self.customer_id)
        self.assertEqual(preferences.get_preferences(another)["spice_level"], "extra spicy")
        wrong = await self.client.post("/memory/decision", json={
            "request_id": second["request_id"], "approved": True,
        })
        self.assertEqual(wrong.status_code, 409)

    async def test_token_totals_without_customer_text(self):
        agent = SimpleNamespace(event_loop_metrics=SimpleNamespace(accumulated_usage={
            "inputTokens": 120, "outputTokens": 35, "totalTokens": 155,
            "cacheReadInputTokens": 10, "cacheWriteInputTokens": 0,
        }))
        self.assertTrue(agent_usage_service.record_agent_usage(
            self.session_id, agent, "completed", 810,
        ))
        totals = agent_usage_service.recent_usage_summary(7)
        self.assertEqual(totals["requests"], 1)
        self.assertEqual(totals["total_tokens"], 155)
        self.assertEqual(totals["cache_read_tokens"], 10)
        self.assertEqual(totals["total_duration_ms"], 810)
        self.assertNotIn("customer_id", totals)
        self.assertNotIn("messages", totals)
        self.assertEqual((await self.client.get("/owner/usage")).status_code, 403)
        self.assertFalse(agent_usage_service.record_agent_usage(
            self.session_id, SimpleNamespace(), "completed", 10,
        ))

    async def test_context_policy_and_approved_prompt_injection(self):
        with patch.dict(os.environ, {
            "NAIJA_BITES_CONTEXT_THRESHOLD": "not a number",
            "NAIJA_BITES_CONTEXT_RECENT_MESSAGES": "999",
        }):
            self.assertEqual(context_settings()["compression_threshold"], 0.5)
            self.assertEqual(context_settings()["preserve_recent_messages"], 10)
        manager = make_conversation_manager()
        self.assertEqual(type(manager).__name__, "SummarizingConversationManager")

        # The prompt includes saved food preferences but never pending proposals.
        from core.intelligence import agent as agent_module
        proposed = preferences.request_preference(self.session_id, "favorite_food", "egusi")
        with patch.dict(os.environ, {"BEDROCK_MODEL_ID": "fake-model"}), \
             patch.object(agent_module, "BedrockModel", return_value=object()), \
             patch.object(agent_module, "Agent", side_effect=lambda **kwargs: kwargs), \
             patch.object(agent_module, "make_session_manager", return_value=object()):
            before = agent_module.make_agent(self.session_id)
            self.assertNotIn('"favorite_food": "egusi"', before["system_prompt"])
            saved = await self.client.post("/memory/decision", json={
                "request_id": proposed["request_id"], "approved": True,
            })
            self.assertEqual(saved.status_code, 200, saved.text)
            after = agent_module.make_agent(self.session_id)
            self.assertIn('"favorite_food": "egusi"', after["system_prompt"])
            self.assertEqual(type(after["conversation_manager"]).__name__, "SummarizingConversationManager")


if __name__ == "__main__":
    unittest.main()
