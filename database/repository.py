
import json
import sqlite3
from datetime import datetime, timezone

try:
    from .models import get_connection
except ImportError:
    from models import get_connection


def to_dict(row):
    """Convert a SQLite row into a Python dictionary."""
    return dict(row) if row is not None else None


def to_list(rows):
    """Convert multiple SQLite rows into dictionaries."""
    return [dict(row) for row in rows]


# ==========================================
# 1. PRODUCT OPERATIONS
# ==========================================

def get_all_products():
    """Return all available products."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM products
            WHERE available = 1
            ORDER BY category, name
        """).fetchall()

    return to_list(rows)


def get_product(product_id):
    """Find one product by its ID."""
    with get_connection() as conn:
        row = conn.execute("""
            SELECT * FROM products
            WHERE id = ?
        """, (product_id,)).fetchone()

    return to_dict(row)


def search_products(query=None, category=None,
                    min_price=None, max_price=None):
    """Search products using optional filters."""

    sql = "SELECT * FROM products WHERE available = 1"
    params = []

    if query:
        sql += " AND LOWER(name) LIKE ?"
        params.append(f"%{query.lower()}%")

    if category:
        sql += " AND LOWER(category) = ?"
        params.append(category.lower())

    if min_price is not None:
        sql += " AND price >= ?"
        params.append(min_price)

    if max_price is not None:
        sql += " AND price <= ?"
        params.append(max_price)

    sql += " ORDER BY price ASC"

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()

    return to_list(rows)


def get_orderable_products(query=None, category=None, min_price=None, max_price=None):
    """Return listed products with at least one in-stock inventory variant.

    This is a recommendation-time snapshot, not a stock reservation.
    Checkout must independently validate stock in its transaction.
    """
    sql = """SELECT p.id, p.name, p.category, p.description, p.price
             FROM products AS p
             WHERE p.available = 1
               AND EXISTS (
                   SELECT 1 FROM inventory AS i
                   WHERE i.product_id = p.id AND i.quantity > 0
               )"""
    params = []
    if query is not None and str(query).strip():
        sql += " AND LOWER(p.name) LIKE ?"
        params.append(f"%{str(query).strip().lower()}%")
    if category is not None and str(category).strip():
        sql += " AND LOWER(p.category) = ?"
        params.append(str(category).strip().lower())
    if min_price is not None:
        sql += " AND p.price >= ?"
        params.append(min_price)
    if max_price is not None:
        sql += " AND p.price <= ?"
        params.append(max_price)
    sql += " ORDER BY p.category, p.name"
    with get_connection() as conn:
        return to_list(conn.execute(sql, params).fetchall())


# ==========================================
# 2. INVENTORY OPERATIONS
# ==========================================

def get_stock(product_id, variant="standard"):
    """Return the stock quantity for a product variant."""

    with get_connection() as conn:
        row = conn.execute("""
            SELECT quantity FROM inventory
            WHERE product_id = ? AND variant = ?
        """, (product_id, variant)).fetchone()

    return row["quantity"] if row else 0


def get_product_variants(product_id):
    """Return all inventory variants for a product."""

    with get_connection() as conn:
        rows = conn.execute("""
            SELECT variant, quantity
            FROM inventory
            WHERE product_id = ?
        """, (product_id,)).fetchall()

    return to_list(rows)


def update_stock(product_id, quantity, variant="standard"):
    """
    Set stock to a new quantity.
    Intended for trusted inventory administration.
    Customer-facing tools must not call this directly.
    """

    if not isinstance(quantity, int) or quantity < 0:
        raise ValueError("Stock must be a non-negative integer.")

    with get_connection() as conn:
        cursor = conn.execute("""
            UPDATE inventory
            SET quantity = ?
            WHERE product_id = ? AND variant = ?
        """, (quantity, product_id, variant))

        if cursor.rowcount == 0:
            raise ValueError("Inventory record not found.")

    return True


# ==========================================
# 3. CUSTOMER OPERATIONS
# ==========================================

def get_customer_by_phone(phone):
    """Find a customer using their phone number."""

    with get_connection() as conn:
        row = conn.execute("""
            SELECT * FROM customers
            WHERE phone = ?
        """, (phone,)).fetchone()

    return to_dict(row)


