"""Local SQLite integration tests, without Bedrock or Windows temp-file locking."""
import sqlite3
import unittest
from unittest.mock import patch
from memory import preferences

class _NoClose:
    def __init__(self, conn): self.conn=conn
    def __getattr__(self, name): return getattr(self.conn, name)
    def close(self): pass
    def __enter__(self): return self.conn.__enter__()
    def __exit__(self, *args): return self.conn.__exit__(*args)

class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("CREATE TABLE customers(id INTEGER PRIMARY KEY)")
        self.db.executemany("INSERT INTO customers(id) VALUES (?)", [(1,), (2,)])
        # Use a shared in-memory DB, and don't let production helpers close it.
        
    def tearDown(self):
        self.db.close()

    def test_isolation_and_forget(self):
        with patch.object(preferences.repository, "get_session", side_effect=lambda s: {"customer_id": {"a": 1, "b": 2}.get(s)}), patch.object(preferences.repository, "get_connection", side_effect=lambda: _NoClose(self.db)):
            preferences.remember_preference("a", "spice_level", "mild")
            self.assertEqual(preferences.get_preferences("a"), {"spice_level": "mild"})
            self.assertEqual(preferences.get_preferences("b"), {})
            self.assertTrue(preferences.forget_preference("a", "spice_level"))
            self.assertEqual(preferences.get_preferences("a"), {})


    def test_request_requires_trusted_approval(self):
        with patch.object(preferences.repository, "get_session", return_value={"customer_id": 1}), patch.object(preferences.repository, "get_connection", side_effect=lambda: _NoClose(self.db)):
            proposal = preferences.request_preference("a", "spice_level", "mild")
            self.assertEqual(preferences.get_preferences("a"), {})
            with self.assertRaises(ValueError):
                preferences.resolve_preference("a", "wrong-id", True)
            result = preferences.resolve_preference("a", proposal["request_id"], True)
            self.assertTrue(result["saved"])
            self.assertEqual(preferences.get_preferences("a"), {"spice_level": "mild"})
            with self.assertRaises(ValueError):
                preferences.resolve_preference("a", proposal["request_id"], True)

    def test_reject_and_replace(self):
        with patch.object(preferences.repository, "get_session", return_value={"customer_id": 1}), patch.object(preferences.repository, "get_connection", side_effect=lambda: _NoClose(self.db)):
            first = preferences.request_preference("a", "spice_level", "mild")
            second = preferences.request_preference("a", "spice_level", "hot")
            with self.assertRaises(ValueError):
                preferences.resolve_preference("a", first["request_id"], True)
            preferences.resolve_preference("a", second["request_id"], False)
            self.assertEqual(preferences.get_preferences("a"), {})

    def test_unverified_denied(self):
        with patch.object(preferences.repository, "get_session", return_value=None):
            with self.assertRaises(PermissionError):
                preferences.get_preferences("unknown")

if __name__ == "__main__": unittest.main()
