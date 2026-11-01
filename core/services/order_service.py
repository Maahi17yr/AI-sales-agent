"""Application-only, atomic checkout with independently verified meal customization."""
import hashlib
import json
import os
import sqlite3
try:
    from psycopg import Error as PostgresError
except ImportError:  # SQLite-only local development
    PostgresError = sqlite3.Error
from collections import Counter

from core import config
from data.database.models import get_connection


def fingerprint(summary):
    return hashlib.sha256(json.dumps(summary, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate_delivery_phone(phone):
    """Basic format check; does not establish ownership of a phone number."""
    if not isinstance(phone, str):
        return False
    cleaned = phone.strip().replace(" ", "").replace("-", "")
    return (cleaned.startswith("+") and cleaned[1:].isdigit() and 10 <= len(cleaned[1:]) <= 15) or (cleaned.isdigit() and 10 <= len(cleaned) <= 15)


class OrderService:
    def __init__(self, session_id):
        self.session_id = session_id

    @staticmethod
    def _calculate_discount(subtotal):
        if not config.DISCOUNTS_ENABLED or subtotal < config.MIN_ORDER_FOR_DISCOUNT:
            return 0
        rate = 0
        for minimum, percentage in sorted(config.BULK_DISCOUNT_TIERS.items()):
            if subtotal >= minimum:
                rate = percentage
        return (subtotal * max(0, min(rate, config.MAX_DISCOUNT_PERCENT)) + 50) // 100

    @classmethod
    def _build_summary_in_transaction(cls, conn, session, details):
        if not session['customer_id']:
            raise ValueError('Session is not bound to a verified customer.')
        customer = conn.execute('SELECT id FROM customers WHERE id = ?',
                                (session['customer_id'],)).fetchone()
        if not customer:
            raise ValueError('Verified session customer no longer exists.')
        if not validate_delivery_phone(details.get('phone')):
            raise ValueError('Enter a valid delivery phone number.')
        zone = conn.execute('''SELECT * FROM delivery_zones
            WHERE LOWER(area) = LOWER(?) AND available = 1''',
            (details['delivery_area'],)).fetchone()
        if not zone:
            raise ValueError('Delivery is unavailable for this area.')
        cart = json.loads(session['cart_data'])
        raw_items = cart.get('items', [])
        if not isinstance(raw_items, list) or not raw_items:
            raise ValueError('The shopping cart is empty.')
        items, subtotal = [], 0
        meal_stock = Counter()
        ingredient_stock = Counter()
        for item in raw_items:
            if not isinstance(item, dict):
                raise ValueError('Invalid cart entry.')
            product_id, quantity = item.get('product_id'), item.get('quantity')
            variant = item.get('variant', 'standard')
            if (type(product_id) is not int or type(quantity) is not int or quantity <= 0
                    or not isinstance(variant, str) or not variant.strip()):
                raise ValueError('Invalid cart item.')
            product = conn.execute('SELECT id, name, price, available FROM products WHERE id = ?',
                                   (product_id,)).fetchone()
            if not product or not product['available']:
                raise ValueError('Meal is unavailable.')
            raw_mods = item.get('modifications', [])
            if not isinstance(raw_mods, list) or (raw_mods and variant != 'standard'):
                raise ValueError('Invalid customization format or variant.')
            seen, mods, extra_cost = set(), [], 0
            for mod in raw_mods:
                if not isinstance(mod, dict):
                    raise ValueError('Invalid customization entry.')
                action, ingredient_id, count = (mod.get('action'), mod.get('ingredient_id'),
                                                mod.get('quantity'))
                if (action not in ('add', 'remove') or type(ingredient_id) is not int
                        or type(count) is not int or count <= 0
                        or (action, ingredient_id) in seen):
                    raise ValueError('Invalid or duplicate customization.')
                seen.add((action, ingredient_id))
                if action == 'remove':
                    rule = conn.execute('''SELECT i.name, mi.included_quantity
                        FROM meal_ingredients mi JOIN ingredients i ON i.id = mi.ingredient_id
                        WHERE mi.product_id = ? AND mi.ingredient_id = ? AND mi.removable = 1''',
                        (product_id, ingredient_id)).fetchone()
                    if not rule or count != rule['included_quantity']:
                        raise ValueError('This removal is not permitted.')
                    delta = 0
                else:
                    rule = conn.execute('''SELECT i.name, i.available, ma.extra_price, ma.max_quantity
                        FROM meal_addons ma JOIN ingredients i ON i.id = ma.ingredient_id
                        WHERE ma.product_id = ? AND ma.ingredient_id = ?''',
                        (product_id, ingredient_id)).fetchone()
                    if not rule or not rule['available'] or count > rule['max_quantity']:
                        raise ValueError('This extra is not permitted or available.')
                    delta = rule['extra_price']
                    extra_cost += count * delta
                    ingredient_stock[ingredient_id] += count * quantity
                mods.append({'action': action, 'ingredient_id': ingredient_id,
                             'name': rule['name'], 'quantity': count,
                             'unit_price_delta': delta})
            mods.sort(key=lambda m: (m['action'], m['ingredient_id']))
            unit_price = product['price'] + extra_cost
            line_total = unit_price * quantity
            subtotal += line_total
            meal_stock[(product_id, variant)] += quantity
            items.append({'product_id': product_id, 'name': product['name'],
                          'variant': variant, 'quantity': quantity, 'modifications': mods,
                          'unit_price': unit_price, 'line_total': line_total})
        for (product_id, variant), needed in meal_stock.items():
            row = conn.execute('''SELECT quantity FROM inventory
                WHERE product_id = ? AND variant = ?''', (product_id, variant)).fetchone()
            if not row or row['quantity'] < needed:
                raise ValueError('Insufficient meal stock for the full order.')
        for ingredient_id, needed in ingredient_stock.items():
            row = conn.execute('''SELECT stock_quantity, available FROM ingredients
                WHERE id = ?''', (ingredient_id,)).fetchone()
            if not row or not row['available'] or row['stock_quantity'] < needed:
                raise ValueError('Insufficient ingredient stock for the full order.')
        discount = cls._calculate_discount(subtotal)
        summary = {'customer_name': details['customer_name'], 'phone': details['phone'],
                   'delivery_address': details['delivery_address'], 'delivery_area': zone['area'],
                   'items': items, 'subtotal': subtotal, 'discount': discount,
                   'delivery_fee': zone['fee'], 'total': subtotal + zone['fee'] - discount,
                   'currency': config.CURRENCY}
        return summary, zone, meal_stock, ingredient_stock

    def _commit_approved_checkout(self, approval_fingerprint, checkout_id=None):
        """Only trusted application code may call this after explicit customer approval."""
        conn = get_connection()
        try:
            conn.execute('BEGIN IMMEDIATE')
            # PostgreSQL needs an explicit row lock; SQLite's BEGIN IMMEDIATE
            # already serializes writers. This prevents double confirmation.
            lock_suffix = " FOR UPDATE" if os.getenv("DATABASE_URL", "").strip() else ""
            session = conn.execute(
                "SELECT customer_id, cart_data FROM conversation_sessions "
                "WHERE session_id = ?" + lock_suffix, (self.session_id,)
            ).fetchone()
            if not session:
                raise ValueError('Session not found.')
            cart = json.loads(session['cart_data'])
            pending = cart.get('pending_checkout')
            if not isinstance(pending, dict):
                # A lost HTTP response or double click must return the SAME
                # order, not create another or force a conversation restart.
                last = cart.get('last_checkout')
                if (checkout_id and isinstance(last, dict)
                        and last.get('checkout_id') == checkout_id
                        and last.get('fingerprint') == approval_fingerprint
                        and isinstance(last.get('result'), dict)):
                    order = conn.execute(
                        "SELECT status FROM orders WHERE id = ? AND customer_id = ?",
                        (last.get('order_id'), session['customer_id'])
                    ).fetchone()
                    if not order:
                        raise ValueError('Previous order no longer exists.')
                    replay = dict(last['result'])
                    replay['status'] = order['status']
                    if order['status'] != 'pending_payment':
                        replay['message'] = 'This order has already moved beyond payment.'
                    conn.commit()
                    return replay
                raise ValueError('No pending checkout. Refresh your order review.')
            if not isinstance(pending.get('summary'), dict):
                raise ValueError('Saved checkout summary is invalid.')
            if checkout_id is not None and pending.get('checkout_id') != checkout_id:
                raise ValueError('This review is outdated. Refresh your order review.')
            expected = pending.get('fingerprint')
            if not isinstance(expected, str) or expected != fingerprint(pending['summary']):
                raise ValueError('Saved checkout summary is invalid.')
            if approval_fingerprint != expected:
                raise ValueError('Approval does not match the reviewed order.')
            details = {key: pending['summary'][key] for key in
                       ('customer_name', 'phone', 'delivery_address', 'delivery_area')}
            summary, zone, meals, ingredients = self._build_summary_in_transaction(conn, session, details)
            if fingerprint(summary) != expected:
                raise ValueError('Order details changed. Review and approve a new summary.')
            cursor = conn.execute('''INSERT INTO orders
                (customer_id, delivery_zone_id, delivery_address, subtotal,
                 delivery_fee, discount, total, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_payment')''',
                (session['customer_id'], zone['id'], summary['delivery_address'],
                 summary['subtotal'], summary['delivery_fee'], summary['discount'], summary['total']))
            order_id = cursor.lastrowid
            for item in summary['items']:
                line = conn.execute('''INSERT INTO order_items
                    (order_id, product_id, variant, quantity, unit_price)
                    VALUES (?, ?, ?, ?, ?)''',
                    (order_id, item['product_id'], item['variant'], item['quantity'], item['unit_price']))
                for mod in item['modifications']:
                    conn.execute('''INSERT INTO order_item_customizations
                        (order_item_id, ingredient_id, action, quantity, unit_price_delta)
                        VALUES (?, ?, ?, ?, ?)''',
                        (line.lastrowid, mod['ingredient_id'], mod['action'],
                         mod['quantity'], mod['unit_price_delta']))
            for (product_id, variant), needed in meals.items():
                updated = conn.execute('''UPDATE inventory SET quantity = quantity - ?
                    WHERE product_id = ? AND variant = ? AND quantity >= ?''',
                    (needed, product_id, variant, needed))
                if updated.rowcount != 1:
                    raise ValueError('Meal stock changed during checkout.')
            for ingredient_id, needed in ingredients.items():
                updated = conn.execute('''UPDATE ingredients SET stock_quantity = stock_quantity - ?
                    WHERE id = ? AND available = 1 AND stock_quantity >= ?''',
                    (needed, ingredient_id, needed))
                if updated.rowcount != 1:
                    raise ValueError('Ingredient stock changed during checkout.')
            result = {'success': True, 'order_id': order_id, 'status': 'pending_payment',
                      'subtotal': summary['subtotal'], 'delivery_fee': summary['delivery_fee'],
                      'discount': summary['discount'], 'total': summary['total'],
                      'currency': summary['currency'], 'message': 'Order created successfully.'}
            # Keep the last confirmed checkout for refresh/payment recovery and
            # replay of the exact same confirmation request. A new cart remains empty.
            cart = {
                'items': [],
                'last_checkout': {
                    'checkout_id': pending.get('checkout_id'),
                    'fingerprint': expected,
                    'order_id': order_id,
                    'result': result,
                },
            }
            conn.execute('''UPDATE conversation_sessions
                SET cart_data = ?, awaiting_confirmation = 0, updated_at = CURRENT_TIMESTAMP
                WHERE session_id = ?''', (json.dumps(cart), self.session_id))
            conn.commit()
            return result
        except (ValueError, KeyError, TypeError, json.JSONDecodeError, sqlite3.Error, PostgresError) as error:
            conn.rollback()
            return {'success': False, 'message': str(error)}
        finally:
            conn.close()

    def create_order(self, *args, **kwargs):
        return {'success': False, 'message': 'Direct order creation is disabled. Use application-controlled checkout.'}
