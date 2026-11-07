"""Privacy-safe tool hook wiring and minimal event logging."""
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from core.observability.correlation import bind, reset
from core.observability.hooks import ToolMonitoringHook


class ToolMetricsTests(unittest.TestCase):
    def test_hook_records_only_tool_duration_status_and_random_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            trace_id, request_id = str(uuid4()), str(uuid4())
            hook = ToolMonitoringHook(path, trace_id=trace_id, request_id=request_id)
            sensitive = "fictional-secret-should-never-be-logged"
            event = SimpleNamespace(tool_use={
                "toolUseId": sensitive, "name": "browse_menu",
                "input": {"message": sensitive},
            })
            hook.before_tool(event)
            hook.after_tool(SimpleNamespace(tool_use=event.tool_use,
                                           result={"status": "success", "output": sensitive},
                                           exception=None))
            hook.before_tool(event)
            hook.after_tool(SimpleNamespace(tool_use=event.tool_use,
                                           result={"status": "error", "output": sensitive},
                                           exception=ValueError(sensitive)))
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn(sensitive, raw)
            records = [json.loads(line) for line in raw.splitlines()]
            self.assertEqual([x["status"] for x in records], ["completed", "error"])
            for record in records:
                self.assertEqual(record["tool"], "browse_menu")
                self.assertEqual(record["trace_id"], trace_id)
                self.assertEqual(record["request_id"], request_id)
                self.assertGreaterEqual(record["duration_ms"], 0)
                self.assertEqual(set(record), {
                    "timestamp", "tool", "duration_ms", "status", "trace_id", "request_id",
                })

    def test_invalid_tool_name_cannot_inject_customer_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            hook = ToolMonitoringHook(path)
            event = SimpleNamespace(tool_use={
                "toolUseId": "fictional", "name": "customer@example.test\nprivate",
            })
            hook.before_tool(event)
            hook.after_tool(SimpleNamespace(tool_use=event.tool_use, result={}))
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("customer@example.test", raw)
            self.assertEqual(json.loads(raw)["tool"], "unknown")

    def test_agent_factory_registers_hook_with_bound_correlation(self):
        from core.intelligence import agent as agent_module

        trace_id, request_id = str(uuid4()), str(uuid4())
        tokens = bind(trace_id, request_id)
        try:
            with (
                patch.dict(os.environ, {"BEDROCK_MODEL_ID": "fictional-model"}),
                patch.object(agent_module.repository, "get_session", return_value={"customer_id": 1}),
                patch.object(agent_module.CartService, "view_cart", return_value={"items": []}),
                patch.object(agent_module, "BedrockModel"),
                patch.object(agent_module, "make_session_manager"),
                patch.object(agent_module, "make_conversation_manager"),
                patch.object(agent_module, "make_cart_tools", return_value=[]),
                patch.object(agent_module, "make_order_tools", return_value=[]),
                patch.object(agent_module, "make_preference_tools", return_value=[]),
                patch.object(agent_module.preferences, "get_preferences", return_value={}),
                patch.object(agent_module, "Agent") as factory,
            ):
                agent_module.make_agent("fictional-session")
                hooks = factory.call_args.kwargs["hooks"]
                self.assertEqual(len(hooks), 1)
                self.assertIsInstance(hooks[0], ToolMonitoringHook)
                self.assertEqual((hooks[0].trace_id, hooks[0].request_id),
                                 (trace_id, request_id))
        finally:
            reset(tokens)


if __name__ == "__main__":
    unittest.main()
