"""No-content metrics, connection semantics, and stream text timing tests."""
import io
import logging
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.observability import metrics


class MetricsTests(unittest.TestCase):
    def test_first_text_delta_excludes_lifecycle_and_tool_events(self):
        self.assertFalse(metrics.is_first_text_event({"type": "RUN_STARTED"}))
        self.assertFalse(metrics.is_first_text_event({"type": "TOOL_CALL_START"}))
        self.assertFalse(metrics.is_first_text_event({"type": "TEXT_MESSAGE_CONTENT", "delta": ""}))
        self.assertTrue(metrics.is_first_text_event(
            SimpleNamespace(type="TEXT_MESSAGE_CONTENT", delta="Fictional answer")))
        self.assertFalse(metrics.is_first_text_event({"type": "TEXT_MESSAGE_CONTENT"}))

    def test_sqlite_adapter_keeps_transactions_and_does_not_log_queries(self):
        with tempfile.TemporaryDirectory() as root:
            raw = sqlite3.connect(Path(root) / "fictional.db")
            conn = metrics.TimedSQLiteConnection(raw)
            logger = logging.getLogger("naija_bites.metrics")
            stream = io.StringIO()
            handler = logging.StreamHandler(stream)
            old_level = logger.level
            logger.setLevel(logging.INFO)
            logger.addHandler(handler)
            try:
                tokens = metrics.start_request_db_metrics()
                with conn:
                    conn.execute("CREATE TABLE fictional_data (secret TEXT)")
                    conn.execute("INSERT INTO fictional_data VALUES (?)",
                                 ("fictional-private-customer-data",))
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM fictional_data").fetchone()[0], 1)
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("SELECT * FROM nonexistent_secret_table")
                counts, elapsed = metrics.finish_request_db_metrics(tokens)
                self.assertEqual(counts["sqlite"], 4)
                self.assertGreaterEqual(elapsed, 0)
                self.assertIn("status=error", stream.getvalue())
                self.assertNotIn("fictional-private-customer-data", stream.getvalue())
                self.assertNotIn("nonexistent_secret_table", stream.getvalue())
                self.assertNotIn("SELECT", stream.getvalue())
            finally:
                logger.removeHandler(handler)
                handler.close()
                logger.setLevel(old_level)
                raw.close()

    def test_rag_metric_wraps_success_and_error_without_query_logging(self):
        from core.rag import retriever

        with patch.object(retriever, "_search_knowledge_base",
                          return_value={"success": True, "results": []}), \
             patch.object(retriever, "record_duration") as timer:
            self.assertTrue(retriever.search_knowledge_base("fictional-private-query")["success"])
            self.assertEqual(timer.call_args.args[:2], ("rag", "retrieve"))
            self.assertEqual(timer.call_args.args[3], "success")

        with patch.object(retriever, "_search_knowledge_base",
                          return_value={"success": False, "results": []}), \
             patch.object(retriever, "record_duration") as timer:
            self.assertFalse(retriever.search_knowledge_base("fictional-private-query")["success"])
            self.assertEqual(timer.call_args.args[3], "error")


if __name__ == "__main__":
    unittest.main()
