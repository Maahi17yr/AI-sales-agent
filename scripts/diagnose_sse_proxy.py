"""Isolated A/B diagnostic: time first SSE chunk directly vs Next rewrite.

Mock agent only. This script never sends live model or payment requests.
"""
import json
import statistics
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


def median_runs(url: str, repeats: int) -> dict:
    records = [measure(url) for _ in range(repeats)]
    return {
        "first_chunk_ms": round(statistics.median(
            record["first_chunk_ms"] for record in records), 1),
        "complete_ms": round(statistics.median(
            record["complete_ms"] for record in records), 1),
        "http_chunks": min(record["http_chunks"] for record in records),
    }


def main():
    backend = "http://127.0.0.1:8000/agent"
    frontend = "http://127.0.0.1:3000/api/backend/agent"
    # The dynamic Next.js endpoint has a one-time development compilation
    # cost; record it but compare warmed streams with the same live backend.
    cold_proxy = measure(frontend)
    direct = median_runs(backend, 3)
    proxy = median_runs(frontend, 3)
    print("SSE_PROXY_DIAGNOSTIC=" +
          json.dumps({"direct": direct, "proxy": proxy,
                      "cold_proxy": cold_proxy}, sort_keys=True),
          flush=True)
    assert direct["http_chunks"] > 20, "Mock backend not streaming"
    assert proxy["http_chunks"] > 20, (
        "Next.js buffered AG-UI response instead of forwarding SSE chunks")
    assert proxy["first_chunk_ms"] < min(350, direct["complete_ms"] * 0.25), (
        "Warmed Next proxy delayed first SSE chunk excessively")
    assert proxy["complete_ms"] <= direct["complete_ms"] * 1.35, (
        "Streaming proxy added significant total completion latency")


if __name__ == "__main__":
    main()
