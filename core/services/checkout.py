"""Checkout preparation and trusted application-only confirmation."""
import json
import os
from uuid import uuid4
import sqlite3
try:
    from psycopg import Error as PostgresError
except ImportError:  # SQLite-only local development
    PostgresError = sqlite3.Error

from data.database.models import get_connection
from core.services.order_service import OrderService, fingerprint


class CheckoutService:
    def __init__(self, session_id):
        self.session_id = session_id

    def prepare_checkout(self, customer_name, phone, delivery_address, delivery_area):
        details = (customer_name, phone, delivery_address, delivery_area)
        if not all(isinstance(v, str) and v.strip() for v in details):
            return {'success': False, 'message': 'All customer and delivery details are required.'}
        fields = dict(zip(('customer_name', 'phone', 'delivery_address', 'delivery_area'),
                          (v.strip() for v in details)))
        conn = get_connection()
        try:
            conn.execute('BEGIN IMMEDIATE')
            # Match confirmation's row lock so a concurrent preparation cannot
            # resurrect an already-confirmed checkout on PostgreSQL.
            lock_suffix = " FOR UPDATE" if os.getenv("DATABASE_URL", "").strip() else ""
            session = conn.execute(
                "SELECT customer_id, cart_data FROM conversation_sessions "
                "WHERE session_id = ?" + lock_suffix, (self.session_id,)
            ).fetchone()
            if not session:
                raise ValueError('Customer session not found.')
            summary, _, _, _ = OrderService._build_summary_in_transaction(conn, session, fields)
            cart = json.loads(session['cart_data'])
            summary_fingerprint = fingerprint(summary)
            previous = cart.get('pending_checkout')
            # A retry for the same unchanged summary is the same review, not
            # a new order. Preserve its token to make confirmation idempotent.
            checkout_id = (previous.get('checkout_id')
                           if isinstance(previous, dict)
                           and previous.get('fingerprint') == summary_fingerprint
                           and isinstance(previous.get('checkout_id'), str)
                           else str(uuid4()))
            cart['pending_checkout'] = {
                'summary': summary, 'fingerprint': summary_fingerprint,
                'checkout_id': checkout_id,
            }
            conn.execute('''UPDATE conversation_sessions
                            SET cart_data = ?, awaiting_confirmation = 0,
                                updated_at = CURRENT_TIMESTAMP WHERE session_id = ?''',
                         (json.dumps(cart), self.session_id))
            conn.commit()
            return {'success': True, 'requires_confirmation': True,
                    'order_created': False, 'summary': summary, 'checkout_id': checkout_id,
                    'message': 'Please review and explicitly approve this order.'}
        except (ValueError, KeyError, TypeError, json.JSONDecodeError, sqlite3.Error, PostgresError) as error:
            conn.rollback()
            return {'success': False, 'message': str(error)}
        finally:
            conn.close()

    def confirm_checkout(self, customer_approved=False, reviewed_fingerprint=None, checkout_id=None):
        """APPLICATION-ONLY: call after a trusted UI/terminal confirmation event.

        The application must retain the fingerprint returned from the exact summary
        shown to the customer; never ask the LLM to supply this value.
        """
        if customer_approved is not True or not isinstance(reviewed_fingerprint, str):
            return {'success': False, 'message': 'Explicit approval of the reviewed order is required.'}
        return OrderService(self.session_id)._commit_approved_checkout(reviewed_fingerprint, checkout_id=checkout_id)
