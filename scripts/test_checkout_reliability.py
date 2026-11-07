"""Regression tests for checkout events' authoritative state and safe retries.

Run: python -m unittest scripts.test_checkout_reliability -v
No Bedrock, Paystack, production database, or real customer data is used.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import io
from uuid import uuid4, UUID
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["NAIJA_BITES_ENV"] = "development"
os.environ["DATABASE_URL"] = ""

import httpx
from core.api.server import app
from data.database import models


class ReliableCheckoutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(models, "DB_PATH", Path(self.temp.name) / "checkout.db")
        self.db_patch.start()
        models.create_tables()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 54321)),
            base_url="http://127.0.0.1:8000",
        )
        registration = await self.client.post("/auth/register", json={
            "name": "Test Customer", "email": "checkout@example.test",
            "password": "Fictional-Password-1234",
        })
        self.assertEqual(registration.status_code, 201, registration.text)
        login = await self.client.post("/auth/login", json={
            "email": "checkout@example.test", "password": "Fictional-Password-1234",
        })
        self.assertEqual(login.status_code, 200, login.text)
        self.customer_id = (await self.client.get("/auth/me")).json()["customer"]["id"]
        # GET /checkout binds the verified session to the account.
        self.assertFalse((await self.client.get("/checkout")).json()["pending"])
        with models.get_connection() as conn:
            product = conn.execute(
                "INSERT INTO products (name, category, price, available) VALUES (?, ?, ?, ?)",
                ("Fictional Jollof", "rice", 4500, 1),
            )
            self.product_id = product.lastrowid
            conn.execute(
                "INSERT INTO inventory (product_id, variant, quantity) VALUES (?, ?, ?)",
                (self.product_id, "standard", 10),
            )
            conn.execute(
                "INSERT INTO delivery_zones (area, fee, estimated_min_hours, estimated_max_hours, available) VALUES (?, ?, ?, ?, ?)",
                ("Lekki Phase 1", 4500, 1, 3, 1),
            )
            conn.execute(
                "UPDATE conversation_sessions SET cart_data = ? WHERE session_id = ?",
                (json.dumps({"items": [{"product_id": self.product_id, "quantity": 2, "variant": "standard", "modifications": []}]}),
                 f"customer-{self.customer_id}"),
            )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.db_patch.stop()
        self.temp.cleanup()

    async def prepare(self):
        return await self.client.post("/checkout/prepare", json={
            "customer_name": "Test Customer",
            "phone": "08012345678",
            "delivery_address": "Fictional Test Street, Lekki",
            "delivery_area": "Lekki Phase 1",
        })

    async def test_prepared_review_survives_reload_and_confirmation_is_idempotent(self):
        before = (await self.client.get("/checkout")).json()
        self.assertTrue(before["cart_has_items"])
        self.assertFalse(before["pending"])
        prepared = await self.prepare()
        self.assertEqual(prepared.status_code, 200, prepared.text)
        ready = prepared.json()
        self.assertEqual(ready["status"], "ready_for_review")
        self.assertTrue(ready["pending"])
        self.assertEqual(ready["summary"]["total"], 13500)
        self.assertTrue(ready["checkout_id"])
        self.assertEqual((await self.client.get("/checkout")).json()["checkout_id"], ready["checkout_id"])
        retry = await self.prepare()
        self.assertEqual(retry.json()["checkout_id"], ready["checkout_id"])

        approval = {"customer_approved": True, "reviewed_fingerprint": ready["fingerprint"],
                    "checkout_id": ready["checkout_id"]}
        placed = await self.client.post("/checkout/confirm", json=approval)
        self.assertEqual(placed.status_code, 200, placed.text)
        order_id = placed.json()["order_id"]
        repeated = await self.client.post("/checkout/confirm", json=approval)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json()["order_id"], order_id)
        recovered = (await self.client.get("/checkout")).json()
        self.assertEqual(recovered["status"], "payment_pending")
        self.assertEqual(recovered["payment_order_id"], order_id)
        self.assertFalse(recovered["pending"])
        with models.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT quantity FROM inventory WHERE product_id = ?", (self.product_id,)
            ).fetchone()[0], 8)
            conn.execute("UPDATE orders SET status = 'confirmed' WHERE id = ?", (order_id,))
        paid_state = (await self.client.get("/checkout")).json()
        self.assertIsNone(paid_state["payment_order_id"])
        self.assertEqual(paid_state["status"], "paid")
        paid_replay = await self.client.post("/checkout/confirm", json=approval)
        self.assertEqual(paid_replay.status_code, 200, paid_replay.text)
        self.assertEqual(paid_replay.json()["order_id"], order_id)
        self.assertEqual(paid_replay.json()["status"], "confirmed")

    async def test_mocked_successful_paystack_verification_is_idempotent(self):
        """Provider-confirmed payments work on SQLite and can be verified twice."""
        from core.services import paystack_service

        ready = (await self.prepare()).json()
        approval = {
            "customer_approved": True,
            "reviewed_fingerprint": ready["fingerprint"],
            "checkout_id": ready["checkout_id"],
        }
        placed = await self.client.post("/checkout/confirm", json=approval)
        self.assertEqual(placed.status_code, 200, placed.text)
        order_id = placed.json()["order_id"]
        reference = f"nb_test_sqlite_{order_id}"
        with models.get_connection() as conn:
            conn.execute(
                "INSERT INTO payments (order_id, amount, payment_reference, status) "
                "VALUES (?, ?, ?, 'unpaid')",
                (order_id, 13500, reference),
            )
            self.assertEqual(conn.execute(
                "SELECT quantity FROM inventory WHERE product_id = ?", (self.product_id,)
            ).fetchone()[0], 8)
        verified = {
            "reference": reference, "currency": "NGN",
            "amount": 13500 * 100, "status": "success",
        }
        with patch.object(paystack_service, "_request", return_value=verified) as provider:
            first = await self.client.post("/payments/verify", json={"reference": reference})
            again = await self.client.post("/payments/verify", json={"reference": reference})
        self.assertEqual(provider.call_count, 2)
        for response in (first, again):
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["payment_status"], "paid")
            self.assertEqual(response.json()["order_status"], "confirmed")
            self.assertIn("Payment confirmed", response.json()["chat_message"]["content"])
        with models.get_connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM orders WHERE id = ?", (order_id,)
            ).fetchone()["status"], "confirmed")
            payment = conn.execute(
                "SELECT status, paid_at FROM payments WHERE order_id = ?", (order_id,)
            ).fetchone()
            self.assertEqual(payment["status"], "paid")
            self.assertIsNotNone(payment["paid_at"])
            self.assertEqual(conn.execute(
                "SELECT quantity FROM inventory WHERE product_id = ?", (self.product_id,)
            ).fetchone()[0], 8)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 1)

    async def _unpaid_payment_fixture(self):
        """Create a real pending order and fake provider reference, without network."""
        ready = (await self.prepare()).json()
        placed = await self.client.post("/checkout/confirm", json={
            "customer_approved": True, "reviewed_fingerprint": ready["fingerprint"],
            "checkout_id": ready["checkout_id"],
        })
        self.assertEqual(placed.status_code, 200, placed.text)
        order_id = placed.json()["order_id"]
        reference = f"nb_test_regression_{order_id}"
        with models.get_connection() as conn:
            conn.execute(
                "INSERT INTO payments (order_id, amount, payment_reference, status) "
                "VALUES (?, ?, ?, 'unpaid')",
                (order_id, 13500, reference),
            )
        return order_id, reference

    def _assert_payment_and_stock(self, order_id, expected_payment, expected_order):
        with models.get_connection() as conn:
            payment = conn.execute(
                "SELECT status FROM payments WHERE order_id=?", (order_id,)
            ).fetchone()
            order = conn.execute(
                "SELECT status FROM orders WHERE id=?", (order_id,)
            ).fetchone()
            stock = conn.execute(
                "SELECT quantity FROM inventory WHERE product_id=?", (self.product_id,)
            ).fetchone()
            self.assertEqual(payment["status"], expected_payment)
            self.assertEqual(order["status"], expected_order)
            self.assertEqual(stock["quantity"], 8)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 1)

    @staticmethod
    def _provider_success(reference):
        return {"reference": reference, "amount": 13500 * 100,
                "currency": "NGN", "status": "success"}

    async def test_payment_rejects_wrong_amount_currency_and_provider_errors(self):
        from core.services import paystack_service

        order_id, reference = await self._unpaid_payment_fixture()
        expected = self._provider_success(reference)
        for bad in (
            {**expected, "amount": expected["amount"] - 100},
            {**expected, "currency": "USD"},
        ):
            with self.subTest(bad=bad["currency"], amount=bad["amount"]), \
                 patch.object(paystack_service, "_request", return_value=bad):
                response = await self.client.post("/payments/verify", json={"reference": reference})
            self.assertEqual(response.status_code, 400, response.text)
            self._assert_payment_and_stock(order_id, "unpaid", "pending_payment")

        with patch.object(paystack_service, "_request",
                          return_value={**expected, "status": "failed"}):
            response = await self.client.post("/payments/verify", json={"reference": reference})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["payment_status"], "unpaid")
        self._assert_payment_and_stock(order_id, "unpaid", "pending_payment")

        with patch.object(paystack_service, "_key", return_value="fictional-test-key"), \
             patch.object(paystack_service.httpx.Client, "request",
                          side_effect=httpx.ReadTimeout("simulated timeout")):
            response = await self.client.post("/payments/verify", json={"reference": reference})
        self.assertEqual(response.status_code, 400, response.text)
        self._assert_payment_and_stock(order_id, "unpaid", "pending_payment")

    async def test_sensitive_provider_errors_and_webhook_logs_are_sanitized(self):
        from core.services import paystack_service

        order_id, reference = await self._unpaid_payment_fixture()
        sentinel = "fictional-sensitive-payment-marker"
        trace_id = str(uuid4())
        output = io.StringIO()
        logger = logging.getLogger("naija_bites.payments")
        handler = logging.StreamHandler(output)
        previous_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        body = json.dumps({"event": "charge.success", "data": {"reference": reference}}).encode()
        signing_key = "fictional-only-signing-key"
        signature = hmac.new(signing_key.encode(), body, hashlib.sha512).hexdigest()
        try:
            with patch.object(paystack_service, "_key", return_value=signing_key), \
                 patch.object(paystack_service.httpx.Client, "request",
                     side_effect=httpx.ReadTimeout(sentinel + " " + reference)):
                direct = await self.client.post(
                    "/payments/verify", json={"reference": reference},
                    headers={"x-naija-trace-id": trace_id},
                )
                webhook = await self.client.post(
                    "/payments/webhook", content=body,
                    headers={"x-paystack-signature": signature,
                             "x-naija-trace-id": trace_id},
                )
            self.assertEqual(direct.status_code, 400, direct.text)
            self.assertEqual(webhook.status_code, 503, webhook.text)
            self.assertEqual(direct.json()["detail"], "Payment provider request failed.")
            self.assertEqual(webhook.json()["detail"], "Verification pending.")
            self._assert_payment_and_stock(order_id, "unpaid", "pending_payment")
            logged = output.getvalue()
            self.assertIn("category=timeout", logged)
            self.assertIn("category=verification_pending", logged)
            self.assertIn("trace_id=" + trace_id, logged)
            for private in (sentinel, reference, signing_key, "Authorization", "Bearer"):
                self.assertNotIn(private, logged)
                self.assertNotIn(private, direct.text)
                self.assertNotIn(private, webhook.text)
            self.assertNotIn("Traceback", logged)
        finally:
            logger.removeHandler(handler)
            handler.close()
            logger.setLevel(previous_level)

    async def test_sanitized_provider_rejections_and_transport_errors(self):
        from core.services import paystack_service

        order_id, reference = await self._unpaid_payment_fixture()
        marker = "fictional-private-provider-response"
        output = io.StringIO()
        logger = logging.getLogger("naija_bites.payments")
        handler = logging.StreamHandler(output)
        previous_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            with patch.object(paystack_service, "_key", return_value="fictional-only-key"), \
                 patch.object(paystack_service.httpx.Client, "request") as provider:
                provider.return_value.raise_for_status.return_value = None
                provider.return_value.json.return_value = {"status": False, "message": marker}
                rejected = await self.client.post("/payments/verify", json={"reference": reference})
                self.assertEqual(rejected.status_code, 400, rejected.text)
                self.assertNotIn(marker, rejected.text)
                provider.side_effect = httpx.ConnectError(marker + reference)
                unavailable = await self.client.post("/payments/verify", json={"reference": reference})
                self.assertEqual(unavailable.status_code, 400, unavailable.text)
            self._assert_payment_and_stock(order_id, "unpaid", "pending_payment")
            self.assertIn("category=invalid_response", output.getvalue())
            self.assertIn("category=transport", output.getvalue())
            self.assertNotIn(marker, output.getvalue())
            self.assertNotIn(reference, output.getvalue())
        finally:
            logger.removeHandler(handler)
            handler.close()
            logger.setLevel(previous_level)

    async def test_payment_and_db_metrics_do_not_expose_payloads(self):
        from core.services import paystack_service
        import contextlib

        order_id, reference = await self._unpaid_payment_fixture()
        output = io.StringIO()
        logger = logging.getLogger("naija_bites.metrics")
        handler = logging.StreamHandler(output)
        previous_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            with patch.object(paystack_service, "_request",
                              return_value=self._provider_success(reference)):
                response = await self.client.post("/payments/verify",
                    json={"reference": reference})
            self.assertEqual(response.status_code, 200, response.text)
            self._assert_payment_and_stock(order_id, "paid", "confirmed")
            log = output.getvalue()
            self.assertIn("db_metric", log)
            self.assertIn("db_request_summary", log)
            self.assertNotIn(reference, log)
            self.assertNotIn("SELECT", log)
        finally:
            logger.removeHandler(handler)
            handler.close()
            logger.setLevel(previous_level)

    async def test_invalid_webhook_signature_rejected_without_provider_call(self):
        from core.services import paystack_service

        order_id, reference = await self._unpaid_payment_fixture()
        body = json.dumps({"event": "charge.success", "data": {"reference": reference}}).encode()
        with patch.object(paystack_service, "_key", return_value="fictional-test-key"), \
             patch.object(paystack_service, "_request") as provider:
            response = await self.client.post("/payments/webhook", content=body,
                                              headers={"x-paystack-signature": "invalid"})
        self.assertEqual(response.status_code, 401, response.text)
        provider.assert_not_called()
        self._assert_payment_and_stock(order_id, "unpaid", "pending_payment")

    async def test_duplicate_webhooks_and_replay_are_idempotent(self):
        from core.services import paystack_service

        order_id, reference = await self._unpaid_payment_fixture()
        body = json.dumps({"event": "charge.success", "data": {"reference": reference}}).encode()
        fictional_key = "fictional-test-key"
        signature = hmac.new(fictional_key.encode(), body, hashlib.sha512).hexdigest()
        with patch.object(paystack_service, "_key", return_value=fictional_key), \
             patch.object(paystack_service, "_request",
                          return_value=self._provider_success(reference)) as provider:
            for _ in range(3):
                response = await self.client.post(
                    "/payments/webhook", content=body,
                    headers={"x-paystack-signature": signature},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["received"])
        self.assertEqual(provider.call_count, 3)
        self._assert_payment_and_stock(order_id, "paid", "confirmed")

    async def test_concurrent_verification_and_duplicate_callbacks_are_idempotent(self):
        from core.services import paystack_service

        order_id, reference = await self._unpaid_payment_fixture()
        with patch.object(paystack_service, "_request",
                          return_value=self._provider_success(reference)) as provider:
            responses = await asyncio.gather(*(
                self.client.post("/payments/verify", json={"reference": reference})
                for _ in range(3)
            ))
            callback = await self.client.post("/payments/verify", json={"reference": reference})
        self.assertEqual(provider.call_count, 4)
        for response in (*responses, callback):
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["payment_status"], "paid")
            self.assertEqual(response.json()["order_status"], "confirmed")
        self._assert_payment_and_stock(order_id, "paid", "confirmed")

    async def test_safe_correlation_across_chat_checkout_and_payment(self):
        from core.observability import correlation

        trace_id = str(uuid4())
        output = io.StringIO()
        logger = logging.getLogger("naija_bites.metrics")
        handler = logging.StreamHandler(output)
        previous_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        observed = []

        class FakeStrandsAgent:
            def __init__(self, **kwargs):
                pass
            async def run(self, input_data):
                observed.append(correlation.current_ids())
                if False:
                    yield None

        try:
            checkout = await self.client.get("/checkout", headers={"x-naija-trace-id": trace_id})
            payment = await self.client.post(
                "/payments/verify", json={"reference": "fictional-invalid-reference"},
                headers={"x-naija-trace-id": trace_id},
            )
            with patch("core.api.server.StrandsAgent", FakeStrandsAgent), \
                 patch("core.api.server.make_agent", return_value=object()):
                chat = await self.client.post("/agent", json={
                    "threadId": str(uuid4()), "runId": str(uuid4()),
                    "parentRunId": None, "state": {}, "messages": [],
                    "tools": [], "context": [], "forwardedProps": {},
                }, headers={"accept": "text/event-stream", "x-naija-trace-id": trace_id})
            self.assertEqual([r.status_code for r in (checkout, payment, chat)], [200, 400, 200])
            for response in (checkout, payment, chat):
                self.assertEqual(response.headers["x-naija-trace-id"], trace_id)
                self.assertEqual(UUID(response.headers["x-naija-request-id"]).version, 4)
            self.assertEqual(len(observed), 1)
            self.assertEqual(observed[0][0], trace_id)
            self.assertEqual(UUID(observed[0][1]).version, 4)
            logged = output.getvalue()
            for stage in ("chat", "checkout", "payment"):
                self.assertIn("stage=" + stage, logged)
            self.assertIn("trace_id=" + trace_id, logged)
            self.assertNotIn("fictional-invalid-reference", logged)
            self.assertNotIn("Fictional Street", logged)
        finally:
            logger.removeHandler(handler)
            handler.close()
            logger.setLevel(previous_level)

    async def test_stale_approval_is_rejected_after_cart_change(self):
        ready = (await self.prepare()).json()
        with models.get_connection() as conn:
            conn.execute("UPDATE conversation_sessions SET cart_data = ? WHERE session_id = ?", (
                json.dumps({"items": [{"product_id": self.product_id, "quantity": 1, "variant": "standard", "modifications": []}]}),
                f"customer-{self.customer_id}",
            ))
        new_ready = (await self.prepare()).json()
        self.assertNotEqual(new_ready["checkout_id"], ready["checkout_id"])
        stale = await self.client.post("/checkout/confirm", json={
            "customer_approved": True, "reviewed_fingerprint": ready["fingerprint"],
            "checkout_id": ready["checkout_id"],
        })
        self.assertEqual(stale.status_code, 409)
        self.assertEqual((await self.client.get("/checkout")).json()["checkout_id"], new_ready["checkout_id"])

    async def test_agent_stream_emits_trusted_checkout_event_without_model_text(self):
        """Even an empty assistant response must still carry checkout UI state."""
        ready = (await self.prepare()).json()

        class FakeStrandsAgent:
            def __init__(self, **kwargs):
                pass

            async def run(self, input_data):
                if False:
                    yield None

        with patch("core.api.server.StrandsAgent", FakeStrandsAgent), \
             patch("core.api.server.make_agent", return_value=object()):
            response = await self.client.post("/agent", json={
                "threadId": "1d5414a4-617e-4ac5-a5ae-000000000001",
                "runId": "1d5414a4-617e-4ac5-a5ae-000000000002",
                "parentRunId": None, "state": {}, "messages": [],
                "tools": [], "context": [], "forwardedProps": {},
            }, headers={"accept": "text/event-stream"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('"type":"CHECKOUT_STATE"', response.text)
        self.assertIn(ready["checkout_id"], response.text)
        self.assertIn('"status":"ready_for_review"', response.text)

    async def test_failed_preparation_never_creates_an_order(self):
        bad = await self.client.post("/checkout/prepare", json={
            "customer_name": "Test Customer", "phone": "invalid",
            "delivery_address": "Fictional Street", "delivery_area": "Lekki Phase 1",
        })
        self.assertEqual(bad.status_code, 409)
        state = (await self.client.get("/checkout")).json()
        self.assertFalse(state["pending"])
        self.assertTrue(state["cart_has_items"])
        with models.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