def save_customer(phone, name=None):
    """Create a customer or return an existing customer."""

    if not isinstance(phone, str) or not phone.strip():
        raise ValueError("A valid phone number is required.")

    phone = phone.strip()

    with get_connection() as conn:
        conn.execute("""
            INSERT INTO customers (phone, name)
            VALUES (?, ?)
            ON CONFLICT(phone) DO NOTHING
        """, (phone, name))

        row = conn.execute("""
            SELECT * FROM customers
            WHERE phone = ?
        """, (phone,)).fetchone()

    return to_dict(row)


# ==========================================
# 4. DELIVERY OPERATIONS
# ==========================================

def get_delivery_zone(area):
    """Find delivery information for an area."""

    with get_connection() as conn:
        row = conn.execute("""
            SELECT * FROM delivery_zones
            WHERE LOWER(area) = LOWER(?)
              AND available = 1
        """, (area,)).fetchone()

    return to_dict(row)


def get_all_delivery_zones():
    """Return all supported delivery areas."""

    with get_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM delivery_zones
            WHERE available = 1
            ORDER BY area
        """).fetchall()

    return to_list(rows)


# ==========================================
# 5. ORDER OPERATIONS
# ==========================================

def get_order(order_id):
    """Retrieve an order and its customer details."""

    with get_connection() as conn:
        row = conn.execute("""
            SELECT
                o.*,
                c.name AS customer_name,
                c.phone AS customer_phone,
                d.area AS delivery_area
            FROM orders o
            JOIN customers c ON o.customer_id = c.id
            JOIN delivery_zones d
                ON o.delivery_zone_id = d.id
            WHERE o.id = ?
        """, (order_id,)).fetchone()

    return to_dict(row)


def get_order_items(order_id):
    """Return all items belonging to an order."""

    with get_connection() as conn:
        rows = conn.execute("""
            SELECT
                oi.*,
                p.name AS product_name
            FROM order_items oi
            JOIN products p ON oi.product_id = p.id
            WHERE oi.order_id = ?
        """, (order_id,)).fetchall()

    return to_list(rows)


def get_customer_orders(customer_id):
    """Retrieve a customer's order history."""

    with get_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM orders
            WHERE customer_id = ?
            ORDER BY created_at DESC, id DESC
        """, (customer_id,)).fetchall()

    return to_list(rows)


def get_order_status(order_id):
    """Retrieve an order's delivery and payment status."""

    with get_connection() as conn:
        row = conn.execute("""
            SELECT
                o.id AS order_id,
                o.status AS order_status,
                o.total,
                o.created_at,
                p.status AS payment_status,
                p.payment_link
            FROM orders o
            LEFT JOIN payments p ON o.id = p.order_id
            WHERE o.id = ?
        """, (order_id,)).fetchone()

    return to_dict(row)


# Order creation will be implemented in
# services/order_service.py.
#
# That service must:
# - Verify customer confirmation
# - Recalculate prices and discounts
# - Check stock availability
# - Create the order and order items
# - Deduct inventory
# - Commit everything in one transaction


# ==========================================
# 6. PAYMENT OPERATIONS
# ==========================================

def get_payment(order_id):
    """Retrieve payment information for an order."""

    with get_connection() as conn:
        row = conn.execute("""
            SELECT * FROM payments
            WHERE order_id = ?
        """, (order_id,)).fetchone()

    return to_dict(row)


def get_payment_by_reference(reference):
    """Find a payment by its unique reference."""

    with get_connection() as conn:
        row = conn.execute("""
            SELECT * FROM payments
            WHERE payment_reference = ?
        """, (reference,)).fetchone()

    return to_dict(row)


# Payment creation and status updates will be
# handled by a trusted payment service.
# The LLM must never mark an order as paid.


# ==========================================
# 7. CONVERSATION SESSION OPERATIONS
# ==========================================

def get_session(session_id):
    """Retrieve a saved conversation session."""

    with get_connection() as conn:
        row = conn.execute("""
            SELECT * FROM conversation_sessions
            WHERE session_id = ?
        """, (session_id,)).fetchone()

    if row is None:
        return None

    session = to_dict(row)

    session["cart_data"] = json.loads(session["cart_data"])
    session["chat_history"] = json.loads(session["chat_history"])

    return session


