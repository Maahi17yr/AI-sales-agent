"""Local API authentication integration tests (no AI model calls)."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from database import models
from agui_server import app


class AuthenticationAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "auth-api.sqlite3"
        patcher = patch.object(models, "DB_PATH", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        with closing(models.get_connection()) as conn, conn:
            conn.execute("""CREATE TABLE customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT, email TEXT UNIQUE COLLATE NOCASE,
                password_hash TEXT, phone TEXT UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            conn.execute("""CREATE TABLE auth_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE CASCADE
            )""")
        # No lifespan context: tests only the auth endpoints, not the Bedrock agent.
        self.client = TestClient(app, client=("127.0.0.1", 12345))
        self.addCleanup(self.client.close)

    def test_registration_login_me_logout(self):
        payload = {
            "name": "Alice", "email": "Alice@example.com",
            "password": "a-long-test-password",
        }
        registered = self.client.post("/auth/register", json=payload)
        self.assertEqual(registered.status_code, 201, registered.text)
        self.assertEqual(registered.json()["customer"]["email"], "alice@example.com")
        self.assertNotIn("password_hash", registered.text)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)
        login = self.client.post(
            "/auth/login",
            json={"email": payload["email"], "password": payload["password"]},
        )
        self.assertEqual(login.status_code, 200, login.text)
        self.assertNotIn("session_token", login.text)
        self.assertIn("httponly", login.headers["set-cookie"].lower())
        self.assertIn("samesite=lax", login.headers["set-cookie"].lower())
        self.assertEqual(self.client.get("/auth/me").json()["customer"]["id"],
                         registered.json()["customer"]["id"])
        self.assertEqual(self.client.post("/auth/logout").status_code, 200)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_invalid_login_and_duplicate_registration(self):
        user = {"name": "Bob", "email": "bob@example.com",
                "password": "another-test-password"}
        self.assertEqual(self.client.post("/auth/register", json=user).status_code, 201)
        self.assertEqual(self.client.post("/auth/register", json=user).status_code, 400)
        bad = self.client.post("/auth/login", json={
            "email": user["email"], "password": "incorrect"
        })
        self.assertEqual(bad.status_code, 401)
        self.assertNotIn("set-cookie", bad.headers)

    def test_untrusted_origin_rejected(self):
        response = self.client.post(
            "/auth/register",
            json={"name": "Eve", "email": "eve@example.com",
                  "password": "another-test-password"},
            headers={"Origin": "https://untrusted.example"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)


if __name__ == "__main__":
    unittest.main()
