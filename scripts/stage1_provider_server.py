"""Run the real agent against disposable SQLite and a deterministic ConverseStream fixture."""
import os
import json
import time
from unittest.mock import patch
from uuid import uuid4
from contextvars import ContextVar
from contextlib import ExitStack
import boto3
from core.intelligence import agent as agent_factory
from data.database import repository

import uvicorn
from botocore.client import BaseClient

# Import enforces the existing /tmp, development, no DATABASE_URL and mock-provider guards.
from scripts import mock_payment_backend
from core.api import server
from core.api.server import app


trace = ContextVar("stage1_trace")


class TraceRequests:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/agent":
            return await self.app(scope, receive, send)
        record = {"provider_first_text_ms": [], "menu_tool_ms": [], "server_first_text_epoch_ms": None}
        token = trace.set(record)
        async def observe(message):
            if message["type"] == "http.response.start":
                record["request_id"] = dict(message["headers"]).get(b"x-request-id", b"").decode()
            if (message["type"] == "http.response.body" and record["server_first_text_epoch_ms"] is None
                    and b'TEXT_MESSAGE_CONTENT' in message.get("body", b"")):
                record["server_first_text_epoch_ms"] = time.time_ns() / 1e6
            await send(message)
        try:
            await self.app(scope, receive, observe)
        finally:
            print("STAGE1_SERVER", json.dumps(record), flush=True)
            trace.reset(token)


real_make_agent = server.make_agent


def timed_make_agent(session_id):
    started = time.perf_counter()
    try:
        return real_make_agent(session_id)
    finally:
        trace.get()["agent_init_ms"] = round((time.perf_counter() - started) * 1000, 2)


real_menu = repository.get_orderable_products


def timed_menu(*args, **kwargs):
    started = time.perf_counter()
    try:
        return real_menu(*args, **kwargs)
    finally:
        trace.get()["menu_tool_ms"].append(round((time.perf_counter() - started) * 1000, 2))


def provider_call(client, operation, params):
    if client.meta.service_model.service_name != "bedrock-runtime" or operation != "ConverseStream":
        raise RuntimeError("Unexpected provider operation in isolated benchmark")
    messages = params["messages"]
    prompts = [item["text"] for message in messages if message["role"] == "user"
               for item in message["content"] if "text" in item]
    prompt = prompts[-1].lower() if prompts else ""
    tool_result = any("toolResult" in item for item in messages[-1]["content"])
    menu = "browse_menu" in prompt and not tool_result
    started = time.perf_counter()
    tool_id = str(uuid4())
    record = trace.get()
    def events():
        yield {"messageStart": {"role": "assistant"}}
        if menu:
            yield {"contentBlockStart": {"contentBlockIndex": 0, "start": {
                "toolUse": {"toolUseId": tool_id, "name": "browse_menu"}}}}
            yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {
                "toolUse": {"input": json.dumps({})}}}}
        else:
            count = 100 if "long response" in prompt else 10
            for index in range(count):
                time.sleep(.008)
                if index == 0:
                    record["provider_first_text_ms"].append(round((time.perf_counter() - started) * 1000, 2))
                yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "bite "}}}
        yield {"contentBlockStop": {"contentBlockIndex": 0}}
        yield {"messageStop": {"stopReason": "tool_use" if menu else "end_turn"}}
        yield {"metadata": {"usage": {"inputTokens": 20, "outputTokens": 15, "totalTokens": 35},
                            "metrics": {"latencyMs": 80}}}
    return {"stream": events()}


if __name__ == "__main__":
    os.environ.update(AWS_ACCESS_KEY_ID="fixture", AWS_SECRET_ACCESS_KEY="fixture",
                      AWS_DEFAULT_REGION="us-east-1", BEDROCK_MODEL_ID="fixture")
    with (
        patch.object(BaseClient, "_make_api_call", provider_call),
        patch.object(repository, "get_orderable_products", timed_menu),
        patch.object(server, "make_agent", timed_make_agent),
    ):
        with ExitStack() as overrides:
            if os.getenv("STAGE1_SDK_BASELINE") == "1":
                overrides.enter_context(patch.object(agent_factory, "_bedrock_session",
                    lambda region: boto3.Session(region_name=region)))
            uvicorn.run(TraceRequests(app), host="127.0.0.1", port=8444,
                    ssl_keyfile="/tmp/naija-proxy-test.key", ssl_certfile="/tmp/naija-proxy-test.crt")
