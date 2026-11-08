"""Payment receipt vs order-confirmation API regressions (mocked provider)."""
import unittest
from unittest.mock import patch

import httpx

from core.api import server


class PaymentConfirmationIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app, client=("127.0.0.1", 54321)),
            base_url="http://127.0.0.1:8000",
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_paid_cancelled_order_has_no_false_confirmation(self):
        with patch.object(server, "_customer_session", return_value="fake-session"), \
             patch.object(server.paystack_service, "verify", return_value={
                 "order_id": 42, "payment_status": "paid",
                 "order_status": "cancelled", "reconciliation_required": True,
             }):
            response = await self.client.post("/payments/verify",
                json={"reference": "nb_test_42_mock"})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertTrue(result["reconciliation_required"])
        self.assertNotIn("chat_message", result)
        self.assertNotIn("chat_saved", result)

    async def test_paid_confirmed_order_has_verified_chat_message(self):
        with patch.object(server, "_customer_session", return_value="fake-session"), \
             patch.object(server.paystack_service, "verify", return_value={
                 "order_id": 42, "payment_status": "paid",
                 "order_status": "confirmed", "reconciliation_required": False,
             }):
            response = await self.client.post("/payments/verify",
                json={"reference": "nb_test_42_mock"})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["chat_message"]["id"], "verified-payment-order-42")
        self.assertEqual(result["chat_saved"], False)


if __name__ == "__main__":
    unittest.main()
