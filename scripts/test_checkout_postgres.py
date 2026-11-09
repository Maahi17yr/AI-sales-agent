"""Checkout regression on a disposable PostgreSQL 16 service in CI only."""
import asyncio
import json
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from core.api.server import app
from core.services import paystack_service
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


    async def test_paystack_verified_payment_updates_postgres_text_timestamp_and_chat(self):
        """Reproduce real Postgres DatatypeMismatch at paid_at=COALESCE(...).

        No provider traffic: payment success is mocked and the database is
        disposable CI-only PostgreSQL 16. Verify return and replay through HTTP.
        """
        delivery = {
            "customer_name": "Fictional PG Customer",
            "phone": "08012345678",
            "delivery_address": "Fictional Payment Street",
            "delivery_area": self.zone,
        }
        prepared = await self.client.post("/checkout/prepare", json=delivery)
        self.assertEqual(prepared.status_code, 200, prepared.text)
        ready = prepared.json()
        confirmed = await self.client.post("/checkout/confirm", json={
            "customer_approved": True,
            "reviewed_fingerprint": ready["fingerprint"],
            "checkout_id": ready["checkout_id"],
        })
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        order_id = confirmed.json()["order_id"]
        reference = f"nb_test_{order_id}_{uuid4().hex[:16]}"
        amount = confirmed.json()["total"]

        chat_id = str(uuid4())
        saved = await self.client.put("/chats/" + chat_id, json={
            "title": "Fictional payment test",
            "messages": [
                {"id": "user-1", "role": "user", "content": "My fictional order"},
            ],
        })
        self.assertEqual(saved.status_code, 200, saved.text)

        with models.get_connection() as conn:
            conn.execute(
                "INSERT INTO payments (order_id, amount, payment_reference, status) "
                "VALUES (?, ?, ?, 'unpaid')", (order_id, amount, reference),
            )

        def mocked_provider(method, path, payload=None):
            self.assertEqual(method, "GET")
            self.assertEqual(path, "/transaction/verify/" + reference)
            return {"reference": reference, "currency": "NGN",
                    "amount": amount * 100, "status": "success"}

        with patch.object(paystack_service, "_request", side_effect=mocked_provider):
            first = await self.client.post("/payments/verify", json={
                "reference": reference, "chat_id": chat_id,
            })
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()["payment_status"], "paid")
            self.assertEqual(first.json()["order_status"], "confirmed")
            self.assertTrue(first.json()["chat_saved"])
            replay = await self.client.post("/payments/verify", json={
                "reference": reference, "chat_id": chat_id,
            })
            self.assertEqual(replay.status_code, 200, replay.text)

        with models.get_connection() as conn:
            payment = conn.execute(
                "SELECT status, paid_at FROM payments WHERE payment_reference=?",
                (reference,),
            ).fetchone()
            self.assertEqual(payment["status"], "paid")
            self.assertTrue(payment["paid_at"])
            self.assertEqual(conn.execute(
                "SELECT status FROM orders WHERE id=?", (order_id,),
            ).fetchone()["status"], "confirmed")
            self.assertEqual(conn.execute(
                "SELECT quantity FROM inventory WHERE product_id=?",
                (self.product_id,),
            ).fetchone()["quantity"], 8)
            chat = conn.execute(
                "SELECT messages FROM customer_chats WHERE customer_id=? AND chat_id=?",
                (self.customer_id, chat_id),
            ).fetchone()
            confirmations = [
                item for item in json.loads(chat["messages"])
                if item.get("id") == f"verified-payment-order-{order_id}"
            ]
            self.assertEqual(len(confirmations), 1)


if __name__ == "__main__":
    unittest.main()
