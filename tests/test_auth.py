"""Run: python -m unittest discover -s tests -p 'test_auth.py'"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import models
from database.migrate_auth import migrate
from services import auth_service


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "test.sqlite3"
        self.patch_db = patch.object(models, "DB_PATH", self.db)
        self.patch_db.start()
        self.addCleanup(self.patch_db.stop)
        # Emulate legacy customers table to exercise the actual migration.
        with sqlite3.connect(self.db) as conn:
            conn.execute("""CREATE TABLE customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                phone TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            conn.execute("INSERT INTO customers(name,phone) VALUES('Legacy','08000000000')")
            conn.execute("""CREATE TABLE conversation_sessions (
                id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id))""")
            conn.execute("INSERT INTO conversation_sessions(id,customer_id) VALUES(1,1)")
        migrate(self.db)

    def test_registration_login_logout_and_isolation(self):
        a = auth_service.register_customer("Alice", "Alice@example.com", "correct-horse-password")
        b = auth_service.register_customer("Bob", "bob@example.com", "another-long-password")
        self.assertNotEqual(a["id"], b["id"])
        with self.assertRaisesRegex(ValueError, "already registered"):
            auth_service.register_customer("Duplicate", "ALICE@example.com", "another-long-password")
        with self.assertRaisesRegex(ValueError, "Invalid email or password"):
            auth_service.login_customer("alice@example.com", "wrong-password")
        login_a = auth_service.login_customer("alice@example.com", "correct-horse-password")
        login_b = auth_service.login_customer("bob@example.com", "another-long-password")
        self.assertEqual(auth_service.authenticate_token(login_a["session_token"])["id"], a["id"])
        self.assertEqual(auth_service.authenticate_token(login_b["session_token"])["id"], b["id"])
        self.assertTrue(auth_service.logout_customer(login_a["session_token"]))
        self.assertIsNone(auth_service.authenticate_token(login_a["session_token"]))
        self.assertEqual(auth_service.authenticate_token(login_b["session_token"])["id"], b["id"])

    def test_migration_preserves_legacy_customer(self):
        with models.get_connection() as conn:
            old = conn.execute("SELECT id, phone FROM customers WHERE id=1").fetchone()
            ref = conn.execute("SELECT customer_id FROM conversation_sessions WHERE id=1").fetchone()
            self.assertEqual(old["phone"], "08000000000")
            self.assertEqual(ref["customer_id"], old["id"])
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(len(list(self.db.parent.glob("*.backup.sqlite3"))), 1)

    def test_expired_session_rejected(self):
        auth_service.register_customer("Alice", "alice@example.com", "correct-horse-password")
        token = auth_service.login_customer("alice@example.com", "correct-horse-password")["session_token"]
        import hashlib
        with models.get_connection() as conn:
            conn.execute("UPDATE auth_sessions SET expires_at=? WHERE token_hash=?",
                         ("2000-01-01T00:00:00+00:00", hashlib.sha256(token.encode()).hexdigest()))
        self.assertIsNone(auth_service.authenticate_token(token))


if __name__ == "__main__":
    unittest.main()
