"""Checkout regression on a disposable PostgreSQL 16 service in CI only."""
import asyncio
import json
import os
import unittest
from uuid import uuid4

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
        self.email = f"postgres-checkout-{uuid4().hex[:10]}@example.test"
        self.zone = f"PG Zone {uuid4().hex[:10]}"
        registration = await self.client.post("/auth/register", json={
            "name": "PG Checkout", "email": self.email,
            "password": "Fictional-PG-Password-12345",
        })
        self.assertEqual(registration.status_code, 201, registration.text)
        login = await self.client.post("/auth/login", json={
            "email": self.email,
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
                (self.zone, 4500, 1, 3, 1),
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
            "delivery_area": self.zone,
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


    async def test_two_customers_compete_for_last_item_without_oversell(self):
        """Two different sessions may review stock 1; only one can commit."""
        other = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 54322)),
            base_url="http://127.0.0.1:8000",
        )
        try:
            email = f"postgres-other-{uuid4().hex[:10]}@example.test"
            registered = await other.post("/auth/register", json={
                "name": "PG Other", "email": email,
                "password": "Fictional-Other-PG-Password-12345",
            })
            self.assertEqual(registered.status_code, 201, registered.text)
            login = await other.post("/auth/login", json={
                "email": email, "password": "Fictional-Other-PG-Password-12345",
            })
            self.assertEqual(login.status_code, 200, login.text)
            other_id = login.json()["customer"]["id"]
            self.assertEqual((await other.get("/checkout")).status_code, 200)

            one = json.dumps({"items": [{
                "product_id": self.product_id, "quantity": 1,
                "variant": "standard", "modifications": [],
            }]})
            with models.get_connection() as conn:
                conn.execute(
                    "UPDATE inventory SET quantity=1 WHERE product_id=? AND variant='standard'",
                    (self.product_id,),
                )
                for cid in (self.customer_id, other_id):
                    conn.execute(
                        "UPDATE conversation_sessions SET cart_data=? WHERE session_id=?",
                        (one, f"customer-{cid}"),
                    )

            delivery = {
                "customer_name": "Fictional PG Customer", "phone": "08012345678",
                "delivery_address": "Fictional PG Race Street",
                "delivery_area": self.zone,
            }
            first = await self.client.post("/checkout/prepare", json=delivery)
            second = await other.post("/checkout/prepare", json=delivery)
            for result in (first, second):
                self.assertEqual(result.status_code, 200, result.text)
            ready1, ready2 = first.json(), second.json()
            def approval(ready):
                return {"customer_approved": True,
                        "reviewed_fingerprint": ready["fingerprint"],
                        "checkout_id": ready["checkout_id"]}
            outcomes = await asyncio.gather(
                self.client.post("/checkout/confirm", json=approval(ready1)),
                other.post("/checkout/confirm", json=approval(ready2)),
            )
            self.assertEqual(sorted(r.status_code for r in outcomes), [200, 409],
                             [r.text for r in outcomes])
            with models.get_connection() as conn:
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM orders WHERE customer_id IN (?,?)",
                    (self.customer_id, other_id),
                ).fetchone()[0], 1)
                self.assertEqual(conn.execute(
                    "SELECT quantity FROM inventory WHERE product_id=?",
                    (self.product_id,),
                ).fetchone()["quantity"], 0)
        finally:
            await other.aclose()


if __name__ == "__main__":
    unittest.main()
