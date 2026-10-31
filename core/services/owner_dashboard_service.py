"""Owner-facing business queries and controlled operations.

All calls must originate from an authenticated owner API. This module never
trusts client-supplied account IDs and never changes payment verification.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone

from data.database.models import get_connection


ORDER_NEXT = {
    "pending_payment": {"cancelled"},
    "confirmed": {"preparing"},
    "preparing": {"out_for_delivery"},
    "out_for_delivery": {"delivered"},
    "delivered": set(),
    "cancelled": set(),
}


def _rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def overview():
    today = datetime.now(timezone.utc).date()
    with closing(get_connection()) as conn:
        totals = conn.execute("""
            SELECT COUNT(*) AS order_count,
                   COALESCE(SUM(CASE WHEN status = 'pending_payment' THEN 1 ELSE 0 END), 0) AS pending_orders,
                   COALESCE(SUM(CASE WHEN status = 'confirmed' THEN 1 ELSE 0 END), 0) AS confirmed_orders
            FROM orders
        """).fetchone()
        # Revenue is recognized ONLY for payment rows independently verified as paid.
        revenue = conn.execute("""
            SELECT COALESCE(SUM(p.amount), 0) AS revenue
            FROM payments p WHERE p.status = 'paid'
        """).fetchone()["revenue"]
        paid_today = conn.execute("""
            SELECT COALESCE(SUM(p.amount), 0) AS revenue
            FROM payments p
            WHERE p.status = 'paid' AND p.paid_at LIKE ?
        """, (f"{today.isoformat()}%",)).fetchone()["revenue"]
        popular = _rows(conn, """
            SELECT pr.name, SUM(oi.quantity) AS quantity
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            JOIN payments pay ON pay.order_id = o.id AND pay.status = 'paid'
            JOIN products pr ON pr.id = oi.product_id
            GROUP BY pr.id, pr.name ORDER BY quantity DESC, pr.name LIMIT 5
        """)
        low_stock = _rows(conn, """
            SELECT p.id AS product_id, p.name, i.variant, i.quantity
            FROM inventory i JOIN products p ON p.id = i.product_id
            WHERE p.available = 1 AND i.quantity <= 5
            ORDER BY i.quantity, p.name LIMIT 10
        """)
        trend = []
        for offset in range(6, -1, -1):
            day = (today - timedelta(days=offset)).isoformat()
            amount = conn.execute("""
                SELECT COALESCE(SUM(amount), 0) AS revenue
                FROM payments WHERE status = 'paid' AND paid_at LIKE ?
            """, (f"{day}%",)).fetchone()["revenue"]
            trend.append({"date": day, "revenue": amount})
    return {
        "order_count": totals["order_count"],
        "pending_orders": totals["pending_orders"],
        "confirmed_orders": totals["confirmed_orders"],
        "paid_revenue": revenue,
        "paid_revenue_today": paid_today,
        "popular_meals": popular,
        "low_stock": low_stock,
        "sales_trend": trend,
        "currency": "NGN",
    }


def list_orders(limit=50):
    with closing(get_connection()) as conn:
        return _rows(conn, """
            SELECT o.id, o.customer_id, c.name AS customer_name,
                   o.status, o.total, o.created_at, o.delivery_address,
                   d.area AS delivery_area, COALESCE(p.status, 'unpaid') AS payment_status
            FROM orders o JOIN customers c ON c.id = o.customer_id
            JOIN delivery_zones d ON d.id = o.delivery_zone_id
            LEFT JOIN payments p ON p.order_id = o.id
            ORDER BY o.created_at DESC, o.id DESC LIMIT ?
        """, (limit,))


def list_menu():
    with closing(get_connection()) as conn:
        products = _rows(conn, """
            SELECT id, name, category, description, price, available, preparation_minutes
            FROM products ORDER BY category, name
        """)
        stock = _rows(conn, """
            SELECT product_id, variant, quantity FROM inventory ORDER BY variant
        """)
    by_product = {}
    for row in stock:
        by_product.setdefault(row["product_id"], []).append({
            "variant": row["variant"], "quantity": row["quantity"]
        })
    for product in products:
        product["variants"] = by_product.get(product["id"], [])
        product["available"] = bool(product["available"])
    return products


def list_customers(limit=100):
    with closing(get_connection()) as conn:
        return _rows(conn, """
            SELECT c.id, c.name, c.email, c.phone, c.created_at,
                   COUNT(o.id) AS order_count,
                   COALESCE(SUM(CASE WHEN p.status = 'paid' THEN o.total ELSE 0 END), 0) AS paid_spend
            FROM customers c
            LEFT JOIN orders o ON o.customer_id = c.id
            LEFT JOIN payments p ON p.order_id = o.id
            WHERE c.role = 'customer'
            GROUP BY c.id, c.name, c.email, c.phone, c.created_at
            ORDER BY c.created_at DESC, c.id DESC LIMIT ?
        """, (limit,))


def update_order_status(owner, order_id, next_status):
    if next_status not in ORDER_NEXT:
        raise ValueError("Invalid order status.")
    with closing(get_connection()) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            order = conn.execute("""
                SELECT o.status, COALESCE(p.status, 'unpaid') AS payment_status
                FROM orders o LEFT JOIN payments p ON p.order_id = o.id
                WHERE o.id = ?
            """, (order_id,)).fetchone()
            if order is None:
                raise LookupError("Order not found.")
            if next_status not in ORDER_NEXT[order["status"]]:
                raise ValueError("Invalid order status transition.")
            if next_status != "cancelled" and order["payment_status"] != "paid":
                raise ValueError("Cannot advance an order without verified payment.")
            updated = conn.execute(
                "UPDATE orders SET status = ? WHERE id = ? AND status = ?",
                (next_status, order_id, order["status"]),
            )
            if updated.rowcount != 1:
                raise ValueError("Order changed; refresh and try again.")
            conn.execute("""
                INSERT INTO owner_audit_log(actor, action, target_customer_id)
                VALUES(?, ?, ?)
            """, (owner["email"], f"order:{order_id}:{order['status']}->{next_status}", owner["id"]))
            conn.commit()
            return {"id": order_id, "status": next_status}
        except Exception:
            conn.rollback()
            raise


def update_product(owner, product_id, *, price=None, available=None):
    if price is None and available is None:
        raise ValueError("Specify a price or availability change.")
    if price is not None and (type(price) is not int or price < 0 or price > 100000000):
        raise ValueError("Price must be a valid non-negative amount in NGN.")
    if available is not None and type(available) is not bool:
        raise ValueError("Availability must be true or false.")
    with closing(get_connection()) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            product = conn.execute("SELECT id, price, available FROM products WHERE id = ?", (product_id,)).fetchone()
            if product is None:
                raise LookupError("Product not found.")
            new_price = product["price"] if price is None else price
            new_available = product["available"] if available is None else int(available)
            conn.execute("UPDATE products SET price = ?, available = ? WHERE id = ?",
                         (new_price, new_available, product_id))
            conn.execute("""
                INSERT INTO owner_audit_log(actor, action, target_customer_id)
                VALUES(?, ?, ?)
            """, (owner["email"], f"product:{product_id}:price={product['price']}->{new_price}:available={product['available']}->{new_available}", owner["id"]))
            conn.commit()
            return {"id": product_id, "price": new_price, "available": bool(new_available)}
        except Exception:
            conn.rollback()
            raise
