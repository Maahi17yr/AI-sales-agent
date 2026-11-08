"""High-risk pooling regression tests using disposable PostgreSQL 16 only.

Inject real deferred-commit failures, server-side cancellations, row lock
timeouts, deadlocks, and pool shutdown while a connection is still leased.
"""
import concurrent.futures
import os
import threading
import unittest
from uuid import uuid4

import psycopg
from data.database import models, pool_experiment


class PoolSafetyValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pool_experiment.guard()
        models.create_tables()

    def setUp(self):
        pool_experiment.open_pool(1, 3, timeout=0.5)
        os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "1"

    def tearDown(self):
        os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "0"
        pool_experiment.close_pool()
        self.assertEqual(pool_experiment.snapshot()["active"], 0, "Leaked borrowed pool connection")

    def test_deferred_commit_error_rolls_back_and_does_not_poison_next_lease(self):
        with models.get_connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS test_pool_deferred "
                         "(key TEXT, value TEXT, CONSTRAINT test_pool_deferred_unique "
                         "UNIQUE(value) DEFERRABLE INITIALLY DEFERRED)")
        token = uuid4().hex
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with models.get_connection() as conn:
                conn.execute("INSERT INTO test_pool_deferred(key,value) VALUES(?,?)", (token+"a", token))
                conn.execute("INSERT INTO test_pool_deferred(key,value) VALUES(?,?)", (token+"b", token))
        with models.get_connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) AS n FROM test_pool_deferred WHERE value=?", (token,)
            ).fetchone()["n"], 0)
            self.assertEqual(conn.execute("SELECT 1 AS n").fetchone()["n"], 1)

    def test_query_cancellation_rolls_back_and_recovers(self):
        ready = threading.Event()
        backend = []
        results = []
        def long_query():
            try:
                with models.get_connection() as conn:
                    backend.append(conn.execute("SELECT pg_backend_pid() AS n").fetchone()["n"])
                    ready.set()
                    conn.execute("SELECT pg_sleep(?)", (3.0,))
            except psycopg.errors.QueryCanceled:
                results.append("cancelled")
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            work = executor.submit(long_query)
            self.assertTrue(ready.wait(2), "Database worker never started")
            with models.get_connection() as c:
                self.assertTrue(c.execute(
                    "SELECT pg_cancel_backend(?) AS ok", (backend[0],)
                ).fetchone()["ok"])
            work.result(timeout=5)
        self.assertEqual(results, ["cancelled"])
        with models.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT 1 AS n").fetchone()["n"], 1)

    def test_row_lock_timeout_does_not_commit_partial_changes(self):
        key = uuid4().hex
        with models.get_connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS pool_lock_test "
                         "(id TEXT PRIMARY KEY, quantity INTEGER NOT NULL)")
            conn.execute("INSERT INTO pool_lock_test(id,quantity) VALUES(?,0)", (key,))
        errors = []
        def contender():
            try:
                with models.get_connection() as conn:
                    conn.execute("SET LOCAL lock_timeout = '120ms'")
                    conn.execute("UPDATE pool_lock_test SET quantity=quantity+1 WHERE id=?", (key,))
            except psycopg.errors.LockNotAvailable:
                errors.append("timed_out")
        with models.get_connection() as holder:
            holder.execute("UPDATE pool_lock_test SET quantity=quantity+1 WHERE id=?", (key,))
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(contender).result(timeout=3)
        self.assertEqual(errors, ["timed_out"])
        with models.get_connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT quantity FROM pool_lock_test WHERE id=?", (key,)
            ).fetchone()["quantity"], 1)

    def test_database_deadlock_rolls_back_only_victim(self):
        key1, key2 = uuid4().hex, uuid4().hex
        with models.get_connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS pool_deadlock_test "
                         "(id TEXT PRIMARY KEY, quantity INTEGER NOT NULL)")
            conn.execute("INSERT INTO pool_deadlock_test(id,quantity) VALUES(?,0)", (key1,))
            conn.execute("INSERT INTO pool_deadlock_test(id,quantity) VALUES(?,0)", (key2,))
        barrier = threading.Barrier(2)
        results = []
        def work(first, second):
            try:
                with models.get_connection() as conn:
                    conn.execute("SET LOCAL statement_timeout = '4000ms'")
                    conn.execute("UPDATE pool_deadlock_test SET quantity=quantity+1 WHERE id=?", (first,))
                    barrier.wait(timeout=3)
                    conn.execute("UPDATE pool_deadlock_test SET quantity=quantity+1 WHERE id=?", (second,))
                results.append("committed")
            except psycopg.errors.DeadlockDetected:
                results.append("deadlocked")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(work, key1, key2), executor.submit(work, key2, key1)]
            for item in futures:
                item.result(timeout=7)
        self.assertCountEqual(results, ["committed", "deadlocked"])
        with models.get_connection() as conn:
            values = [conn.execute("SELECT quantity FROM pool_deadlock_test WHERE id=?", (key,)
                          ).fetchone()["quantity"] for key in (key1, key2)]
        self.assertEqual(values, [1, 1], "Victim's first update must be rolled back")

    def test_shutdown_with_outstanding_lease_cannot_cross_pool_generations(self):
        borrowed = models.get_connection()
        self.assertEqual(pool_experiment.snapshot()["active"], 1)
        pool_experiment.close_pool()
        with self.assertRaises(RuntimeError):
            pool_experiment.open_pool(1, 2, timeout=0.2)
        borrowed.close()
        self.assertEqual(pool_experiment.snapshot()["active"], 0)
        pool_experiment.open_pool(1, 2, timeout=0.2)
        with models.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT 1 AS n").fetchone()["n"], 1)

    def test_backend_disconnect_is_evicted_and_replaced(self):
        borrowed = models.get_connection()
        with models.get_connection() as killer:
            pid = borrowed.execute("SELECT pg_backend_pid() AS n").fetchone()["n"]
            self.assertTrue(killer.execute(
                "SELECT pg_terminate_backend(?) AS ok", (pid,)
            ).fetchone()["ok"])
        try:
            with self.assertRaises(psycopg.OperationalError):
                borrowed.execute("SELECT 1")
        finally:
            borrowed.close()
        with models.get_connection() as fresh:
            self.assertEqual(fresh.execute("SELECT 1 AS n").fetchone()["n"], 1)

    def test_reset_removes_temp_tables_and_session_identity(self):
        marker = "pool_secret_" + uuid4().hex[:9]
        with models.get_connection() as conn:
            conn.execute("SELECT set_config('application_name', ?, false)", (marker,))
            conn.execute("CREATE TEMP TABLE pool_identity_test(v TEXT)")
        with models.get_connection() as conn:
            self.assertNotEqual(conn.execute(
                "SELECT current_setting('application_name') AS n"
            ).fetchone()["n"], marker)
            self.assertIsNone(conn.execute(
                "SELECT to_regclass('pg_temp.pool_identity_test') AS temp"
            ).fetchone()["temp"])


if __name__ == "__main__":
    unittest.main()
