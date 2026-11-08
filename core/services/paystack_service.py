"""Paystack TEST MODE integration. Not suitable for public use without authentication."""
import hashlib
import hmac
import os
import secrets
import sqlite3
import logging

import httpx
from data.database.models import get_connection

API = "https://api.paystack.co"

def _key():
    key = os.getenv("PAYSTACK_SECRET_KEY", "")
    if not key.startswith("sk_test_"):
        raise ValueError("Paystack TEST secret key (sk_test_...) is required.")
    return key

def _request(method, path, payload=None):
    try:
        with httpx.Client(timeout=20) as client:
            response = client.request(
                method, API + path,
                headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
        if not result.get("status"):
            raise ValueError("Paystack rejected the request.")
        return result["data"]
    except httpx.TimeoutException as exc:
        logging.getLogger(__name__).warning("paystack_transport_failure type=%s", type(exc).__name__)
        raise ValueError("Payment provider timed out. Please check payment status before retrying.") from exc
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
        logging.getLogger(__name__).warning("paystack_transport_failure type=%s", type(exc).__name__)
        raise ValueError("Payment provider temporarily unavailable. Please check payment status before retrying.") from exc

def _owned_order(conn, order_id, session_id):
    return conn.execute("""
        SELECT o.id, o.total, o.status, c.phone
        FROM orders o JOIN conversation_sessions s ON s.customer_id=o.customer_id
        JOIN customers c ON c.id=o.customer_id
        WHERE o.id=? AND s.session_id=?
    """, (order_id, session_id)).fetchone()

def initialize(order_id, session_id, email, callback_origin=None):
    if not isinstance(order_id, int) or isinstance(order_id, bool) or order_id <= 0:
        raise ValueError("Invalid order ID.")
    if not isinstance(email, str) or "@" not in email or len(email) > 254:
        raise ValueError("Enter a valid email address for the payment receipt.")
    email = email.strip()
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        order = _owned_order(conn, order_id, session_id)
        if order is None:
            raise ValueError("Order not found for this customer.")
        if order["status"] != "pending_payment":
            raise ValueError("This order is not awaiting payment.")
        existing = conn.execute("SELECT * FROM payments WHERE order_id=?", (order_id,)).fetchone()
        if existing:
            if existing["status"] == "paid":
                raise ValueError("Order is already paid.")
            if existing["payment_link"]:
                return {"order_id": order_id, "authorization_url": existing["payment_link"],
                        "reference": existing["payment_reference"]}
            reference = existing["payment_reference"]
        else:
            reference = f"nb_test_{order_id}_{secrets.token_hex(12)}"
            conn.execute("""INSERT INTO payments
                (order_id, amount, payment_reference, status)
                VALUES (?, ?, ?, 'unpaid')""", (order_id, order["total"], reference))
    # Save the reference before calling Paystack to avoid losing the association.
    # A failed initialization can safely be retried with the same reference.
    data = _request("POST", "/transaction/initialize", {
        "email": email, "amount": order["total"] * 100,
        "currency": "NGN", "reference": reference,
        "callback_url": (callback_origin.rstrip("/") + "/?payment_return=1"
            if callback_origin else os.getenv("PAYSTACK_CALLBACK_URL",
                "http://127.0.0.1:3000/?payment_return=1")),
        "metadata": {"order_id": order_id},
    })
    if data.get("reference") != reference or not str(data.get("authorization_url", "")).startswith("https://"):
        raise ValueError("Unexpected Paystack initialization response.")
    with get_connection() as conn:
        conn.execute("""UPDATE payments SET payment_link=? WHERE order_id=? AND payment_reference=?
            AND status='unpaid'""", (data["authorization_url"], order_id, reference))
    return {"order_id": order_id, "authorization_url": data["authorization_url"], "reference": reference}

def verify(reference, session_id=None):
    if not isinstance(reference, str) or len(reference) > 100 or not reference.startswith("nb_test_"):
        raise ValueError("Invalid payment reference.")
    with get_connection() as conn:
        row = conn.execute("""SELECT p.order_id, p.amount, p.status, o.customer_id
            FROM payments p JOIN orders o ON o.id=p.order_id
            WHERE p.payment_reference=?""", (reference,)).fetchone()
        if row is None:
            raise ValueError("Payment reference not found.")
        if session_id is not None and _owned_order(conn, row["order_id"], session_id) is None:
            raise ValueError("Payment does not belong to this customer.")
    data = _request("GET", "/transaction/verify/" + reference)
    if (data.get("reference") != reference or data.get("currency") != "NGN"
        or data.get("amount") != row["amount"] * 100):
        raise ValueError("Paystack verification did not match the saved order.")
    paid = data.get("status") == "success"
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT status FROM payments WHERE payment_reference=?", (reference,)).fetchone()
        if current is None:
            raise ValueError("Payment no longer exists.")
        if paid:
            conn.execute("""UPDATE payments SET status='paid', paid_at=COALESCE(paid_at,CURRENT_TIMESTAMP::text)
                WHERE payment_reference=? AND status != 'paid'""", (reference,))
            conn.execute("""UPDATE orders SET status='confirmed'
                WHERE id=? AND status='pending_payment'""", (row["order_id"],))
        status = conn.execute(
            "SELECT status FROM payments WHERE payment_reference=?", (reference,)
        ).fetchone()["status"]
        # Report persisted reality, not an inferred order state. A previously
        # cancelled/changed order must never be reported as confirmed merely
        # because Paystack has recorded a successful charge.
        order = conn.execute(
            "SELECT status FROM orders WHERE id=?", (row["order_id"],)
        ).fetchone()
        if order is None:
            raise ValueError("Payment order no longer exists.")
        order_status = order["status"]
    return {"order_id": row["order_id"], "reference": reference,
            "payment_status": status, "order_status": order_status,
            "reconciliation_required": status == "paid" and order_status != "confirmed"}

def verify_webhook_signature(body, signature):
    expected = hmac.new(_key().encode(), body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature or "")
