"""Disposable PostgreSQL pool isolation, error handling and idempotency tests."""
import concurrent.futures
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from psycopg_pool import PoolTimeout

from core.services import auth_service
from data.database import models, pool_experiment
from data.database.postgres import PostgresConnection


class ExperimentalPoolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if (os.getenv("NAIJA_BITES_POOL_EXPERIMENT") != "1" or
                os.getenv("DATABASE_URL") != pool_experiment._ALLOWED_URL):
            raise unittest.SkipTest("Requires isolated PostgreSQL pooling database")
        models.create_tables()

    def setUp(self):
        self.assertFalse(pool_experiment.pool_is_open())
        pool_experiment.open_pool(min_size=1, max_size=3, timeout=0.2)
        os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "1"

    def tearDown(self):
        os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "0"
        pool_experiment.close_pool()

    def test_disabled_by_default_and_guard_rejects_other_database(self):
        os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "0"
        with models.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT 1 AS x").fetchone()["x"], 1)
            self.assertFalse(conn._experimental_pooled)
        os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "1"
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://not-the-test-db"}):
            with self.assertRaises(RuntimeError):
                PostgresConnection("postgresql://not-the-test-db")

    def test_rollback_after_error_and_no_cross_session_leak(self):
        with models.get_connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS pool_regression (id TEXT PRIMARY KEY, value TEXT)")
        key = "fictional-" + uuid4().hex
        with self.assertRaises(ValueError):
            with models.get_connection() as conn:
                conn.execute("INSERT INTO pool_regression(id,value) VALUES(?,?)", (key,"not-committed"))
                raise ValueError("revert")
        with models.get_connection() as conn:
            self.assertIsNone(conn.execute("SELECT value FROM pool_regression WHERE id=?", (key,)).fetchone())
        with models.get_connection() as conn:
            conn.execute("SELECT set_config('application_name','fictional_customer_session',false)")
        with models.get_connection() as conn:
            name = conn.execute("SELECT current_setting('application_name') AS n").fetchone()["n"]
            self.assertNotEqual(name, "fictional_customer_session")

    def test_close_without_context_rollback_then_reborrow(self):
        with models.get_connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS pool_rollback (id TEXT PRIMARY KEY)")
        key = "fictional-" + uuid4().hex
        conn = models.get_connection()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO pool_rollback(id) VALUES(?)", (key,))
        conn.close()
        with models.get_connection() as verify:
            self.assertIsNone(verify.execute("SELECT id FROM pool_rollback WHERE id=?", (key,)).fetchone())

    def test_bounded_pool_exhaustion_and_recovery(self):
        pool_experiment.close_pool()
        pool_experiment.open_pool(min_size=1, max_size=1, timeout=0.15)
        conn = models.get_connection()
        try:
            with self.assertRaises(PoolTimeout):
                models.get_connection()
            self.assertGreaterEqual(pool_experiment.snapshot()["timeouts"], 1)
        finally:
            conn.close()
        with models.get_connection() as recovered:
            self.assertEqual(recovered.execute("SELECT 1 AS n").fetchone()["n"], 1)

    def test_concurrent_customer_authentication_isolated(self):
        customers = []
        for i in range(5):
            email = "fictional-pool-" + uuid4().hex + "@example.test"
            account = auth_service.register_customer("Fictional Customer", email,
                                                     "Fictional-Password-12345")
            credentials = auth_service.login_customer(email, "Fictional-Password-12345")
            customers.append((account["id"], credentials["session_token"]))
        def work(pair):
            cid, token = pair
            return auth_service.authenticate_token(token)["id"] == cid
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            self.assertTrue(all(executor.map(work, customers)))

    def test_pool_shutdown_is_idempotent(self):
        self.assertTrue(pool_experiment.pool_is_open())
        pool_experiment.close_pool()
        pool_experiment.close_pool()
        self.assertFalse(pool_experiment.pool_is_open())
        with self.assertRaises(RuntimeError):
            models.get_connection()


if __name__ == "__main__":
    unittest.main()
