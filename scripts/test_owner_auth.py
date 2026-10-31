"""Isolated Phase 1 owner authentication and promotion regression tests."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["NAIJA_BITES_ENV"] = "development"
os.environ["DATABASE_URL"] = ""

import httpx
from core.api.server import app
from core.services.owner_access_service import promote_registered_customer
from data.database import models

PASSWORD = "Fictional-Password-1234"
EMAIL = "fictional.owner@example.test"


class OwnerAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(models, "DB_PATH", Path(self.temp.name) / "test.db")
        self.patch.start()
        models.create_tables()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 54321)),
            base_url="http://127.0.0.1:8000",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.patch.stop()
        self.temp.cleanup()

    async def register(self):
        return await self.client.post("/auth/register", json={
            "name": "Test Owner", "email": EMAIL, "password": PASSWORD,
        })

    async def login(self):
        return await self.client.post("/auth/login", json={
            "email": EMAIL, "password": PASSWORD,
        })

    async def test_shared_login_and_owner_protection(self):
        self.assertEqual((await self.client.get("/owner/me")).status_code, 401)
        registered = await self.register()
        self.assertEqual(registered.status_code, 201)
        self.assertEqual(registered.json()["customer"]["role"], "customer")
        logged_in = await self.login()
        self.assertEqual(logged_in.json()["customer"]["role"], "customer")
        self.assertEqual((await self.client.get("/owner/me")).status_code, 403)
        self.assertEqual((await self.client.get("/auth/me")).json()["customer"]["role"], "customer")

        promoted = promote_registered_customer(EMAIL)
        self.assertEqual(promoted["role"], "owner")
        # Previous cookie is revoked immediately.
        self.assertEqual((await self.client.get("/auth/me")).status_code, 401)
        self.assertEqual((await self.client.get("/owner/me")).status_code, 401)
        logged_in = await self.login()
        self.assertEqual(logged_in.json()["customer"]["role"], "owner")
        self.assertEqual((await self.client.get("/owner/me")).json()["owner"]["email"], EMAIL)
        with models.get_connection() as conn:
            logs = conn.execute("SELECT action FROM owner_audit_log").fetchall()
            self.assertEqual([row["action"] for row in logs], ["promote_to_owner"])
        with self.assertRaises(ValueError):
            promote_registered_customer(EMAIL)

    async def test_registration_cannot_choose_owner_role(self):
        response = await self.client.post("/auth/register", json={
            "name": "Test Owner", "email": EMAIL, "password": PASSWORD, "role": "owner",
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["customer"]["role"], "customer")
        self.assertEqual((await self.login()).json()["customer"]["role"], "customer")

    async def test_migration_keeps_existing_customers(self):
        # Simulate a database created before role support.
        with models.get_connection() as conn:
            conn.execute("DROP TABLE owner_audit_log")
            conn.execute("DROP TABLE auth_sessions")
            conn.execute("DROP TABLE customer_chats")
            conn.execute("DROP TABLE conversation_sessions")
            conn.execute("DROP TABLE orders") if False else None
            # SQLite cannot drop a referenced customers table while other tables exist.
            # Rebuild the legacy customers schema in a standalone temporary database.
        legacy_path = Path(self.temp.name) / "legacy.db"
        import sqlite3
        with sqlite3.connect(legacy_path) as legacy:
            legacy.execute("""CREATE TABLE customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT,
                email TEXT UNIQUE COLLATE NOCASE, password_hash TEXT,
                phone TEXT UNIQUE, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
            legacy.execute("INSERT INTO customers(name,email,password_hash) VALUES(?,?,?)",
                           ("Existing", "existing@example.test", "hash"))
        with patch.object(models, "DB_PATH", legacy_path):
            models.create_tables()
            models.create_tables()  # Migration must be repeatable.
            with models.get_connection() as conn:
                row = conn.execute("SELECT email, role FROM customers").fetchone()
                self.assertEqual((row["email"], row["role"]), ("existing@example.test", "customer"))


if __name__ == "__main__":
    unittest.main()
