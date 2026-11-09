"""Authoritative checkout snapshot shared by REST and agent-stream events.

The authenticated server session is the only source of identity. Never derive
checkout state from assistant prose or a browser-supplied session identifier.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

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
        order = None
        if isinstance(last, dict) and type(last.get("order_id")) is int:
            order = conn.execute(
                "SELECT id, status FROM orders WHERE id = ? AND customer_id = ?",
                (last["order_id"], row["customer_id"]),
            ).fetchone()

        if order is None and not state["cart_has_items"] and row["customer_id"]:
            # A concurrent chat save can overwrite last_checkout immediately
            # after commit. Recover only a very recent order in that case.
            # Never display an older unpaid order as the active payment card.
            candidate = conn.execute(
                "SELECT id, status, created_at FROM orders WHERE customer_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (row["customer_id"],),
            ).fetchone()
            if candidate and candidate["status"] == "pending_payment":
                try:
                    created = datetime.fromisoformat(str(candidate["created_at"]))
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    age = datetime.now(timezone.utc) - created.astimezone(timezone.utc)
                    if timedelta(0) <= age <= timedelta(minutes=15):
                        order = candidate
                except (ValueError, TypeError):
                    logger.warning("checkout_state_invalid_order_created_at")
        if order and order["status"] == "pending_payment":
            state.update(status="payment_pending", payment_order_id=order["id"])
        elif order and order["status"] in (
            "confirmed", "preparing", "out_for_delivery", "delivered"
        ):
            # Never offer to pay a completed order again.
            state["status"] = "paid"
        return state
    finally:
        conn.close()
