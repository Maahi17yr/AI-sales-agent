"""Test memory session authorization without AWS or a database."""
import unittest
from unittest.mock import patch
from memory.session import make_session_manager

class MemoryAuthorizationTests(unittest.TestCase):
    def test_empty_session_rejected(self):
        with self.assertRaises(ValueError):
            make_session_manager("")

    def test_unknown_session_rejected(self):
        with patch("memory.session.repository.get_session", return_value=None):
            with self.assertRaises(PermissionError):
                make_session_manager("unknown-session")

    def test_unverified_session_rejected(self):
        with patch("memory.session.repository.get_session", return_value={"customer_id": None}):
            with self.assertRaises(PermissionError):
                make_session_manager("unverified-session")

if __name__ == "__main__":
    unittest.main()
