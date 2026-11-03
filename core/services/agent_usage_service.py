"""Privacy-minimal model token accounting for each completed agent request.

Only numeric usage, duration, model identifier, and outcome are saved.
Never record prompts, tool arguments, addresses, or other chat content.
"""
import logging
import os
from datetime import datetime, timedelta, timezone

from data.database.models import get_connection
from data.database import repository

logger = logging.getLogger(__name__)
_TOKEN_KEYS = {
    "input_tokens": "inputTokens",
    "output_tokens": "outputTokens",
    "total_tokens": "totalTokens",
    "cache_read_tokens": "cacheReadInputTokens",
    "cache_write_tokens": "cacheWriteInputTokens",
}


def extract_token_usage(agent) -> dict | None:
    """Read the Strands Agent's real accumulated provider usage, not guesses."""
    # An AgentResult is produced by the *running* agent inside AG-UI.
    # The wrapper clones template agents, so do not rely on template metrics.
    metrics = getattr(agent, "metrics", None)
    if metrics is None:
        metrics = getattr(agent, "event_loop_metrics", None)
    usage = getattr(metrics, "accumulated_usage", None)
    if not isinstance(usage, dict):
        return None
    result = {}
    for field, provider_key in _TOKEN_KEYS.items():
        value = usage.get(provider_key, 0)
        # bool is not a meaningful token count; refuse malformed metrics.
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        result[field] = value
    if result["total_tokens"] == 0:
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result


def record_agent_usage(session_id: str, agent, outcome: str, duration_ms: int) -> bool:
    """Persist token metadata when supplied by the provider. Fail safe upstream."""
    usage = extract_token_usage(agent)
    if usage is None:
        logger.info("agent_usage_unavailable outcome=%s", outcome)
        return False
    session = repository.get_session(session_id)
    if not session or session.get("customer_id") is None:
        return False
    model_id = os.getenv("BEDROCK_MODEL_ID", "unknown")[:160]
    duration = max(0, int(duration_ms))
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO agent_usage_events
                (customer_id, model_id, input_tokens, output_tokens, total_tokens,
                 cache_read_tokens, cache_write_tokens, duration_ms, outcome)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session["customer_id"], model_id,
                usage["input_tokens"], usage["output_tokens"], usage["total_tokens"],
                usage["cache_read_tokens"], usage["cache_write_tokens"],
                duration, outcome if outcome in {"completed", "error"} else "error",
            ),
        )
    logger.info(
        "agent_usage input_tokens=%d output_tokens=%d total_tokens=%d "
        "cache_read_tokens=%d cache_write_tokens=%d duration_ms=%d outcome=%s",
        usage["input_tokens"], usage["output_tokens"], usage["total_tokens"],
        usage["cache_read_tokens"], usage["cache_write_tokens"], duration, outcome,
    )
    return True


def recent_usage_summary(days: int = 7) -> dict:
    """Owner-only aggregate; deliberately excludes individual customer identities."""
    days = max(1, min(int(days), 30))
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS requests,
                      COALESCE(SUM(input_tokens), 0) AS input_tokens,
                      COALESCE(SUM(output_tokens), 0) AS output_tokens,
                      COALESCE(SUM(total_tokens), 0) AS total_tokens,
                      COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                      COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens,
                      COALESCE(SUM(duration_ms), 0) AS total_duration_ms
               FROM agent_usage_events WHERE created_at >= ?""", (start,)
        ).fetchone()
    return {
        "period_days": days,
        "requests": row["requests"],
        "input_tokens": row["input_tokens"],
        "output_tokens": row["output_tokens"],
        "total_tokens": row["total_tokens"],
        "cache_read_tokens": row["cache_read_tokens"],
        "cache_write_tokens": row["cache_write_tokens"],
        "total_duration_ms": row["total_duration_ms"],
        "note": "Token counts are provider-reported. This is not a billing invoice.",
    }
