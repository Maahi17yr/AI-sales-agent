"""Offline tests: the real-backend A/B experiment cannot target production."""
import os
import unittest
from unittest.mock import patch

from scripts import real_backend_ab_pool as pool
from data.database.postgres import PostgresConnection


class DummyConn:
    closed = False

    def __init__(self):
        self.statements = []
        self.close_calls = 0

    def execute(self, statement, params=None):
        self.statements.append(statement)
        return self

    def close(self):
        self.closed = True
        self.close_calls += 1


class RealBackendABSafetyTests(unittest.TestCase):
    def config(self, mode="fresh"):
        return {
            "NAIJA_AB_LAB": "1", "NAIJA_AB_POOL_MODE": mode,
            "NAIJA_BITES_ENV": "demo", "OBSERVABILITY_MODE": "off",
            "DATABASE_URL": (
                "postgresql://lab_user:fake_password@" + pool.HOST
                + "/" + pool.DATABASES[mode] + "?sslmode=require"
            ),
            "NAIJA_BITES_FRONTEND_ORIGIN": pool.SERVICE_URLS[mode],
            "PAYSTACK_CALLBACK_URL": pool.SERVICE_URLS[mode] + "/payments/callback",
            "PAYSTACK_SECRET_KEY": "sk_test_disabled_real_ab_lab",
            "BEDROCK_MODEL_ID": "disabled-for-real-ab-lab",
            "NAIJA_AB_LAB_KEY": "synthetic-lab-key-for-ci-only-123",
        }

    def test_only_exact_disposable_databases_allowed(self):
        for mode in ("fresh", "pooled"):
            with self.subTest(mode=mode), patch.dict(os.environ, self.config(mode), clear=True):
                self.assertEqual(pool.guard(), mode)

    def test_rejects_production_or_other_neon_hosts(self):
        for mode in ("fresh", "pooled"):
            cfg = self.config(mode)
            for url in (
                cfg["DATABASE_URL"].replace(pool.HOST, "some-other.neon.tech"),
                cfg["DATABASE_URL"].replace(pool.DATABASES[mode], "neondb"),
                cfg["DATABASE_URL"].replace("sslmode=require", "sslmode=disable"),
                cfg["DATABASE_URL"].replace(pool.HOST, pool.HOST.replace(".c-", "-pooler.c-")),
            ):
                with self.subTest(mode=mode, url=url.split("@")[-1]), patch.dict(os.environ, {**cfg, "DATABASE_URL": url}, clear=True):
                    with self.assertRaises(RuntimeError):
                        pool.guard()

    def test_rejects_real_external_credentials(self):
        cfg = self.config()
        with patch.dict(os.environ, {**cfg, "AWS_ACCESS_KEY_ID": "dummy"}, clear=True):
            with self.assertRaises(RuntimeError):
                pool.guard()
        with patch.dict(os.environ, {**cfg, "PAYSTACK_SECRET_KEY": "sk_test_real"}, clear=True):
            with self.assertRaises(RuntimeError):
                pool.guard()

    def test_fresh_adapter_closes_once(self):
        fake = DummyConn()
        with patch.dict(os.environ, {"NAIJA_AB_LAB": "0"}, clear=True):
            with patch("data.database.postgres.psycopg.connect", return_value=fake):
                with PostgresConnection("ignored") as conn:
                    conn.execute("SELECT 1")
                conn.close()
        self.assertEqual(fake.close_calls, 1)
        self.assertEqual(fake.statements, ["BEGIN", "SELECT 1", "COMMIT"])

    def test_pooled_adapter_returns_lease_once(self):
        fake = DummyConn()
        with patch.dict(os.environ, {"NAIJA_AB_LAB": "1", "NAIJA_AB_POOL_MODE": "pooled"}, clear=True):
            with patch("scripts.real_backend_ab_pool.borrow", return_value=fake), patch("scripts.real_backend_ab_pool.release") as release:
                with PostgresConnection("ignored") as conn:
                    conn.execute("SELECT 1")
                conn.close()
                release.assert_called_once_with(fake)
        self.assertEqual(fake.close_calls, 0)
        self.assertEqual(fake.statements, ["BEGIN", "SELECT 1", "COMMIT"])


if __name__ == "__main__":
    unittest.main()
