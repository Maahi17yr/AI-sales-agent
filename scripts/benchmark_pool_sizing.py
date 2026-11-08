"""Actual wall-clock concurrency and pool-sizing validation; disposable PG only.

Controlled client arrival times with realistic thread scheduling and a deliberately
held SQL connection for 40ms, making saturation visible. No user/provider data.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import platform
import resource
import statistics
import time

from data.database import models, pool_experiment


def percentile(values, pct):
    ordered = sorted(values)
    return round(ordered[max(0, (len(ordered) * pct + 99) // 100 - 1)], 3)


def run_load(n, count, rps, operation):
    """Measure response duration AND elapsed elapsed wall time including arrivals."""
    start_wall = time.perf_counter()
    cpu_start = time.process_time()
    rss_start = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    def work(scheduled):
        actual = time.perf_counter()
        try:
            operation()
            return (time.perf_counter() - actual) * 1000, True, scheduled
        except Exception:
            return (time.perf_counter() - actual) * 1000, False, scheduled

    with ThreadPoolExecutor(max_workers=n) as executor:
        futures = []
        for i in range(count):
            scheduled = start_wall + i / rps
            delay = scheduled - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            futures.append(executor.submit(work, scheduled))
        values = [f.result() for f in futures]
    elapsed = time.perf_counter() - start_wall
    durations = [entry[0] for entry in values]
    success = sum(1 for entry in values if entry[1])
    return {
        "attempted": count, "completed": success, "errors": count - success,
        "p50_ms": percentile(durations, 50),
        "p95_ms": percentile(durations, 95), "p99_ms": percentile(durations, 99),
        "elapsed_s": round(elapsed, 3),
        "actual_completed_rps": round(success / elapsed, 2),
        "scheduled_arrival_rps": rps,
        "process_cpu_s": round(time.process_time() - cpu_start, 3),
        "peak_process_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "rss_at_start_kib": rss_start,
    }


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--requests", type=int, default=60)
    args.add_argument("--arrival-rps", type=float, default=300)
    config = args.parse_args()
    pool_experiment.guard()
    if not 50 <= config.requests <= 400 or not 10 <= config.arrival_rps <= 1000:
        raise ValueError("Controlled benchmark must use 50-400 requests, 10-1000 arrivals/s.")
    models.create_tables()
    database = models.get_connection()
    try:
        limit = int(database.execute("SHOW max_connections").fetchone()[0])
    finally:
        database.close()

    # No SQL mutation in the measured operation; forcing 40ms occupancy exposes
    # bounded-pool saturation instead of hiding it behind microsecond queries.
    def held_select():
        with models.get_connection() as conn:
            conn.execute("SELECT pg_sleep(?)", (0.04,)).fetchone()

    profiles = ((1, 3), (2, 5), (2, 10), (5, 15))
    for mn, mx in profiles:
        if mx + 5 > limit:
            print("SIZE_SKIP " + json.dumps({"min": mn, "max": mx, "reason": "database_connection_limit"}))
            continue
        pool_experiment.open_pool(mn, mx, timeout=0.2)
        os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "1"
        try:
            for count in (1, 5, 10, 20, 50):
                # Warmups are outside measured operations and wall-clock throughput.
                for _ in range(5):
                    held_select()
                before = pool_experiment.snapshot()
                stats = run_load(count, config.requests, config.arrival_rps, held_select)
                after = pool_experiment.snapshot()
                stats.update({
                    "min": mn, "max": mx, "concurrency": count,
                    "pool_borrow_ms_total": round(after["wait_ms"] - before["wait_ms"], 3),
                    "pool_timeout_count": after["timeouts"] - before["timeouts"],
                    "reset_ms_total": round(after["reset_ms"] - before["reset_ms"], 3),
                    "reset_count": after["reset_count"] - before["reset_count"],
                    "physical_connections_new": after["new_physical"] - before["new_physical"],
                    "peak_active_connections": after["peak_active"],
                    "pool_available": after.get("pool", {}).get("pool_available"),
                    "pool_size_end": after.get("pool", {}).get("pool_size"),
                    "db_max_connections": limit,
                })
                print("POOL_SIZE " + json.dumps(stats, sort_keys=True), flush=True)
        finally:
            os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "0"
            pool_experiment.close_pool()

    print("SIZE_ENV " + json.dumps({
        "python": platform.python_version(), "postgres_major": 16,
        "fixture": "constant SELECT pg_sleep(0.04), no mutation",
        "arrivals": "fixed interval on same CI runner",
        "latency": "per-request execution including connection wait; not per batch",
        "throughput": "successful completions / real wall seconds",
        "raw_tracing": "off", "database": "disposable",
    }, sort_keys=True))


if __name__ == "__main__":
    main()
