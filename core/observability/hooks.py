"""Local JSONL monitoring without logging customer messages or tool payloads."""
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from strands.hooks import (HookProvider, HookRegistry, BeforeToolCallEvent,
                           AfterToolCallEvent, BeforeModelCallEvent, AfterModelCallEvent)
from core.observability.metrics import count_agent_operation

logger = logging.getLogger("naija_bites.monitoring")


class ToolMonitoringHook(HookProvider):
    """Track tool names, durations and SDK-reported failures.

    Instantiate per agent. Never record tool input, output, or session ID.
    """

    def __init__(self, log_path=None, trace_id=None, request_id=None):
        self.log_path = Path(log_path or os.getenv("MONITOR_LOG_PATH", "logs/tool_events.jsonl"))
        self.trace_id = trace_id
        self.request_id = request_id
        self._starts = {}
        self._lock = Lock()

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool)
        registry.add_callback(AfterToolCallEvent, self.after_tool)
        registry.add_callback(BeforeModelCallEvent, self.before_model)
        registry.add_callback(AfterModelCallEvent, self.after_model)

    def before_model(self, event: BeforeModelCallEvent) -> None:
        count_agent_operation("model")

    def after_model(self, event: AfterModelCallEvent) -> None:
        if getattr(event, "exception", None) is not None:
            count_agent_operation("model", failed=True)


    def before_tool(self, event: BeforeToolCallEvent) -> None:
        count_agent_operation("tool")
        try:
            tool = event.tool_use
            tool_id = tool.get("toolUseId")
            if tool_id is not None:
                with self._lock:
                    self._starts[tool_id] = time.perf_counter()
        except Exception:
            logger.warning("tool_monitoring_failed phase=before")

    def after_tool(self, event: AfterToolCallEvent) -> None:
        try:
            tool = event.tool_use
            tool_id = tool.get("toolUseId")
            with self._lock:
                start = self._starts.pop(tool_id, None)
            result = getattr(event, "result", None)
            status = result.get("status") if isinstance(result, dict) else None
            failed = status == "error" or getattr(event, "exception", None) is not None
            if failed:
                count_agent_operation("tool", failed=True)
            duration = round((time.perf_counter() - start) * 1000, 2) if start is not None else None
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tool": (tool.get("name") if isinstance(tool.get("name"), str)
                         and re.fullmatch(r"[a-zA-Z_][a-zA-Z_0-9]{0,63}", tool["name"])
                         else "unknown"),
                "trace_id": self.trace_id,
                "request_id": self.request_id,
                "duration_ms": duration,
                "status": "error" if failed else "completed",
            }
            logger.info("tool=%s status=%s duration_ms=%s", record["tool"], record["status"], duration)
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                with self.log_path.open("a", encoding="utf-8") as output:
                    output.write(json.dumps(record) + "\n")
        except Exception:
            logger.warning("tool_monitoring_failed phase=after")
