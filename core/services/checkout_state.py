"""Authoritative checkout snapshot shared by REST and agent-stream events.

The authenticated server session is the only source of identity. Never derive
checkout state from assistant prose or a browser-supplied session identifier.
"""
import json
import logging

from data.database.models import get_connection
from core.services.order_service import fingerprint

logger = logging.getLogger(__name__)


def get_checkout_state(session_id: str) -> dict:
    """Read the latest committed state so reloads and missed events recover."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT customer_id, cart_data FROM conversation_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        state = {
            "success": True,
            "pending": False,
            "status": "cart",
            "summary": None,
            "fingerprint": None,
            "checkout_id": None,
            "cart_has_items": False,
            "payment_order_id": None,
        }
        if row is None:
            return state
        try:
            cart = json.loads(row["cart_data"])
        except (ValueError, TypeError):
            logger.warning("checkout_state_invalid_cart")
            return state
        if not isinstance(cart, dict):
            return state
        state["cart_has_items"] = bool(cart.get("items"))
        pending = cart.get("pending_checkout")
        if isinstance(pending, dict):
            summary = pending.get("summary")
            saved_fingerprint = pending.get("fingerprint")
            if (isinstance(summary, dict)
                    and isinstance(saved_fingerprint, str)
                    and fingerprint(summary) == saved_fingerprint):
                state.update(
                    pending=True,
                    status="ready_for_review",
                    summary=summary,
                    fingerprint=saved_fingerprint,
                    checkout_id=pending.get("checkout_id"),
                )
                return state
            logger.warning("checkout_state_invalid_pending_summary")
        last = cart.get("last_checkout")
        if isinstance(last, dict) and type(last.get("order_id")) is int:
            order = conn.execute(
                "SELECT id, status FROM orders WHERE id = ? AND customer_id = ?",
                (last["order_id"], row["customer_id"]),
            ).fetchone()
            if order and order["status"] == "pending_payment":
                state.update(status="payment_pending", payment_order_id=order["id"])
            elif order and order["status"] in (
                "confirmed", "preparing", "out_for_delivery", "delivered"
            ):
                # A verified payment transitions the order out of pending
                # payment. Never offer to pay a completed order again.
                state["status"] = "paid"
        return state
    finally:
        conn.close()
