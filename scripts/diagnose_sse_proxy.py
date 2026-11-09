"""Isolated A/B diagnostic: time first SSE chunk directly vs Next rewrite.

Mock agent only. This script never sends live model or payment requests.
"""
import json
import time
import httpx

PAYLOAD = {
    "threadId": "00000000-0000-4000-8000-000000000001",
    "runId": "00000000-0000-4000-8000-000000000002",
    "parentRunId": None, "state": {},
    "messages": [{"id": "sample-1", "role": "user",
                  "content": "benchmark proxy streaming"}],
    "tools": [], "context": [], "forwardedProps": {},
}


def measure(url: str) -> dict:
    started = time.perf_counter()
    first = None
    count = 0
    with httpx.Client(timeout=30) as client:
        with client.stream("POST", url,
                           headers={"Accept": "text/event-stream"},
                           json=PAYLOAD) as response:
            response.raise_for_status()
            for block in response.iter_bytes():
                if not block:
                    continue
                count += 1
                if first is None:
                    first = 1000 * (time.perf_counter() - started)
    return {
        "first_chunk_ms": round(first or 0, 1),
        "complete_ms": round(1000 * (time.perf_counter() - started), 1),
        "http_chunks": count,
    }


def main():
    direct = measure("http://127.0.0.1:8000/agent")
    proxy = measure("http://127.0.0.1:3000/api/backend/agent")
    print("SSE_PROXY_DIAGNOSTIC=" +
          json.dumps({"direct": direct, "proxy": proxy}, sort_keys=True),
          flush=True)
    assert direct["http_chunks"] > 1, "Mock backend not streaming as expected"


if __name__ == "__main__":
    main()
