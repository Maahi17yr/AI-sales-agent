"""Run the real agent against disposable SQLite and a deterministic ConverseStream fixture."""
import os
import json
import time
from unittest.mock import patch

import uvicorn
from botocore.client import BaseClient

# Import enforces the existing /tmp, development, no DATABASE_URL and mock-provider guards.
from scripts import mock_payment_backend
from core.api.server import app


def provider_call(client, operation, params):
    if client.meta.service_model.service_name != "bedrock-runtime" or operation != "ConverseStream":
        raise RuntimeError("Unexpected provider operation in isolated benchmark")
    messages = params["messages"]
    prompts = [item["text"] for message in messages if message["role"] == "user"
               for item in message["content"] if "text" in item]
    prompt = prompts[-1].lower() if prompts else ""
    tool_result = any("toolResult" in item for item in messages[-1]["content"])
    menu = "browse_menu" in prompt and not tool_result
    def events():
        yield {"messageStart": {"role": "assistant"}}
        if menu:
            yield {"contentBlockStart": {"contentBlockIndex": 0, "start": {
                "toolUse": {"toolUseId": "fixture-menu", "name": "browse_menu"}}}}
            yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {
                "toolUse": {"input": json.dumps({})}}}}
        else:
            count = 100 if "long response" in prompt else 10
            for _ in range(count):
                time.sleep(.008)
                yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "bite "}}}
        yield {"contentBlockStop": {"contentBlockIndex": 0}}
        yield {"messageStop": {"stopReason": "tool_use" if menu else "end_turn"}}
        yield {"metadata": {"usage": {"inputTokens": 20, "outputTokens": 15, "totalTokens": 35},
                            "metrics": {"latencyMs": 80}}}
    return {"stream": events()}


if __name__ == "__main__":
    os.environ.update(AWS_ACCESS_KEY_ID="fixture", AWS_SECRET_ACCESS_KEY="fixture",
                      AWS_DEFAULT_REGION="us-east-1", BEDROCK_MODEL_ID="fixture")
    with patch.object(BaseClient, "_make_api_call", provider_call):
        uvicorn.run(app, host="127.0.0.1", port=8444,
                    ssl_keyfile="/tmp/naija-proxy-test.key", ssl_certfile="/tmp/naija-proxy-test.crt")
