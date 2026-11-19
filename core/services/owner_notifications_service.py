"""Owner notifications derived exclusively from persisted business events.

Order creation, verified payments and prepared-stock movements are real database
records. Read state is persisted per owner; no synthetic activity is generated.
"""
from contextlib import closing
from datetime import datetime, timezone

from data.database.models import get_connection

MAX_ITEMS = 100


def _event(key, category, title, detail, created_at, target=None):
    return {
        "key": key,
        "category": category,
        "title": title,
        "detail": detail,
        "created_at": str(created_at),
        "target": target,
    }


def list_notifications(owner_id, limit=50):
    if type(owner_id) is not int or owner_id < 1:
        raise ValueError("Invalid owner.")
    limit = max(1, min(int(limit), MAX_ITEMS))
    with closing(get_connection()) as conn:
        order_rows = conn.execute("""
            SELECT o.id, o.status, o.created_at
            FROM orders o ORDER BY o.id DESC LIMIT ?
        """, (MAX_ITEMS,)).fetchall()
        payment_rows = conn.execute("""
            SELECT p.id, p.order_id, p.amount, p.paid_at
            FROM payments p
            WHERE p.status = 'paid' AND p.paid_at IS NOT NULL
            ORDER BY p.id DESC LIMIT ?
        """, (MAX_ITEMS,)).fetchall()
        stock_rows = conn.execute("""
            SELECT m.id, m.product_id, m.variant, m.action, m.change_units,
                   m.after_units, m.created_at, p.name
            FROM prepared_stock_movements m
            JOIN products p ON p.id = m.product_id
            ORDER BY m.id DESC LIMIT ?
        """, (MAX_ITEMS,)).fetchall()
        deduction_rows = conn.execute("""
            SELECT d.order_id, d.product_id, d.variant, d.quantity_units,
                   d.after_units, d.created_at, p.name
            FROM prepared_order_deductions d
            JOIN products p ON p.id = d.product_id
            ORDER BY d.created_at DESC LIMIT ?
        """, (MAX_ITEMS,)).fetchall()

        events = []
        for row in order_rows:
            events.append(_event(
                f"order:{row['id']}", "order", f"New order #{row['id']}",
                f"Order received · {row['status'].replace('_', ' ')}",
                row["created_at"], "orders"))
        for row in payment_rows:
            events.append(_event(
                f"payment:{row['id']}", "payment",
                f"Payment verified for order #{row['order_id']}",
                f"NGN {row['amount']:,} verified paid",
                row["paid_at"], "orders"))
        for row in stock_rows:
            events.append(_event(
                f"stock:{row['id']}", "stock",
                f"Stock {row['action']}: {row['name']}",
                f"{row['variant']} · {row['change_units']:+} units · {row['after_units']} remaining",
                row["created_at"], "prepared"))
        for row in deduction_rows:
            events.append(_event(
                f"deduction:{row['order_id']}:{row['product_id']}:{row['variant']}",
                "stock", f"Prepared stock used: {row['name']}",
                f"Order #{row['order_id']} · {row['variant']} · {row['quantity_units']} units used",
                row["created_at"], "prepared"))
        events.sort(key=lambda event: (event["created_at"], event["key"]), reverse=True)
        events = events[:limit]
        if not events:
            return {"items": [], "unread_count": 0}
        keys = [event["key"] for event in events]
        marks = ",".join("?" for _ in keys)
        rows = conn.execute(
            f"SELECT event_key FROM owner_notification_reads WHERE owner_customer_id = ? AND event_key IN ({marks})",
            (owner_id, *keys),
        ).fetchall()
        read_keys = {row["event_key"] for row in rows}
    for event in events:
        event["read"] = event["key"] in read_keys
    return {"items": events, "unread_count": sum(not event["read"] for event in events)}


def set_read(owner_id, event_key, read=True):
    if type(read) is not bool or not isinstance(event_key, str) or len(event_key) > 240:
        raise ValueError("Invalid notification action.")
    known = {e["key"] for e in list_notifications(owner_id, MAX_ITEMS)["items"]}
    if event_key not in known:
        raise LookupError("Notification not found.")
    with closing(get_connection()) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            if read:
                conn.execute("""
                    INSERT INTO owner_notification_reads(owner_customer_id, event_key)
                    VALUES (?, ?)
                    ON CONFLICT(owner_customer_id, event_key)
                    DO UPDATE SET read_at = CURRENT_TIMESTAMP
                """, (owner_id, event_key))
            else:
                conn.execute("""
                    DELETE FROM owner_notification_reads
                    WHERE owner_customer_id = ? AND event_key = ?
                """, (owner_id, event_key))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {"key": event_key, "read": read}


def mark_all_read(owner_id):
    events = list_notifications(owner_id, MAX_ITEMS)["items"]
    with closing(get_connection()) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            for event in events:
                conn.execute("""
                    INSERT INTO owner_notification_reads(owner_customer_id, event_key)
                    VALUES (?, ?)
                    ON CONFLICT(owner_customer_id, event_key)
                    DO UPDATE SET read_at = CURRENT_TIMESTAMP
                """, (owner_id, event["key"]))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {"marked": len(events)}
