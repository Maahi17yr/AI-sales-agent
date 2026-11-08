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


    async def test_malformed_signed_webhook_is_safe_400(self):
        from core.api import server
        with patch.object(server.paystack_service, "verify_webhook_signature", return_value=True):
            response = await self.client.post("/payments/webhook", content=b"{bad json",
                                              headers={"x-paystack-signature": "fake"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Invalid webhook payload.")

    async def test_paystack_failure_does_not_expose_secret(self):
        from core.services import paystack_service
        def failure(*args, **kwargs):
            raise httpx.ConnectError("sk_test_SECRET_DO_NOT_EXPOSE")
        with patch.object(httpx.Client, "request", side_effect=failure):
            with self.assertRaisesRegex(ValueError, "temporarily unavailable") as caught:
                paystack_service._request("GET", "/transaction/verify/fake")
        self.assertNotIn("sk_test_SECRET_DO_NOT_EXPOSE", str(caught.exception))

if __name__ == "__main__":
    unittest.main()
