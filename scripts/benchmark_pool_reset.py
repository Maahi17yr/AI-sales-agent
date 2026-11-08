"""Safe reset-policy cost comparisons on a disposable standalone connection.

A cheaper RESET ALL is *not* adopted: prove it leaves temporary tables behind.
"""
import json
import os
import platform
import time

import psycopg
from data.database import pool_experiment
from scripts.benchmark_pool_sizing import percentile


def main():
    pool_experiment.guard()
    # Standalone connection prevents reset experiments from weakening real pool
    # cleanup; all settings are on disposable localhost Postgres.
    conn = psycopg.connect(pool_experiment._ALLOWED_URL,
                           autocommit=True, prepare_threshold=None)
    try:
        results = {"discard_all": [], "reset_all": [], "rollback": []}
        for rep in range(3):
            for iteration in range(35):
                # Counterbalance order and apply the same session state to both.
                order = ("discard_all", "reset_all") if (rep + iteration) % 2 == 0 else ("reset_all", "discard_all")
                for name in order:
                    conn.execute("SELECT set_config('application_name', 'fictional-reset-bench', false)")
                    start = time.perf_counter()
                    conn.execute("DISCARD ALL" if name == "discard_all" else "RESET ALL")
                    elapsed = (time.perf_counter() - start) * 1000
                    if iteration >= 5:
                        results[name].append(elapsed)
                conn.execute("BEGIN")
                conn.execute("SELECT 1")
                start = time.perf_counter()
                conn.rollback()
                elapsed = (time.perf_counter() - start) * 1000
                if iteration >= 5:
                    results["rollback"].append(elapsed)

        # Security equivalence proof: RESET ALL does not clear temp table data.
        conn.execute("CREATE TEMP TABLE test_reset_leak (value INTEGER)")
        conn.execute("INSERT INTO test_reset_leak VALUES (42)")
        conn.execute("RESET ALL")
        unsafe = conn.execute("SELECT value FROM test_reset_leak").fetchone()[0] == 42
        conn.execute("DISCARD ALL")
        cleared = conn.execute("SELECT to_regclass('pg_temp.test_reset_leak')").fetchone()[0] is None
        if not unsafe or not cleared:
            raise AssertionError("Reset policy security assumptions no longer valid")
        for name, times in results.items():
            print("RESET_COST " + json.dumps({
                "operation": name, "samples": len(times), "warmups_per_repeat": 5,
                "repetitions": 3, "p50_ms": percentile(times, 50),
                "p95_ms": percentile(times, 95), "p99_ms": percentile(times, 99),
            }, sort_keys=True), flush=True)
        print("RESET_POLICY " + json.dumps({
            "reset_all_leaves_temp_data": unsafe,
            "discard_all_clears_temp_table": cleared,
            "decision": "keep DISCARD ALL; RESET ALL unsafe as a drop-in replacement",
            "python": platform.python_version(),
            "database": "disposable PG16",
        }, sort_keys=True))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
