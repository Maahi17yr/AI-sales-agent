"""Optional OpenTelemetry tracing. Disabled by default for customer privacy."""
import os
from threading import Lock

_lock = Lock()
_initialized = False


def setup_observability():
    """Configure Strands tracing once per Python process.

    OBSERVABILITY_MODE=off (default), console, or otlp.
    CAUTION: raw spans may include customer messages and tool arguments.
    """
    global _initialized
    with _lock:
        if _initialized:
            return
        mode = os.getenv("OBSERVABILITY_MODE", "off").strip().lower()
        if mode == "off":
            _initialized = True
            return
        if mode not in {"console", "otlp"}:
            raise ValueError("OBSERVABILITY_MODE must be off, console, or otlp")
        if mode == "otlp" and not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
            raise ValueError("Set OTEL_EXPORTER_OTLP_ENDPOINT for OTLP tracing")
        from strands.telemetry import StrandsTelemetry
        telemetry = StrandsTelemetry()
        if mode == "console":
            telemetry.setup_console_exporter()
        else:
            telemetry.setup_otlp_exporter()
        _initialized = True
