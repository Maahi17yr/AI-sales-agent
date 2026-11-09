"""Regression tests for checkout events' authoritative state and safe retries.

Run: python -m unittest scripts.test_checkout_reliability -v
No Bedrock, Paystack, production database, or real customer data is used.
"""
import json
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

    async def _create_mock_payment_order(self):
        from core.services import paystack_service

        ready = (await self.prepare()).json()
        placed = await self.client.post("/checkout/confirm", json={
            "customer_approved": True, "reviewed_fingerprint": ready["fingerprint"],
            "checkout_id": ready["checkout_id"],
        })
        self.assertEqual(placed.status_code, 200, placed.text)
        order_id = placed.json()["order_id"]
        def mock_initialize(method, path, payload=None):
            self.assertEqual(method, "POST")
            return {"reference": payload["reference"],
                    "authorization_url": "https://checkout.paystack.test/fictional"}
        with patch.object(paystack_service, "_request", side_effect=mock_initialize):
            initialized = paystack_service.initialize(
                order_id, f"customer-{self.customer_id}", "fictional@example.test")
        return order_id, initialized["reference"], placed.json()["total"]

    async def test_payment_verify_replay_is_idempotent_and_does_not_restock(self):
        from core.services import paystack_service

        order_id, reference, amount = await self._create_mock_payment_order()
        def mock_verify(method, path, payload=None):
            self.assertEqual(method, "GET")
            self.assertEqual(path, "/transaction/verify/" + reference)
            return {"reference": reference, "currency": "NGN",
                    "amount": amount * 100, "status": "success"}
        with patch.object(paystack_service, "_request", side_effect=mock_verify):
            first = paystack_service.verify(reference, f"customer-{self.customer_id}")
            replay = paystack_service.verify(reference, f"customer-{self.customer_id}")
        self.assertEqual(first["order_status"], "confirmed")
        self.assertFalse(first["reconciliation_required"])
        self.assertEqual(replay["order_status"], "confirmed")
        with models.get_connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM payments WHERE order_id = ?", (order_id,)
            ).fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT quantity FROM inventory WHERE product_id = ?", (self.product_id,)
            ).fetchone()[0], 8)

    async def test_paid_cancelled_order_must_not_claim_confirmation(self):
        from core.services import paystack_service

        order_id, reference, amount = await self._create_mock_payment_order()
        with models.get_connection() as conn:
            conn.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))
        with patch.object(paystack_service, "_request", return_value={
            "reference": reference, "currency": "NGN",
            "amount": amount * 100, "status": "success",
        }):
            verified = paystack_service.verify(reference, f"customer-{self.customer_id}")
        self.assertEqual(verified["payment_status"], "paid")
        self.assertEqual(verified["order_status"], "cancelled")
        self.assertTrue(verified["reconciliation_required"])
        with models.get_connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM orders WHERE id=?", (order_id,)
            ).fetchone()["status"], "cancelled")

    async def test_failed_commit_rolls_back_order_and_inventory(self):
        import sqlite3
        from core.services.order_service import OrderService

        ready = (await self.prepare()).json()
        session_id = f"customer-{self.customer_id}"
        original_connection = models.get_connection

        class FailBeforeCommit:
            def __init__(self):
                self.conn = original_connection()
            def execute(self, *args, **kwargs):
                return self.conn.execute(*args, **kwargs)
            def commit(self):
                raise sqlite3.OperationalError("SECRET_COMMIT_FAILURE")
            def rollback(self):
                return self.conn.rollback()
            def close(self):
                return self.conn.close()

        with patch("core.services.order_service.get_connection", side_effect=FailBeforeCommit):
            result = OrderService(session_id)._commit_approved_checkout(
                ready["fingerprint"], checkout_id=ready["checkout_id"])
        self.assertFalse(result["success"])
        self.assertNotIn("SECRET_COMMIT_FAILURE", str(result))
        with models.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT quantity FROM inventory WHERE product_id=?", (self.product_id,)
            ).fetchone()[0], 10)
        retry = OrderService(session_id)._commit_approved_checkout(
            ready["fingerprint"], checkout_id=ready["checkout_id"])
        self.assertTrue(retry["success"])
        with models.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT quantity FROM inventory WHERE product_id=?", (self.product_id,)
            ).fetchone()[0], 8)

    async def test_commit_acknowledgement_lost_recovers_same_durable_order(self):
        import sqlite3
        from core.services.order_service import OrderService

        ready = (await self.prepare()).json()
        session_id = f"customer-{self.customer_id}"
        original_connection = models.get_connection

        class CommitThenDisconnect:
            def __init__(self):
                self.conn = original_connection()
            def execute(self, *args, **kwargs):
                return self.conn.execute(*args, **kwargs)
            def commit(self):
                self.conn.commit()
                raise sqlite3.OperationalError("SECRET_CONNECTION_DROPPED_AFTER_COMMIT")
            def rollback(self):
                return self.conn.rollback()
            def close(self):
                return self.conn.close()

        with patch("core.services.order_service.get_connection", side_effect=CommitThenDisconnect):
            uncertain = OrderService(session_id)._commit_approved_checkout(
                ready["fingerprint"], checkout_id=ready["checkout_id"])
        self.assertFalse(uncertain["success"])
        self.assertNotIn("SECRET_CONNECTION_DROPPED", str(uncertain))
        replay = OrderService(session_id)._commit_approved_checkout(
            ready["fingerprint"], checkout_id=ready["checkout_id"])
        self.assertTrue(replay["success"])
        with models.get_connection() as conn:
            order_id = conn.execute("SELECT id FROM orders").fetchone()["id"]
            self.assertEqual(replay["order_id"], order_id)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT quantity FROM inventory WHERE product_id=?", (self.product_id,)
            ).fetchone()[0], 8)

    async def test_old_linked_checkout_never_sticks_to_chat(self):
        ready = (await self.prepare()).json()
        approval = {"customer_approved": True,
                    "reviewed_fingerprint": ready["fingerprint"],
                    "checkout_id": ready["checkout_id"]}
        placed = await self.client.post("/checkout/confirm", json=approval)
        self.assertEqual(placed.status_code, 200, placed.text)
        order_id = placed.json()["order_id"]
        self.assertEqual((await self.client.get("/checkout")).json()["payment_order_id"], order_id)
        with models.get_connection() as conn:
            conn.execute("UPDATE orders SET created_at = ? WHERE id = ?",
                         ("2020-01-01 00:00:00", order_id))
        state = (await self.client.get("/checkout")).json()
        self.assertEqual(state["status"], "cart")
        self.assertIsNone(state["payment_order_id"])
        # Expiring a payment prompt never deletes the order or breaks replay.
        replay = await self.client.post("/checkout/confirm", json=approval)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["order_id"], order_id)

    async def test_hide_payment_card_survives_refresh_and_lost_link(self):
        ready = (await self.prepare()).json()
        placed = await self.client.post("/checkout/confirm", json={
            "customer_approved": True,
            "reviewed_fingerprint": ready["fingerprint"],
            "checkout_id": ready["checkout_id"]})
        self.assertEqual(placed.status_code, 200, placed.text)
        order_id = placed.json()["order_id"]
        self.assertEqual((await self.client.get("/checkout")).json()["payment_order_id"], order_id)
        hidden = await self.client.post("/checkout/dismiss-payment", json={"order_id": order_id})
        self.assertEqual(hidden.status_code, 200, hidden.text)
        self.assertIsNone(hidden.json()["payment_order_id"])
        self.assertIsNone((await self.client.get("/checkout")).json()["payment_order_id"])
        # Even if chat overwrites the checkout link, the recent fallback must
        # respect the per-customer dismissed order.
        with models.get_connection() as conn:
            conn.execute(
                "UPDATE conversation_sessions SET cart_data=? WHERE session_id=?",
                (json.dumps({"items": [], "dismissed_payment_order_id": order_id}),
                 f"customer-{self.customer_id}"))
        self.assertIsNone((await self.client.get("/checkout")).json()["payment_order_id"])
        with models.get_connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM orders WHERE id=?", (order_id,)
            ).fetchone()["status"], "pending_payment")
            self.assertEqual(conn.execute(
                "SELECT quantity FROM inventory WHERE product_id=?", (self.product_id,)
            ).fetchone()["quantity"], 8)
        self.assertEqual((await self.client.post(
            "/checkout/dismiss-payment", json={"order_id": 99999999}
        )).status_code, 404)
        self.assertEqual((await self.client.post(
            "/checkout/dismiss-payment", json={"order_id": -1}
        )).status_code, 422)

    async def test_new_cart_does_not_inherit_previous_unpaid_payment_card(self):
        ready = (await self.prepare()).json()
        placed = await self.client.post("/checkout/confirm", json={
            "customer_approved": True,
            "reviewed_fingerprint": ready["fingerprint"],
            "checkout_id": ready["checkout_id"],
        })
        self.assertEqual(placed.status_code, 200, placed.text)
        previous_order = placed.json()["order_id"]
        self.assertEqual(
            (await self.client.get("/checkout")).json()["payment_order_id"],
            previous_order,
        )
        # Starting a new order can retain last_checkout for idempotent replay,
        # but must not show the previous order as this cart's payment request.
        with models.get_connection() as conn:
            session_id = f"customer-{self.customer_id}"
            row = conn.execute(
                "SELECT cart_data FROM conversation_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
            cart = json.loads(row["cart_data"])
            cart["items"] = [{
                "product_id": self.product_id, "quantity": 1,
                "variant": "standard", "modifications": [],
            }]
            conn.execute(
                "UPDATE conversation_sessions SET cart_data=? WHERE session_id=?",
                (json.dumps(cart), session_id),
            )
        active = (await self.client.get("/checkout")).json()
        self.assertTrue(active["cart_has_items"])
        self.assertIsNone(active["payment_order_id"])
        self.assertEqual(active["status"], "cart")
        with models.get_connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM orders WHERE id=?", (previous_order,)
            ).fetchone()["status"], "pending_payment")

    async def test_historical_unpaid_order_is_not_active_without_checkout_link(self):
        # An unrelated earlier order can still be unpaid, but it must not
        # become the current payment card after starting a fresh session/cart.
        with models.get_connection() as conn:
            zone = conn.execute(
                "SELECT id FROM delivery_zones WHERE area = ?",
                ("Lekki Phase 1",),
            ).fetchone()["id"]
            order_id = conn.execute(
                "INSERT INTO orders (customer_id, delivery_zone_id, "
                "delivery_address, subtotal, delivery_fee, discount, total, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_payment')",
                (self.customer_id, zone, "Old test address", 4500, 4500, 0, 9000),
            ).lastrowid
        state = (await self.client.get("/checkout")).json()
        self.assertEqual(state["status"], "cart")
        self.assertIsNone(state["payment_order_id"])
        self.assertTrue(state["cart_has_items"])
        with models.get_connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM orders WHERE id = ?", (order_id,)
            ).fetchone()["status"], "pending_payment")

    async def test_lost_checkout_link_only_recovers_recent_unpaid_order(self):
        ready = (await self.prepare()).json()
        approved = await self.client.post("/checkout/confirm", json={
            "customer_approved": True,
            "reviewed_fingerprint": ready["fingerprint"],
            "checkout_id": ready["checkout_id"],
        })
        self.assertEqual(approved.status_code, 200, approved.text)
        old_order = approved.json()["order_id"]
        with models.get_connection() as conn:
            conn.execute(
                "UPDATE conversation_sessions SET cart_data = ? WHERE session_id = ?",
                (json.dumps({"items": []}), f"customer-{self.customer_id}"),
            )
        recovered = (await self.client.get("/checkout")).json()
        self.assertEqual(recovered["status"], "payment_pending")
        self.assertEqual(recovered["payment_order_id"], old_order)
        # A historical order is not an active checkout, even if unpaid.
        with models.get_connection() as conn:
            conn.execute(
                "UPDATE orders SET created_at = ? WHERE id = ?",
                ("2020-01-01 00:00:00", old_order),
            )
        state = (await self.client.get("/checkout")).json()
        self.assertEqual(state["status"], "cart")
        self.assertIsNone(state["payment_order_id"])
        with models.get_connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM orders WHERE id = ?", (old_order,)
            ).fetchone()["status"], "pending_payment")

    async def test_full_mocked_customer_payment_return_new_order_and_isolation(self):
        """No real Paystack/Bedrock; exercise authenticated HTTP API end to end."""
        from core.services import paystack_service

        me = await self.client.get("/auth/me")
        self.assertEqual(me.status_code, 200)
        menu = await self.client.get("/menu")
        self.assertEqual(menu.status_code, 200)
        self.assertTrue(any(p["id"] == self.product_id for p in menu.json()["products"]))

        chat_id = "d5e24b92-9a7b-4bd0-a211-0b456e03c501"
        chat_payload = {"title": "Mocked order", "messages": [
            {"id": "user-msg-1", "role": "user", "content": "I'd like two fictional meals."},
        ]}
        self.assertEqual((await self.client.put("/chats/" + chat_id, json=chat_payload)).status_code, 200)
        self.assertEqual(len((await self.client.get("/chats")).json()["chats"]), 1)

        ready = (await self.prepare()).json()
        self.assertEqual(ready["status"], "ready_for_review")
        approval = {"customer_approved": True, "reviewed_fingerprint": ready["fingerprint"],
                    "checkout_id": ready["checkout_id"]}
        approved = await self.client.post("/checkout/confirm", json=approval)
        self.assertEqual(approved.status_code, 200, approved.text)
        order_id = approved.json()["order_id"]
        self.assertEqual((await self.client.post("/checkout/confirm", json=approval)).json()["order_id"], order_id)
        self.assertEqual((await self.client.get("/checkout")).json()["payment_order_id"], order_id)

        requests = []
        def fake_provider(method, path, payload=None):
            requests.append((method, path))
            if method == "POST":
                return {"reference": payload["reference"],
                        "authorization_url": "https://checkout.paystack.test/fictional"}
            return {"reference": reference, "currency": "NGN",
                    "amount": approved.json()["total"] * 100, "status": "success"}

        with patch.object(paystack_service, "_request", side_effect=fake_provider):
            init = await self.client.post("/payments/initialize", json={
                "order_id": order_id, "email": "test@example.test"})
            self.assertEqual(init.status_code, 200, init.text)
            reference = init.json()["reference"]
            retry_init = await self.client.post("/payments/initialize", json={
                "order_id": order_id, "email": "test@example.test"})
            self.assertEqual(retry_init.json()["reference"], reference)
            self.assertEqual(sum(1 for method, _ in requests if method == "POST"), 1)
            verification = await self.client.post("/payments/verify", json={
                "reference": reference, "chat_id": chat_id})
            self.assertEqual(verification.status_code, 200, verification.text)
            self.assertEqual(verification.json()["order_status"], "confirmed")
            self.assertTrue(verification.json()["chat_saved"])
            replay = await self.client.post("/payments/verify", json={
                "reference": reference, "chat_id": chat_id})
            self.assertEqual(replay.status_code, 200, replay.text)

        saved = (await self.client.get("/chats")).json()["chats"][0]["messages"]
        confirmation_id = "verified-payment-order-" + str(order_id)
        self.assertEqual(sum(1 for m in saved if m["id"] == confirmation_id), 1)
        checkout_after_payment = (await self.client.get("/checkout")).json()
        self.assertIsNone(checkout_after_payment["payment_order_id"])

        # Another account and a fresh browser session must not inherit this order.
        other = httpx.AsyncClient(transport=httpx.ASGITransport(
            app=app, client=("127.0.0.1", 54322)), base_url="http://127.0.0.1:8000")
        try:
            self.assertEqual((await other.get("/checkout")).status_code, 401)
            self.assertEqual((await other.post("/auth/register", json={
                "name": "Other Test", "email": "other@example.test",
                "password": "Another-Fictional-Password-1234"})).status_code, 201)
            self.assertEqual((await other.post("/auth/login", json={
                "email": "other@example.test",
                "password": "Another-Fictional-Password-1234"})).status_code, 200)
            self.assertEqual((await other.get("/chats")).json()["chats"], [])
            self.assertIsNone((await other.get("/checkout")).json()["payment_order_id"])
            with patch.object(paystack_service, "_request", side_effect=AssertionError("must not call provider")):
                refused = await other.post("/payments/verify", json={"reference": reference})
                self.assertEqual(refused.status_code, 400, refused.text)
        finally:
            await other.aclose()

        # New order must not resurrect the completed payment card.
        with models.get_connection() as conn:
            conn.execute("UPDATE conversation_sessions SET cart_data=? WHERE session_id=?", (
                json.dumps({"items": [{"product_id": self.product_id, "quantity": 1,
                                      "variant": "standard", "modifications": []}]}),
                f"customer-{self.customer_id}"))
        new_cart = (await self.client.get("/checkout")).json()
        self.assertTrue(new_cart["cart_has_items"])
        self.assertIsNone(new_cart["payment_order_id"])
        next_review = (await self.prepare()).json()
        self.assertEqual(next_review["status"], "ready_for_review")
        self.assertNotEqual(next_review["checkout_id"], ready["checkout_id"])
        with models.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT quantity FROM inventory WHERE product_id=?", (self.product_id,)
            ).fetchone()[0], 8)

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
