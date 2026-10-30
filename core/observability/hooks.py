"""Local JSONL monitoring without logging customer messages or tool payloads."""
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from strands.hooks import HookProvider, HookRegistry, BeforeToolCallEvent, AfterToolCallEvent

logger = logging.getLogger("naija_bites.monitoring")


class ToolMonitoringHook(HookProvider):
    """Track tool names, durations and SDK-reported failures.

    Instantiate per agent. Never record tool input, output, or session ID.
    """

    def __init__(self, log_path=None):
        self.log_path = Path(log_path or os.getenv("MONITOR_LOG_PATH", "logs/tool_events.jsonl"))
        self._starts = {}
        self._lock = Lock()

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool)
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    def before_tool(self, event: BeforeToolCallEvent) -> None:
        try:
            tool = event.tool_use
            tool_id = tool.get("toolUseId")
            if tool_id is not None:
                with self._lock:
                    self._starts[tool_id] = time.perf_counter()
        except Exception:
            logger.exception("Monitoring hook failed before tool call")

    def after_tool(self, event: AfterToolCallEvent) -> None:
        try:
            tool = event.tool_use
            tool_id = tool.get("toolUseId")
            with self._lock:
                start = self._starts.pop(tool_id, None)
            result = getattr(event, "result", None)
            status = result.get("status") if isinstance(result, dict) else None
            failed = status == "error" or getattr(event, "exception", None) is not None
            duration = round((time.perf_counter() - start) * 1000, 2) if start is not None else None
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tool": tool.get("name", "unknown"),
                "duration_ms": duration,
                "status": "error" if failed else "completed",
            }
            logger.info("tool=%s status=%s duration_ms=%s", record["tool"], record["status"], duration)
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                with self.log_path.open("a", encoding="utf-8") as output:
                    output.write(json.dumps(record) + "\n")
        except Exception:
            logger.exception("Monitoring hook failed after tool call")
