"""Request/journey correlation without logging user inputs or session data."""
import logging
import time
from contextvars import ContextVar
from uuid import UUID, uuid4

_trace = ContextVar("naija_trace_id", default=None)
_request = ContextVar("naija_request_id", default=None)
logger = logging.getLogger("naija_bites.metrics")


def new_id():
    return str(uuid4())


def validated_trace_id(value):
    if not isinstance(value, str) or len(value) != 36:
        return new_id()
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        return new_id()
    return str(parsed) if parsed.version == 4 and str(parsed) == value.lower() else new_id()


def bind(trace_id, request_id):
    return _trace.set(trace_id), _request.set(request_id)


def reset(tokens):
    _trace.reset(tokens[0])
    _request.reset(tokens[1])


def current_ids():
    return _trace.get(), _request.get()


def stage_for_path(path):
    if path == "/agent":
        return "chat"
    if path.startswith("/checkout"):
        return "checkout"
    if path.startswith("/payments/"):
        return "payment"
    return None


def log_request(trace_id, request_id, stage, status, duration_ms, outcome="headers"):
    if stage is not None:
        logger.info(
            "request_metric trace_id=%s request_id=%s stage=%s status=%d "
            "duration_ms=%.2f outcome=%s",
            trace_id, request_id, stage, status, duration_ms, outcome,
        )
