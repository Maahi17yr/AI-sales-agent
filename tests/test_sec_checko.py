"""Run from project root: python tests/test_secure_checkout.py
Uses a temporary SQLite database; never touches database/smallchops.db.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if not (ROOT / 'database' / 'models.py').exists():
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

from database import models
from services.checkout import CheckoutService
from services.order_service import fingerprint


def setup_db(path):
    models.DB_PATH = path
    models.create_tables()
    with models.get_connection() as conn:
        conn.execute("INSERT INTO customers (name, phone) VALUES (?, ?)", ('Test Customer', '08000000001'))
        customer_id = conn.execute('SELECT id FROM customers WHERE phone = ?', ('08000000001',)).fetchone()['id']
        conn.execute("INSERT INTO products (name, category, price, available) VALUES ('Jollof Rice', 'Meals', 2000, 1)")
        product_id = conn.execute("SELECT id FROM products WHERE name = 'Jollof Rice'").fetchone()['id']
        conn.execute('INSERT INTO inventory (product_id, variant, quantity) VALUES (?, ?, ?)', (product_id, 'standard', 10))
        conn.execute("INSERT INTO delivery_zones (area, fee, estimated_min_hours, estimated_max_hours, available) VALUES ('Ikeja', 500, 1, 2, 1)")
        import json
        conn.execute('INSERT INTO conversation_sessions (session_id, customer_id, cart_data) VALUES (?, ?, ?)',
                     ('trusted-test-session', customer_id, json.dumps({'items': [{'product_id': product_id, 'variant': 'standard', 'quantity': 2}]})))
    return product_id


def count_orders():
    with models.get_connection() as conn:
        return conn.execute('SELECT COUNT(*) AS n FROM orders').fetchone()['n']


def stock(product_id):
    with models.get_connection() as conn:
        return conn.execute('SELECT quantity FROM inventory WHERE product_id=? AND variant=?', (product_id, 'standard')).fetchone()['quantity']


def prepare(checkout):
    result = checkout.prepare_checkout('Test Customer', '08000000001', '12 Example Street', 'Ikeja')
    assert result['success'], result
    return fingerprint(result['summary'])


def main():
    original_path = models.DB_PATH
    try:
        with tempfile.TemporaryDirectory() as temp:
            product_id = setup_db(Path(temp) / 'secure_checkout_test.db')
            checkout = CheckoutService('trusted-test-session')
            fp = prepare(checkout)

            denied = checkout.confirm_checkout(customer_approved=False, reviewed_fingerprint=fp)
            assert not denied['success'] and count_orders() == 0
            print('PASS: No order without explicit approval')

            denied = checkout.confirm_checkout(customer_approved=True, reviewed_fingerprint='wrong-fingerprint')
            assert not denied['success'] and count_orders() == 0
            print('PASS: Mismatched reviewed summary denied')

            with models.get_connection() as conn:
                conn.execute('UPDATE products SET price = 2500 WHERE id=?', (product_id,))
            denied = checkout.confirm_checkout(customer_approved=True, reviewed_fingerprint=fp)
            assert not denied['success'] and count_orders() == 0 and stock(product_id) == 10
            print('PASS: Changed price requires a new review')

            fp = prepare(checkout)
            with models.get_connection() as conn:
                conn.execute('UPDATE inventory SET quantity = 1 WHERE product_id=?', (product_id,))
            denied = checkout.confirm_checkout(customer_approved=True, reviewed_fingerprint=fp)
            assert not denied['success'] and count_orders() == 0 and stock(product_id) == 1
            print('PASS: Insufficient stock rolls back order creation')

            with models.get_connection() as conn:
                conn.execute('UPDATE inventory SET quantity = 10 WHERE product_id=?', (product_id,))
            fp = prepare(checkout)
            result = checkout.confirm_checkout(customer_approved=True, reviewed_fingerprint=fp)
            assert result['success'], result
            assert count_orders() == 1 and stock(product_id) == 8
            again = checkout.confirm_checkout(customer_approved=True, reviewed_fingerprint=fp)
            assert not again['success'] and count_orders() == 1 and stock(product_id) == 8
            print('PASS: One-time confirmation creates exactly one order')
            print('\nALL SECURE CHECKOUT TESTS PASSED')
    finally:
        models.DB_PATH = original_path


if __name__ == '__main__':
    main()
