"""Disposable end-to-end checkout smoke through Next.js -> FastAPI -> SQLite.

The backend and frontend are started by chat-safety.yml. No external payment
or LLM calls occur, and DATABASE_URL must be absent.
"""
import json
import os
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:3000"
API = BASE + "/api/backend"
EMAIL = "checkout-smoke@example.test"
PASSWORD = "Fictional-Test-Password-12345"


def require(response, status=200):
    if response.status_code != status:
        raise AssertionError(
            f"{response.request.method} {response.request.url}: "
            f"expected {status}, got {response.status_code}: {response.text[:500]}"
        )
    return response.json()


def main():
    if not os.getenv("NAIJA_BITES_DATA_DIR") or os.getenv("DATABASE_URL"):
        raise RuntimeError("Smoke test requires isolated NAIJA_BITES_DATA_DIR and no DATABASE_URL.")

    from data.database.models import get_connection

    with httpx.Client(timeout=25, follow_redirects=False) as client:
        for _ in range(60):
            try:
                if client.get(API + "/ping").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(2)
        else:
            raise RuntimeError("Disposable backend and Next.js proxy are unavailable.")

        require(client.post(API + "/auth/register", json={
            "name": "Checkout Smoke", "email": EMAIL, "password": PASSWORD,
        }), 201)
        login = require(client.post(API + "/auth/login", json={
            "email": EMAIL, "password": PASSWORD,
        }))
        customer_id = login["customer"]["id"]
        session_id = f"customer-{customer_id}"

        # The data is fictional and lives only in the disposable CI database.
        with get_connection() as conn:
            product_id = conn.execute(
                "INSERT INTO products (name, category, price, available) VALUES (?, ?, ?, ?)",
                ("Fictional Party Jollof", "rice", 4500, 1),
            ).lastrowid
            conn.execute(
                "INSERT INTO inventory (product_id, variant, quantity) VALUES (?, ?, ?)",
                (product_id, "standard", 20),
            )
            conn.execute(
                "INSERT INTO delivery_zones (area, fee, estimated_min_hours, estimated_max_hours, available) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Lekki Phase 1", 4500, 1, 3, 1),
            )
            conn.execute(
                "UPDATE conversation_sessions SET cart_data = ? WHERE session_id = ?",
                (json.dumps({"items": [{
                    "product_id": product_id, "quantity": 2,
                    "variant": "standard", "modifications": [],
                }]}), session_id),
            )

        initial = require(client.get(API + "/checkout"))
        assert initial["cart_has_items"] and not initial["pending"], initial

        delivery = {
            "customer_name": "Checkout Smoke",
            "phone": "08012345678",
            "delivery_address": "Fictional Test Street, Lekki",
            "delivery_area": "Lekki Phase 1",
        }
        ready = require(client.post(API + "/checkout/prepare", json=delivery))
        assert ready["pending"] and ready["status"] == "ready_for_review", ready
        assert ready["summary"]["total"] == 13500, ready
        assert ready["checkout_id"]

        # A fresh HTTP client represents a browser refresh: same auth cookie,
        # no in-memory React state. The card data must be fully recoverable.
        with httpx.Client(timeout=25, follow_redirects=False, cookies=client.cookies) as refreshed:
            restored = require(refreshed.get(API + "/checkout"))
            assert restored["checkout_id"] == ready["checkout_id"], restored
            assert restored["summary"] == ready["summary"], restored

        retried = require(client.post(API + "/checkout/prepare", json=delivery))
        assert retried["checkout_id"] == ready["checkout_id"], retried

        approval = {
            "customer_approved": True,
            "reviewed_fingerprint": ready["fingerprint"],
            "checkout_id": ready["checkout_id"],
        }
        confirmed = require(client.post(API + "/checkout/confirm", json=approval))
        order_id = confirmed["order_id"]
        assert confirmed["status"] == "pending_payment", confirmed
        repeated = require(client.post(API + "/checkout/confirm", json=approval))
        assert repeated["order_id"] == order_id, repeated

        pending_payment = require(client.get(API + "/checkout"))
        assert pending_payment["status"] == "payment_pending", pending_payment
        assert pending_payment["payment_order_id"] == order_id, pending_payment
        assert not pending_payment["pending"]

        with get_connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM orders WHERE customer_id = ?", (customer_id,)).fetchone()[0] == 1
            assert conn.execute(
                "SELECT quantity FROM inventory WHERE product_id = ?", (product_id,)
            ).fetchone()[0] == 18
            # Simulate an externally verified payment status; no Paystack call.
            conn.execute("UPDATE orders SET status = 'confirmed' WHERE id = ?", (order_id,))

        paid = require(client.get(API + "/checkout"))
        assert paid["status"] == "paid" and paid["payment_order_id"] is None, paid
        paid_replay = require(client.post(API + "/checkout/confirm", json=approval))
        assert paid_replay["order_id"] == order_id and paid_replay["status"] == "confirmed", paid_replay

        print("PASS: Next.js proxy, durable review, refresh, idempotent confirm, payment recovery and paid state")


if __name__ == "__main__":
    main()
