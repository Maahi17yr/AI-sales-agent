"""Unit tests for strict, privacy-safe correlation IDs."""
import unittest
from uuid import UUID, uuid4

from core.observability import correlation


class CorrelationTests(unittest.TestCase):
    def test_rejects_customer_text_and_nonrandom_identifiers(self):
        good = str(uuid4())
        self.assertEqual(correlation.validated_trace_id(good), good)
        for value in ("customer@example.test", "fictional-session-token",
                      str(UUID(int=0)), "DATABASE_URL=private", "", None):
            with self.subTest(value=value):
                result = correlation.validated_trace_id(value)
                self.assertEqual(UUID(result).version, 4)
                self.assertNotEqual(result, value)

    def test_context_reset(self):
        before = correlation.current_ids()
        token = correlation.bind(str(uuid4()), str(uuid4()))
        self.assertNotEqual(correlation.current_ids(), before)
        correlation.reset(token)
        self.assertEqual(correlation.current_ids(), before)


if __name__ == "__main__":
    unittest.main()
