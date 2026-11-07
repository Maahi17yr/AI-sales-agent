"""Privacy-safe per-run token metrics for Naija Bites.

Uses Strands' provider-reported token counts, not estimated character counts.
No prompts, chat text, customer IDs, or credentials are logged.
"""


def token_usage(agent):
    """Return sanitized usage and context size, or None if unavailable.

    Strands creates a new Agent per incoming request in this application.
    Prefer aggregate usage to include every model cycle; using only the latest
    invocation would omit earlier model calls. Direct summarization-model calls
    are not guaranteed to be included in the SDK totals.
    """
    metrics = getattr(agent, "event_loop_metrics", None)
    if metrics is None:
        return None

    latest = getattr(metrics, "latest_agent_invocation", None)
    # The SDK accumulates model usage across cycles. Use latest only if the
    # aggregate is unavailable; never add both (that would double-count).
    aggregate = getattr(metrics, "accumulated_usage", None)
    usage = aggregate if isinstance(aggregate, dict) and aggregate else getattr(latest, "usage", None)
    if not isinstance(usage, dict) or not usage:
        return None

    source_keys = {
        "input_tokens": "inputTokens",
        "output_tokens": "outputTokens",
        "total_tokens": "totalTokens",
        "cache_read_tokens": "cacheReadInputTokens",
        "cache_write_tokens": "cacheWriteInputTokens",
    }
    values = {
        key: usage.get(provider_key)
        for key, provider_key in source_keys.items()
        if type(usage.get(provider_key)) is int and usage[provider_key] >= 0
    }

    context_size = getattr(metrics, "latest_context_size", None)
    if type(context_size) is int and context_size >= 0:
        values["context_tokens"] = context_size
    if values:
        values["scope"] = "aggregate" if usage is aggregate else "latest_only"
    return values or None


def log_token_usage(logger, agent):
    """Log token counts only, without user content or personal identifiers."""
    usage = token_usage(agent)
    if usage is None:
        logger.info("agent_token_usage unavailable")
        return
    logger.info(
        "agent_token_usage input_tokens=%s output_tokens=%s total_tokens=%s "
        "cache_read_tokens=%s cache_write_tokens=%s context_tokens=%s "
        "scope=%s summarization_usage=unavailable",
        usage.get("input_tokens"), usage.get("output_tokens"),
        usage.get("total_tokens"), usage.get("cache_read_tokens"),
        usage.get("cache_write_tokens"), usage.get("context_tokens"),
        usage.get("scope"),
    )
