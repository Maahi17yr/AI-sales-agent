"""Isolated FastAPI authentication integration checks (no Bedrock or Paystack).

Run from repository root:
    python -m unittest scripts.test_auth_api -v

All data is stored in a disposable SQLite database. No live credentials are used.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Force development and disable remote database access BEFORE importing the API.
os.environ["NAIJA_BITES_ENV"] = "development"
os.environ["DATABASE_URL"] = ""

import httpx
from data.database import models
from application.api.server import app

TEST_PASSWORD = "FictionalOnly-Password-1234"
TEST_EMAIL = "test.customer@example.test"


class AuthenticationAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(models, "DB_PATH", Path(self.temp_dir.name) / "auth_test.db")
        self.db_patch.start()
        models.create_tables()
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 54321))
        self.client = httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    async def register(self, email=TEST_EMAIL, password=TEST_PASSWORD):
        return await self.client.post("/auth/register", json={
            "name": "Fictional Customer", "email": email, "password": password
        })

    async def login(self, email=TEST_EMAIL, password=TEST_PASSWORD):
        return await self.client.post("/auth/login", json={
            "email": email, "password": password
        })

    async def test_register_login_session_refresh_and_logout(self):
        self.assertEqual((await self.client.get("/auth/me")).status_code, 401)
        registered = await self.register()
        self.assertEqual(registered.status_code, 201, registered.text)
        self.assertNotIn("password_hash", registered.text)

        login = await self.login()
        self.assertEqual(login.status_code, 200, login.text)
        cookie = login.headers.get("set-cookie", "").lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        self.assertEqual((await self.client.get("/auth/me")).status_code, 200)
        self.assertEqual((await self.client.get("/auth/me")).json()["customer"]["email"], TEST_EMAIL)

        logout = await self.client.post("/auth/logout")
        self.assertEqual(logout.status_code, 200)
        self.assertEqual((await self.client.get("/auth/me")).status_code, 401)

    async def test_wrong_password_rejected(self):
        await self.register()
        result = await self.login(password="WrongPassword-1234")
        self.assertEqual(result.status_code, 401)
        self.assertEqual((await self.client.get("/auth/me")).status_code, 401)

    async def test_duplicate_registration_rejected(self):
        self.assertEqual((await self.register()).status_code, 201)
        self.assertEqual((await self.register(email="TEST.CUSTOMER@example.test")).status_code, 400)

    async def test_short_password_rejected(self):
        self.assertEqual((await self.register(password="short")).status_code, 400)

    async def test_expired_session_rejected(self):
        await self.register()
        self.assertEqual((await self.login()).status_code, 200)
        conn = models.get_connection()
        try:
            conn.execute("UPDATE auth_sessions SET expires_at = ?", ("2000-01-01T00:00:00+00:00",))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual((await self.client.get("/auth/me")).status_code, 401)

    async def test_protected_checkout_requires_login(self):
        self.assertEqual((await self.client.get("/checkout")).status_code, 401)

    async def test_unknown_origin_rejected(self):
        result = await self.client.post(
            "/auth/register",
            headers={"Origin": "https://untrusted.example"},
            json={"name": "Fictional Customer", "email": TEST_EMAIL, "password": TEST_PASSWORD},
        )
        self.assertEqual(result.status_code, 403)


if __name__ == "__main__":
    unittest.main()
