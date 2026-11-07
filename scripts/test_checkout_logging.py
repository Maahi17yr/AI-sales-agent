"""Checkout tool logging must not expose trusted session or delivery details."""
import contextlib
import io
import logging
import unittest
from unittest.mock import patch

from core.tools import orders


class CheckoutLoggingTests(unittest.TestCase):
    def test_checkout_success_and_failure_do_not_log_customer_details(self):
        # Bypass the Strands decorator only for this isolated unit test.
        # These are fictional sentinel values, never live customer information.
        session = "fictional-session-sensitive-sentinel"
        address = "Fictional Sensitive Street"
        area = "Fictional Sensitive Area"
        stdout, stderr, logs = io.StringIO(), io.StringIO(), io.StringIO()
        logger = logging.getLogger("core.tools.orders")
        handler = logging.StreamHandler(logs)
        logger.addHandler(handler)
        try:
            with patch.object(orders, "tool", lambda fn: fn), \
                 patch.object(orders.repository, "get_session", return_value={"customer_id": 7}), \
                 patch.object(orders, "CheckoutService") as checkout, \
                 contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                checkout.return_value.prepare_checkout.return_value = {
                    "success": True, "summary": {"total": 100},
                }
                create_order, _ = orders.make_order_tools(session)
                successful = create_order(
                    "Fictional Customer", "08012345678", address, area
                )
                self.assertTrue(successful["success"])
                checkout.return_value.prepare_checkout.side_effect = ValueError("fictional failure")
                failed = create_order(
                    "Fictional Customer", "08012345678", address, area
                )
                self.assertFalse(failed["success"])
        finally:
            logger.removeHandler(handler)
            handler.close()
        captured = stdout.getvalue() + stderr.getvalue() + logs.getvalue()
        for private_value in (session, address, area, "Fictional Customer", "08012345678"):
            self.assertNotIn(private_value, captured)
        self.assertNotIn("[CHECKOUT DEBUG]", captured)


if __name__ == "__main__":
    unittest.main()
