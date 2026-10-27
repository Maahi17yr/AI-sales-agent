"""Test customer/order isolation without modifying the project's real database."""
import tempfile
from pathlib import Path

from database import models, repository

with tempfile.TemporaryDirectory() as temp_dir:
    original_path = models.DB_PATH
    models.DB_PATH = Path(temp_dir) / 'authorization_test.db'
    try:
        models.create_tables()
        with models.get_connection() as conn:
            conn.execute("INSERT INTO customers (id, name, phone) VALUES (1, 'Test A', '08000000001')")
            conn.execute("INSERT INTO customers (id, name, phone) VALUES (2, 'Test B', '08000000002')")
            conn.execute("INSERT INTO delivery_zones (id, area, fee, estimated_min_hours, estimated_max_hours) VALUES (1, 'Test Area', 500, 1, 2)")
            conn.execute("INSERT INTO orders (id, customer_id, delivery_zone_id, delivery_address, subtotal, delivery_fee, discount, total) VALUES (101, 1, 1, 'Test Address A', 1000, 500, 0, 1500)")
            conn.execute("INSERT INTO orders (id, customer_id, delivery_zone_id, delivery_address, subtotal, delivery_fee, discount, total) VALUES (102, 2, 1, 'Test Address B', 2000, 500, 0, 2500)")

        assert repository.bind_session_customer('session-A', 1)
        assert repository.bind_session_customer('session-B', 2)
        assert repository.get_order_status_for_session(101, 'session-A') is not None
        assert repository.get_order_status_for_session(102, 'session-B') is not None
        print('PASS: Customers can view their own orders')

        assert repository.get_order_status_for_session(102, 'session-A') is None
        assert repository.get_order_status_for_session(101, 'session-B') is None
        print('PASS: Cross-customer order access is denied')

        assert repository.get_order_status_for_session(101, 'unknown-session') is None
        print('PASS: Unknown sessions cannot view orders')

        try:
            repository.bind_session_customer('session-A', 2)
        except PermissionError:
            print('PASS: Session owner cannot be switched')
        else:
            raise AssertionError('Session reassignment was allowed')

        try:
            repository.save_session('session-A', {'items': []}, [], customer_id=2)
        except PermissionError:
            print('PASS: save_session cannot switch the owner')
        else:
            raise AssertionError('save_session reassignment was allowed')

        print('\nALL AUTHORIZATION TESTS PASSED')
    finally:
        models.DB_PATH = original_path