def save_session(
    session_id,
    cart_data,
    chat_history,
    customer_id=None,
    awaiting_confirmation=False
):
    """Create or update a conversation session."""

    cart_json = json.dumps(cart_data)
    history_json = json.dumps(chat_history)

    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if customer_id is not None:
            existing = conn.execute(
                "SELECT customer_id FROM conversation_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if existing and existing["customer_id"] not in (None, customer_id):
                raise PermissionError("Session is bound to another customer.")
        conn.execute("""
            INSERT INTO conversation_sessions (
                session_id,
                customer_id,
                cart_data,
                chat_history,
                awaiting_confirmation,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)

            ON CONFLICT(session_id) DO UPDATE SET
                customer_id = COALESCE(
                    excluded.customer_id,
                    conversation_sessions.customer_id
                ),
                cart_data = excluded.cart_data,
                chat_history = excluded.chat_history,
                awaiting_confirmation =
                    excluded.awaiting_confirmation,
                updated_at = CURRENT_TIMESTAMP
        """, (
            session_id,
            customer_id,
            cart_json,
            history_json,
            int(awaiting_confirmation)
        ))

    return get_session(session_id)


def delete_session(session_id):
    """Delete a conversation session."""

    with get_connection() as conn:
        cursor = conn.execute("""
            DELETE FROM conversation_sessions
            WHERE session_id = ?
        """, (session_id,))

    return cursor.rowcount > 0


def bind_session_customer(session_id, customer_id):
    """Trusted application only: bind once; never silently switch owners.

    The application must establish customer identity before calling this.
    A phone number supplied in chat is NOT sufficient authentication.
    """
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("A valid session ID is required.")
    if type(customer_id) is not int or customer_id <= 0:
        raise ValueError("A valid customer ID is required.")

    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        customer = conn.execute(
            "SELECT id FROM customers WHERE id = ?", (customer_id,)
        ).fetchone()
        if customer is None:
            raise ValueError("Customer not found.")

        conn.execute("""
            INSERT INTO conversation_sessions (session_id, customer_id)
            VALUES (?, ?)
            ON CONFLICT(session_id) DO NOTHING
        """, (session_id, customer_id))

        session = conn.execute("""
            SELECT customer_id FROM conversation_sessions
            WHERE session_id = ?
        """, (session_id,)).fetchone()

        if session["customer_id"] is None:
            conn.execute("""
                UPDATE conversation_sessions SET customer_id = ?
                WHERE session_id = ? AND customer_id IS NULL
            """, (customer_id, session_id))
        elif session["customer_id"] != customer_id:
            raise PermissionError("Session is bound to another customer.")

    return True


def get_order_status_for_session(order_id, session_id):
    """Return order status only when this session belongs to its owner."""
    if type(order_id) is not int or order_id <= 0:
        return None
    if not isinstance(session_id, str) or not session_id.strip():
        return None

    with get_connection() as conn:
        row = conn.execute("""
            SELECT o.id AS order_id,
                   o.status AS order_status,
                   o.total,
                   o.created_at,
                   p.status AS payment_status,
                   p.payment_link
            FROM orders AS o
            JOIN conversation_sessions AS s
              ON s.customer_id = o.customer_id
            LEFT JOIN payments AS p ON p.order_id = o.id
            WHERE o.id = ? AND s.session_id = ?
        """, (order_id, session_id)).fetchone()

    return to_dict(row)


# ==========================================
# 8. SIMPLE DATABASE TEST
# ==========================================

def test_repository():
    """Test basic database read operations."""

    print("\nTESTING DATABASE REPOSITORY")
    print("-" * 40)

    products = get_all_products()
    print(f"Products found: {len(products)}")

    zones = get_all_delivery_zones()
    print(f"Delivery zones found: {len(zones)}")

    results = search_products(query="Jollof")
    print(f"Jollof search results: {len(results)}")

    if results:
        product = results[0]
        print(f"Product: {product['name']}")
        print(f"Price: NGN {product['price']:,}")
        print(f"Stock: {get_stock(product['id'])}")

    lekki = get_delivery_zone("Lekki Phase 1")

    if lekki:
        print(f"Lekki delivery fee: NGN {lekki['fee']:,}")

    print("\nRepository test completed!")


if __name__ == "__main__":
    test_repository()
