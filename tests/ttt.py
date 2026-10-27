"""Run from project root: python tests/test_customized_checkout.py
Uses a disposable SQLite database, not database/smallchops.db.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if not (ROOT / 'database' / 'models.py').exists():
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

from database import models
from database.customization_schema import create_customization_tables
from services.cart import CartService
from services.checkout import CheckoutService
from services.order_service import fingerprint

SESSION = 'test-trusted-session'
PHONE = '08000000001'


def scalar(sql, params=()):
    conn = models.get_connection()
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def execute(sql, params=()):
    conn = models.get_connection()
    try:
        with conn:
            conn.execute(sql, params)
    finally:
        conn.close()


def setup(path):
    models.DB_PATH = path
    models.create_tables()
    create_customization_tables()
    conn = models.get_connection()
    try:
        with conn:
            conn.execute('INSERT INTO customers (name, phone) VALUES (?, ?)', ('Test Customer', PHONE))
            cid = conn.execute('SELECT id FROM customers WHERE phone=?', (PHONE,)).fetchone()[0]
            conn.execute('INSERT INTO products (name, category, price) VALUES (?, ?, ?)',
                         ('Test Jollof', 'Meals', 4500))
            pid = conn.execute('SELECT id FROM products WHERE name=?', ('Test Jollof',)).fetchone()[0]
            conn.execute('INSERT INTO inventory (product_id, variant, quantity) VALUES (?, ?, ?)',
                         (pid, 'standard', 10))
            conn.execute('INSERT INTO delivery_zones (area, fee, estimated_min_hours, estimated_max_hours) VALUES (?, ?, ?, ?)',
                         ('Ikeja', 1500, 1, 2))
            conn.execute('INSERT INTO ingredients (name, stock_quantity) VALUES (?, ?)', ('Boiled Egg', 10))
            conn.execute('INSERT INTO ingredients (name, stock_quantity) VALUES (?, ?)', ('Fried Plantain', 10))
            egg = conn.execute('SELECT id FROM ingredients WHERE name=?', ('Boiled Egg',)).fetchone()[0]
            plantain = conn.execute('SELECT id FROM ingredients WHERE name=?', ('Fried Plantain',)).fetchone()[0]
            conn.execute('INSERT INTO meal_ingredients (product_id, ingredient_id, removable) VALUES (?, ?, 1)', (pid, egg))
            conn.execute('INSERT INTO meal_addons (product_id, ingredient_id, extra_price, max_quantity) VALUES (?, ?, 1000, 2)', (pid, plantain))
            conn.execute('INSERT INTO conversation_sessions (session_id, customer_id, cart_data) VALUES (?, ?, ?)',
                         (SESSION, cid, json.dumps({'items': []})))
        return pid, egg, plantain
    finally:
        conn.close()


def prepare():
    result = CheckoutService(SESSION).prepare_checkout('Test Customer', PHONE, '12 Example Street', 'Ikeja')
    assert result['success'], result
    return fingerprint(result['summary'])


def confirm(fp, approved=True):
    return CheckoutService(SESSION).confirm_checkout(customer_approved=approved, reviewed_fingerprint=fp)


def main():
    original = models.DB_PATH
    try:
        with tempfile.TemporaryDirectory() as directory:
            pid, egg, plantain = setup(Path(directory) / 'customization_test.db')
            cart = CartService(SESSION)
            assert cart.add_item(pid, remove=['Boiled Egg'])['success']
            assert cart.add_item(pid, add={'Fried Plantain': 1})['success']
            lines = cart.view_cart()['items']
            assert len(lines) == 2 and lines[0]['modifications'] != lines[1]['modifications']
            print('PASS: Different customizations remain separate cart lines')

            assert not cart.add_item(pid, add={'Boiled Egg': 1})['success']
            assert not cart.add_item(pid, add={'Fried Plantain': 3})['success']
            print('PASS: Unapproved and excessive extras rejected')

            fp = prepare()
            assert not confirm(fp, approved=False)['success']
            assert not confirm('wrong-fingerprint')['success']
            assert scalar('SELECT COUNT(*) FROM orders') == 0
            print('PASS: Explicit approval and exact reviewed fingerprint required')

            execute('UPDATE meal_addons SET extra_price=1200 WHERE product_id=? AND ingredient_id=?', (pid, plantain))
            assert not confirm(fp)['success']
            assert scalar('SELECT COUNT(*) FROM orders') == 0
            print('PASS: Changed extra price requires a fresh review')

            fp = prepare()
            execute('UPDATE ingredients SET stock_quantity=0 WHERE id=?', (plantain,))
            assert not confirm(fp)['success']
            assert scalar('SELECT COUNT(*) FROM orders') == 0
            assert scalar('SELECT quantity FROM inventory WHERE product_id=?', (pid,)) == 10
            print('PASS: Ingredient shortage prevents order and stock deduction')

            execute('UPDATE ingredients SET stock_quantity=10 WHERE id=?', (plantain,))
            fp = prepare()
            result = confirm(fp)
            assert result['success'], result
            assert scalar('SELECT COUNT(*) FROM orders') == 1
            assert scalar('SELECT COUNT(*) FROM order_items') == 2
            assert scalar('SELECT COUNT(*) FROM order_item_customizations') == 2
            assert scalar('SELECT quantity FROM inventory WHERE product_id=?', (pid,)) == 8
            assert scalar('SELECT stock_quantity FROM ingredients WHERE id=?', (plantain,)) == 9
            assert scalar('SELECT stock_quantity FROM ingredients WHERE id=?', (egg,)) == 10
            print('PASS: Customizations persisted and correct stock deducted atomically')

            assert not confirm(fp)['success']
            assert scalar('SELECT COUNT(*) FROM orders') == 1
            print('PASS: Repeated confirmation cannot create another order')
            print('\nALL CUSTOMIZED CHECKOUT TESTS PASSED')
    finally:
        models.DB_PATH = original


if __name__ == '__main__':
    main()
