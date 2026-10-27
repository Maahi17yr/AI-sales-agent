import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from rag import ingest, retriever


class RAGTests(unittest.TestCase):
    def test_split_keeps_context(self):
        chunks = ingest.split_markdown("# Title\n## Policy\n### Refunds\nMoney back after review.\n")
        self.assertEqual(len(chunks), 1)
        self.assertIn("Policy", chunks[0]["heading"])
        self.assertIn("Refunds", chunks[0]["heading"])

    def test_index_and_search(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            knowledge = base / "knowledge"
            knowledge.mkdir()
            (knowledge / "policy.md").write_text("# Policy\n## Refunds\nRefund requests are reviewed.\n", encoding="utf-8")
            index = base / "index.db"
            with patch.object(ingest, "KNOWLEDGE_DIR", knowledge), patch.object(ingest, "INDEX_PATH", index), patch.object(retriever, "INDEX_PATH", index):
                stats = ingest.build_index()
                self.assertEqual(stats["documents"], 1)
                result = retriever.search_knowledge_base("refund")
                self.assertTrue(result["success"])
                self.assertEqual(result["results"][0]["source"], "policy.md")

    def test_empty_query(self):
        self.assertFalse(retriever.search_knowledge_base(" ")["success"])


if __name__ == "__main__":
    unittest.main()
