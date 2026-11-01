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
        self.assertIsNone((await self.client.get("/checkout")).json()["payment_order_id"])

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
