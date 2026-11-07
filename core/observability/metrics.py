"""Privacy-safe timings, counts and first visible text detection.

No SQL text, parameters, queries, provider responses or user content are logged.
"""
import logging
import re
import time
from collections import Counter
from contextvars import ContextVar
from core.observability.correlation import current_ids

logger = logging.getLogger("naija_bites.metrics")
_db_counts = ContextVar("naija_db_query_counts", default=None)
_db_total = ContextVar("naija_db_total_ms", default=None)
_agent_counts = ContextVar("naija_agent_call_counts", default=None)


def begin_agent_counting():
    return _agent_counts.set({
        "model_calls": 0, "model_failures": 0,
        "tool_calls": 0, "tool_failures": 0,
    })


def finish_agent_counting(token):
    _agent_counts.reset(token)


def count_agent_operation(kind, failed=False):
    """Called only by Strands hook callbacks; do not inspect event payloads."""
    counts = _agent_counts.get()
    if counts is None:
        return
    if kind not in ("model", "tool"):
        return
    if failed:
        counts[kind + "_failures"] += 1
    else:
        counts[kind + "_calls"] += 1


def snapshot_agent_counts():
    counts = _agent_counts.get()
    return dict(counts) if counts is not None else None



def record_duration(component, operation, elapsed_ms, status="success"):
    # Only call with static, code-owned labels.
    trace_id, request_id = current_ids()
    logger.info(
        "operation_metric trace_id=%s request_id=%s component=%s operation=%s "
        "duration_ms=%.2f status=%s",
        trace_id, request_id, component, operation, elapsed_ms, status,
    )


def record_db_query(backend, elapsed_ms, succeeded):
    trace_id, request_id = current_ids()
    counts = _db_counts.get()
    if counts is not None:
        counts[backend] += 1
    total = _db_total.get()
    if total is not None:
        total[0] += elapsed_ms
    logger.info(
        "db_metric trace_id=%s request_id=%s backend=%s duration_ms=%.2f "
        "status=%s",
        trace_id, request_id, backend, elapsed_ms,
        "success" if succeeded else "error",
    )


def start_request_db_metrics():
    return _db_counts.set(Counter()), _db_total.set([0.0])


def snapshot_request_db_metrics():
    """Read shared counters in the stream task, without resetting another task's tokens."""
    counts = _db_counts.get()
    total = _db_total.get()
    return dict(counts or {}), round((total or [0.0])[0], 2)


def finish_request_db_metrics(tokens):
    counts = _db_counts.get()
    total = _db_total.get()
    result = (dict(counts or {}), round((total or [0.0])[0], 2))
    _db_counts.reset(tokens[0])
    _db_total.reset(tokens[1])
    return result


def is_first_text_event(event):
    """Accept only known text-delta event types, not tool or lifecycle events."""
    kind = getattr(event, "type", None)
    if kind is None and isinstance(event, dict):
        kind = event.get("type")
    kind = getattr(kind, "value", kind)
    if kind not in ("TEXT_MESSAGE_CONTENT", "text_message_content"):
        return False
    delta = getattr(event, "delta", None)
    if delta is None and isinstance(event, dict):
        delta = event.get("delta")
    return isinstance(delta, str) and bool(delta)


class FirstVisibleText:
    """Detect text that survives the frontend's hidden-thought filtering.

    Hold at most 16 KiB in memory, never log text. Return unavailable (None)
    rather than falsely reporting visible text if that bound is exceeded.
    """

    def __init__(self):
        self.buffer = ""
        self.exceeded = False

    def observe(self, event):
        if not is_first_text_event(event) or self.exceeded:
            return False
        delta = event.get("delta") if isinstance(event, dict) else getattr(event, "delta", "")
        if len(self.buffer) + len(delta) > 16384:
            self.buffer = ""
            self.exceeded = True
            return False
        self.buffer += delta
        visible = re.sub(
            r"<(?:think|thinking)\\b[^>]*>.*?</(?:think|thinking)\\s*>",
            "", self.buffer, flags=re.I | re.S,
        )
        visible = re.sub(
            r"<(?:think|thinking)\\b[^>]*>.*$", "",
            visible, flags=re.I | re.S,
        )
        visible = re.sub(r"</?(?:think|thinking)\\b[^>]*>", "", visible, flags=re.I)
        visible = re.sub(
            r"<(?:t|th|thi|thin|think|thinki|thinkin|thinking)$",
            "", visible, flags=re.I,
        )
        return bool(visible.strip())


class TimedSQLiteConnection:
    """Narrow delegation wrapper preserving SQLite connection semantics."""
    def __init__(self, connection):
        self._connection = connection

    def execute(self, sql, params=()):
        started = time.perf_counter()
        try:
            result = self._connection.execute(sql, params)
        except Exception:
            record_db_query("sqlite", (time.perf_counter() - started) * 1000, False)
            raise
        record_db_query("sqlite", (time.perf_counter() - started) * 1000, True)
        return result

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._connection.__exit__(exc_type, exc, tb)
