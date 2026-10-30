"""Read-only diagnosis of Naija Bites checkout state. Run from project root."""
import json
import os
from database.models import get_connection

session_id = os.getenv('NAIJA_BITES_DEV_SESSION_ID', 'naija-bites-local-dev-v1')
conn = get_connection()
try:
    row = conn.execute('''
        SELECT s.session_id, s.customer_id, s.cart_data, s.awaiting_confirmation,
               c.phone AS registered_phone
        FROM conversation_sessions s
        LEFT JOIN customers c ON c.id = s.customer_id
        WHERE s.session_id = ?
    ''', (session_id,)).fetchone()
    if row is None:
        print('SESSION NOT FOUND:', session_id)
    else:
        data = dict(row)
        cart = json.loads(data.pop('cart_data') or '{}')
        pending = cart.get('pending_checkout')
        print('SESSION:', json.dumps(data, indent=2, default=str))
        print('CART ITEMS:', json.dumps(cart.get('items', []), indent=2))
        print('PENDING CHECKOUT:', json.dumps(pending, indent=2))
        if not cart.get('items'):
            print('DIAGNOSIS: No cart items saved for the backend session.')
        elif not pending:
            print('DIAGNOSIS: Cart exists but no pending checkout was saved. Check the create_order tool call and its actual return value.')
        else:
            print('DIAGNOSIS: Pending checkout exists in this database. If /checkout says false, verify the running server uses this same database and session ID.')
finally:
    conn.close()
