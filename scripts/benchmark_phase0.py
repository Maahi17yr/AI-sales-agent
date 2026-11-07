"""Synthetic baseline for adapter/provider instrumentation; NO live network or model.

Run: python -m scripts.benchmark_phase0 --iterations 30 --warmup 5
Prints only aggregate timing statistics and error counts, no SQL, credentials or content.
"""
import argparse
import os
import sqlite3
import statistics
import time
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

import httpx

from core.observability.metrics import TimedSQLiteConnection
from core.services import paystack_service


def percentile(samples, percent):
    """Nearest-rank sample percentile; predictable for repeatable local tests."""
    if not samples:
        raise ValueError("Samples required")
    ordered = sorted(samples)
    return ordered[max(0, (len(ordered) * percent + 99) // 100 - 1)]


def run(iterations=30, warmup=5):
    if iterations <= 0 or warmup < 0:
        raise ValueError("Iterations must be positive and warmup nonnegative")
    if os.getenv("DATABASE_URL", "").strip():
        raise ValueError("This synthetic benchmark refuses any configured DATABASE_URL.")
    sql_times, pay_times, failures = [], [], 0

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": True, "data": {"ok": True}}

    def fake_request(self, method, url, **kwargs):
        return FakeResponse()

    conn = TimedSQLiteConnection(sqlite3.connect(":memory:"))
    try:
        conn.execute("CREATE TABLE fixture(value INTEGER)")
        conn.execute("INSERT INTO fixture(value) VALUES (1)")
        with patch.object(paystack_service, "_key", return_value="fictional-test-key"), \
             patch.object(httpx.Client, "request", new=fake_request):
            for i in range(warmup + iterations):
                start = time.perf_counter()
                value = conn.execute("SELECT value FROM fixture").fetchone()[0]
                elapsed_sql = (time.perf_counter() - start) * 1000
                start = time.perf_counter()
                response = paystack_service._request("GET", "/transaction/verify/fictional")
                elapsed_pay = (time.perf_counter() - start) * 1000
                if value != 1 or not response["ok"]:
                    failures += 1
                if i >= warmup:
                    sql_times.append(elapsed_sql)
                    pay_times.append(elapsed_pay)
    finally:
        conn.close()
    return {
        "samples": iterations, "warmup": warmup, "errors": failures,
        "sqlite_p50_ms": round(percentile(sql_times, 50), 3),
        "sqlite_p95_ms": round(percentile(sql_times, 95), 3),
        "mock_paystack_p50_ms": round(percentile(pay_times, 50), 3),
        "mock_paystack_p95_ms": round(percentile(pay_times, 95), 3),
        "environment": "synthetic-local-no-network",
    }


def main():
    parser = argparse.ArgumentParser(description="Local-only synthetic benchmark")
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=5)
    args = parser.parse_args()
    result = run(args.iterations, args.warmup)
    for key, value in result.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
