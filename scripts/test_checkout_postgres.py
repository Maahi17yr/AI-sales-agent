"""Checkout regression on a disposable PostgreSQL 16 service in CI only."""
import asyncio
import json
import os
import unittest

import httpx

from core.api.server import app
from data.database import models


class CheckoutPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if os.getenv("NAIJA_BITES_POSTGRES_CI") != "1" or not os.getenv(
            "DATABASE_URL", ""
        ).startswith("postgresql://postgres:postgres@127.0.0.1:5432/"):
            self.skipTest("Requires the isolated CI PostgreSQL database.")
        models.create_tables()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 54321)),
            base_url="http://127.0.0.1:8000",
        )
        registration = await self.client.post("/auth/register", json={
            "name": "PG Checkout", "email": "postgres-checkout@example.test",
            "password": "Fictional-PG-Password-12345",
        })
        self.assertEqual(registration.status_code, 201, registration.text)
        login = await self.client.post("/auth/login", json={
            "email": "postgres-checkout@example.test",
            "password": "Fictional-PG-Password-12345",
        })
        self.assertEqual(login.status_code, 200, login.text)
        self.customer_id = login.json()["customer"]["id"]
        self.assertEqual((await self.client.get("/checkout")).status_code, 200)
        with models.get_connection() as conn:
            self.product_id = conn.execute(
                "INSERT INTO products (name, category, price, available) VALUES (?, ?, ?, ?)",
                ("Fictional PG Jollof", "rice", 4500, 1),
            ).lastrowid
            conn.execute(
                "INSERT INTO inventory (product_id, variant, quantity) VALUES (?, ?, ?)",
                (self.product_id, "standard", 10),
            )
            conn.execute(
                "INSERT INTO delivery_zones (area, fee, estimated_min_hours, estimated_max_hours, available) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Lekki Phase 1", 4500, 1, 3, 1),
            )
            conn.execute(
                "UPDATE conversation_sessions SET cart_data = ? WHERE session_id = ?",
                (json.dumps({"items": [{
                    "product_id": self.product_id, "quantity": 2,
                    "variant": "standard", "modifications": [],
                }]}), f"customer-{self.customer_id}"),
            )

    async def asyncTearDown(self):
        if hasattr(self, "client"):
            await self.client.aclose()

    async def test_checkout_persists_and_concurrent_confirmation_creates_one_order(self):
        delivery = {
            "customer_name": "PG Checkout", "phone": "08012345678",
            "delivery_address": "Fictional PG Street, Lekki",
            "delivery_area": "Lekki Phase 1",
        }
        prepared = await self.client.post("/checkout/prepare", json=delivery)
        self.assertEqual(prepared.status_code, 200, prepared.text)
        ready = prepared.json()
        self.assertEqual(ready["status"], "ready_for_review")
        self.assertEqual(ready["summary"]["total"], 13500)
        self.assertEqual((await self.client.get("/checkout")).json()["checkout_id"], ready["checkout_id"])

        approval = {
            "customer_approved": True, "reviewed_fingerprint": ready["fingerprint"],
            "checkout_id": ready["checkout_id"],
        }
        responses = await asyncio.gather(*(
            self.client.post("/checkout/confirm", json=approval) for _ in range(2)
        ))
        for response in responses:
            self.assertEqual(response.status_code, 200, response.text)
        order_ids = [r.json()["order_id"] for r in responses]
        self.assertEqual(order_ids[0], order_ids[1])
        self.assertEqual((await self.client.get("/checkout")).json()["status"], "payment_pending")
        with models.get_connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM orders WHERE customer_id = ?", (self.customer_id,)
            ).fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT quantity FROM inventory WHERE product_id = ?", (self.product_id,)
            ).fetchone()[0], 8)


if __name__ == "__main__":
    unittest.main()
