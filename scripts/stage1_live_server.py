"""Isolated live Bedrock tracing with a shared, pre-reserved inference budget."""
import fcntl
import json
import os
import re
import time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import boto3
import uvicorn
from botocore.client import BaseClient

from scripts.stage1_provider_server import (
    TraceRequests, agent_factory, app, repository, server, timed_make_agent,
    timed_menu, trace,
)

LEDGER = Path("/tmp/stage1-live-budget.json")
MODEL = os.environ.get("BEDROCK_MODEL_ID", "").strip()
# Conservative USD/MTok rates above standard Sonnet 4.5 regional pricing.
INPUT_RATE = 5
OUTPUT_RATE = 25
MAX_OUTPUT = 768
original_call = BaseClient._make_api_call


def update_budget(reserve, refund=0):
    with LEDGER.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        content = handle.read()
        state = json.loads(content) if content else {"charged_upper_usd": 0, "calls": 0}
        amount = state["charged_upper_usd"] + reserve - refund
        limit = float(os.environ["STAGE1_LIVE_BUDGET_USD"])
        if amount > limit or (reserve and state["calls"] >= 24):
            raise RuntimeError("Stage 1 inference budget exhausted")
        state["charged_upper_usd"] = round(max(0, amount), 8)
        if reserve:
            state["calls"] += 1
        handle.seek(0)
        handle.truncate()
        json.dump(state, handle)
        handle.flush()
        os.fsync(handle.fileno())
        return state


class MeteredStream:
    def __init__(self, stream, started, record, reservation):
        self.stream = stream
        self.started = started
        self.record = record
        self.reservation = reservation

    def __iter__(self):
        first = True
        for event in self.stream:
            delta = event.get("contentBlockDelta", {}).get("delta", {})
            if first and delta.get("text"):
                self.record["provider_first_text_ms"].append(
                    round((time.perf_counter() - self.started) * 1000, 2))
                first = False
            metadata = event.get("metadata", {})
            if "usage" in metadata:
                usage = metadata["usage"]
                actual_upper = (usage["inputTokens"] * INPUT_RATE +
                                usage["outputTokens"] * OUTPUT_RATE) / 1_000_000
                state = update_budget(0, max(0, self.reservation - actual_upper))
                self.record.setdefault("usage", []).append({
                    "input_tokens": usage["inputTokens"],
                    "output_tokens": usage["outputTokens"],
                    "conservative_cost_usd": round(actual_upper, 8),
                })
                self.record["budget"] = state
            yield event

    def close(self):
        self.stream.close()


def live_call(client, operation, params):
    if client.meta.service_model.service_name != "bedrock-runtime":
        raise RuntimeError("Unexpected AWS service in isolated live benchmark")
    if operation != "ConverseStream":
        raise RuntimeError("Unexpected AWS operation in isolated live benchmark")
    if params["modelId"] != MODEL:
        raise RuntimeError("Unexpected model in isolated live benchmark")
    request = dict(params)
    request["inferenceConfig"] = {**request.get("inferenceConfig", {}), "maxTokens": MAX_OUTPUT}
    if request.get("guardrailConfig") or request.get("performanceConfig", {}).get("latency") == "optimized":
        raise RuntimeError("Unbudgeted inference configuration")
    native_input = {key: request[key] for key in ("messages", "system", "toolConfig") if key in request}
    counted = original_call(client, "CountTokens", {"modelId": MODEL, "input": {"converse": native_input}})
    tokens = counted["inputTokens"]
    if tokens > 30_000:
        raise RuntimeError("Live benchmark input exceeds approved token bound")
    reservation = (tokens * INPUT_RATE + MAX_OUTPUT * OUTPUT_RATE) / 1_000_000
    update_budget(reservation)
    started = time.perf_counter()
    record = trace.get()
    response = original_call(client, operation, request)
    response["stream"] = MeteredStream(response["stream"], started, record, reservation)
    return response


if __name__ == "__main__":
    if not re.fullmatch(r"(?:us\.|eu\.|au\.|jp\.|global\.)?anthropic\.claude-(?:sonnet-4-5-20250929|haiku-4-5-20251001)-v1:0", MODEL):
        raise RuntimeError("Live budget rates require the configured Sonnet 4.5 or Haiku 4.5 model ID")
    if not 0 < float(os.environ.get("STAGE1_LIVE_BUDGET_USD", "0")) <= 1:
        raise RuntimeError("Live budget must be within the approved US$1 ceiling")
    if not os.environ.get("AWS_ACCESS_KEY_ID") or not os.environ.get("AWS_SECRET_ACCESS_KEY"):
        raise RuntimeError("Stage 1 AWS credentials are missing")
    if not os.environ.get("AWS_SESSION_TOKEN"):
        os.environ.pop("AWS_SESSION_TOKEN", None)
    with ExitStack() as overrides:
        overrides.enter_context(patch.object(BaseClient, "_make_api_call", live_call))
        overrides.enter_context(patch.object(repository, "get_orderable_products", timed_menu))
        overrides.enter_context(patch.object(server, "make_agent", timed_make_agent))
        if os.getenv("STAGE1_SDK_BASELINE") == "1":
            overrides.enter_context(patch.object(agent_factory, "_bedrock_session",
                lambda region: boto3.Session(region_name=region)))
        uvicorn.run(TraceRequests(app), host="127.0.0.1", port=8444,
                    ssl_keyfile="/tmp/naija-proxy-test.key", ssl_certfile="/tmp/naija-proxy-test.crt")
