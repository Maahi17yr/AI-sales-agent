"""Failure-injection tests: database errors must not expose secrets or claim success."""
import sqlite3
import unittest
from unittest.mock import patch

from core.services.checkout import CheckoutService
from core.services.order_service import OrderService


class BrokenConnection:
    def execute(self, *_args, **_kwargs):
        raise sqlite3.OperationalError("SECRET_DATABASE_URL_DO_NOT_LEAK")

    def rollback(self):
        raise sqlite3.OperationalError("SECRET_ROLLBACK_DO_NOT_LEAK")

    def close(self):
        pass


class CheckoutDatabaseFailureTests(unittest.TestCase):
    def test_prepare_database_and_rollback_failure_are_safe(self):
        with patch("core.services.checkout.get_connection", return_value=BrokenConnection()):
            result = CheckoutService("test-session").prepare_checkout(
                "Test", "08012345678", "Test street", "Ikeja")
        self.assertFalse(result["success"])
        self.assertNotIn("SECRET", str(result))

    def test_confirm_database_and_rollback_failure_are_safe(self):
        with patch("core.services.order_service.get_connection", return_value=BrokenConnection()):
            result = OrderService("test-session")._commit_approved_checkout("test-fingerprint")
        self.assertFalse(result["success"])
        self.assertNotIn("SECRET", str(result))


if __name__ == "__main__":
    unittest.main()
