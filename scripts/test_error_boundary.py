"""Privacy-safe request error regression tests (no providers or external DB)."""
import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from core.api.server import app


class ErrorBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 54321)),
            base_url="http://127.0.0.1:8000",
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_validation_does_not_echo_password(self):
        response = await self.client.post(
            "/auth/register",
            json={"name": "Test", "email": "test@example.test",
                  "password": {"secret": "NEVER_ECHO_ME"}},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")
        self.assertNotIn("NEVER_ECHO_ME", response.text)
        self.assertTrue(response.headers["x-request-id"])

    async def test_auth_status_preserved_and_correlated(self):
        response = await self.client.get("/auth/me", headers={"x-request-id": "test_request_123"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Authentication required.")
        self.assertEqual(response.headers["x-request-id"], "test_request_123")
        self.assertEqual(response.json()["request_id"], "test_request_123")

    async def test_invalid_request_id_is_replaced(self):
        response = await self.client.get("/auth/me", headers={"x-request-id": "secret\nheader"})
        self.assertNotEqual(response.headers["x-request-id"], "secret\nheader")
        self.assertEqual(len(response.headers["x-request-id"]), 32)

    async def test_unhandled_failure_hides_internal_exception(self):
        from core.api import server
        with patch.object(server.auth_service, "authenticate_token",
                          side_effect=RuntimeError("DATABASE_PASSWORD_DO_NOT_EXPOSE")):
            response = await self.client.get("/auth/me")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["code"], "internal_error")
        self.assertNotIn("DATABASE_PASSWORD_DO_NOT_EXPOSE", response.text)


if __name__ == "__main__":
    unittest.main()
