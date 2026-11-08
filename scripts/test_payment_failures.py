"""Isolated regression tests for payment transport and signed webhook safety."""
import hashlib
import hmac
import json
import unittest
from unittest.mock import patch

import httpx

from core.api.server import app
from core.services import paystack_service


class PaymentFailureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 54321)),
            base_url="http://127.0.0.1:8000",
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def signed_webhook(self, event):
        body = json.dumps(event).encode()
        signature = hmac.new(b"sk_test_fake_key", body, hashlib.sha512).hexdigest()
        with patch.dict("os.environ", {"PAYSTACK_SECRET_KEY": "sk_test_fake_key"}):
            return await self.client.post(
                "/payments/webhook", content=body,
                headers={"x-paystack-signature": signature},
            )

    async def test_malformed_nested_charge_success_rejected_without_verification(self):
        with patch.object(paystack_service, "verify") as verify:
            for data in (None, [], "invalid", {}, {"reference": 123},
                         {"reference": ""}, {"reference": "  "}):
                with self.subTest(data=data):
                    response = await self.signed_webhook({"event": "charge.success", "data": data})
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertEqual(response.json()["detail"], "Invalid webhook payload.")
            verify.assert_not_called()

    async def test_signed_invalid_json_is_400(self):
        body = b"{invalid"
        signature = hmac.new(b"sk_test_fake_key", body, hashlib.sha512).hexdigest()
        with patch.dict("os.environ", {"PAYSTACK_SECRET_KEY": "sk_test_fake_key"}):
            response = await self.client.post("/payments/webhook", content=body,
                headers={"x-paystack-signature": signature})
        self.assertEqual(response.status_code, 400)

    async def test_replayed_event_calls_idempotent_verifier_without_leaking_details(self):
        event = {"event": "charge.success", "data": {"reference": "nb_test_42_reference"}}
        with patch.object(paystack_service, "verify", return_value={"payment_status": "paid"}) as verify:
            self.assertEqual((await self.signed_webhook(event)).status_code, 200)
            self.assertEqual((await self.signed_webhook(event)).status_code, 200)
            self.assertEqual(verify.call_count, 2)

    async def test_verification_error_safe(self):
        with patch.object(paystack_service, "verify",
                          side_effect=ValueError("sk_test_SECRET_INTERNAL")):
            response = await self.signed_webhook(
                {"event": "charge.success", "data": {"reference": "nb_test_42_reference"}})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("sk_test_SECRET_INTERNAL", response.text)

    def test_provider_errors_are_redacted(self):
        with patch.dict("os.environ", {"PAYSTACK_SECRET_KEY": "sk_test_fake_key"}), \
             patch.object(httpx.Client, "request",
                          side_effect=httpx.ConnectError("sk_test_SECRET_INTERNAL")):
            with self.assertRaisesRegex(ValueError, "temporarily unavailable") as caught:
                paystack_service._request("GET", "/transaction/verify/nb_test_1")
        self.assertNotIn("sk_test_SECRET_INTERNAL", str(caught.exception))

    def test_timeout_requires_state_check_without_automatic_retry(self):
        with patch.dict("os.environ", {"PAYSTACK_SECRET_KEY": "sk_test_fake_key"}), \
             patch.object(httpx.Client, "request",
                          side_effect=httpx.ReadTimeout("sk_test_SECRET_INTERNAL")) as request:
            with self.assertRaisesRegex(ValueError, "check payment status") as caught:
                paystack_service._request("POST", "/transaction/initialize", {"amount": 100})
        self.assertEqual(request.call_count, 1)
        self.assertNotIn("sk_test_SECRET_INTERNAL", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
