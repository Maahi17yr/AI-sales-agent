"""Isolated PDF/Markdown RAG ingestion regressions (no production files or APIs)."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from core.rag import ingest
from core.rag import retriever


def write_pdf(path: Path, pages: list[str | None], *, encrypted: bool = False) -> None:
    """Create real, text-selectable PDF fixtures using only pypdf."""
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        if text is None:
            continue
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })
        commands = []
        for idx, line in enumerate(text.splitlines()):
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(
                f"BT /F1 12 Tf 40 {750 - idx * 20} Td ({escaped}) Tj ET"
            )
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("fictional-password")
    with path.open("wb") as destination:
        writer.write(destination)


class RagIngestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "knowledge"
        self.folder.mkdir()
        self.db = Path(self.temp.name) / "knowledge_index.db"
        for module, key, value in (
            (ingest, "KNOWLEDGE_DIR", self.folder),
            (ingest, "INDEX_PATH", self.db),
            (retriever, "INDEX_PATH", self.db),
        ):
            patcher = patch.object(module, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_markdown_legacy_headings_and_fts_query(self):
        (self.folder / "policy.md").write_text(
            "# Handbook\n## Delivery\n### Lagos Mainland\nLekki delivery takes 30 minutes.\n",
            encoding="utf-8",
        )
        stats = ingest.build_index()
        self.assertEqual(stats, {"documents": 1, "sections": 1})
        hits = retriever.search_knowledge_base("Lekki delivery")["results"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["source"], "policy.md")
        self.assertEqual(hits[0]["heading"], "Handbook — Delivery — Lagos Mainland")
        self.assertEqual(hits[0]["content"], "Lekki delivery takes 30 minutes.")

    def test_text_pdf_pages_are_searchable_with_page_citations(self):
        write_pdf(self.folder / "client-policy.pdf", [
            "Party jollof is available on pre-order.",
            "Bulk orders require at least twenty-four hours notice.",
        ])
        stats = ingest.build_index()
        self.assertEqual(stats, {"documents": 1, "sections": 2})
        results = retriever.search_knowledge_base("bulk orders")["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], "client-policy.pdf")
        self.assertIn("Page 2", results[0]["heading"])
        self.assertIn("twenty-four hours", results[0]["content"])

    def test_same_policy_in_pdf_and_markdown_is_retrievable(self):
        wording = "Customers can request catering for birthday parties."
        (self.folder / "catering.md").write_text(
            "# Catering\n## Requests\n" + wording, encoding="utf-8"
        )
        write_pdf(self.folder / "catering.pdf", [wording])
        self.assertEqual(ingest.build_index()["documents"], 2)
        results = retriever.search_knowledge_base("catering birthday")["results"]
        self.assertEqual({r["source"] for r in results}, {"catering.md", "catering.pdf"})
        for hit in results:
            self.assertIn(wording, hit["content"])

    def test_long_sections_have_bounded_overlapping_chunks(self):
        words = [f"policyword{i:04d}" for i in range(700)]
        chunks = ingest.split_markdown("# Guide\n## Rules\n" + " ".join(words))
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(0 < len(c["content"]) <= ingest.CHUNK_MAX_CHARS for c in chunks))
        self.assertTrue(all(c["heading"] == "Guide — Rules" for c in chunks))
        for word in words:
            self.assertTrue(any(word in c["content"] for c in chunks), word)
        for before, after in zip(chunks, chunks[1:]):
            self.assertTrue(set(before["content"].split()) & set(after["content"].split()))

    def test_normalizes_pdf_text_and_removes_repeated_margins(self):
        self.assertEqual(
            ingest._normalize_pdf_page("Oﬃce   policies\r\n\r\n  Return \u00adpolicy"),
            "Office policies\n\nReturn policy",
        )
        pages = [
            "Naija Bites\nOne jollof rice\nPage footer",
            "Naija Bites\nTwo meat pies\nPage footer",
            "Naija Bites\nThree drinks\nPage footer",
        ]
        self.assertEqual(
            ingest._remove_repeated_margins(pages),
            ["One jollof rice", "Two meat pies", "Three drinks"],
        )

    def test_broken_pdf_never_overwrites_previous_complete_index(self):
        (self.folder / "policy.md").write_text(
            "# Reference\n## Payments\nVerified payment status is authoritative.",
            encoding="utf-8",
        )
        ingest.build_index()
        (self.folder / "corrupt.pdf").write_bytes(b"%PDF-1.4 broken PDF")
        with self.assertRaisesRegex(ValueError, "could not extract"):
            ingest.build_index()
        hits = retriever.search_knowledge_base("verified payment")["results"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["source"], "policy.md")

    def test_blank_or_scanned_page_causes_explicit_failure(self):
        (self.folder / "policy.md").write_text(
            "# Policy\n## Opening\nOur store is open weekdays.", encoding="utf-8"
        )
        ingest.build_index()
        write_pdf(self.folder / "scanned.pdf", [
            "Deliveries are available in Ikeja.",
            None,
        ])
        with self.assertRaisesRegex(ValueError, "page 2 has no selectable text"):
            ingest.build_index()
        hits = retriever.search_knowledge_base("weekdays")["results"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["source"], "policy.md")

    def test_encrypted_pdf_is_rejected(self):
        write_pdf(self.folder / "encrypted.pdf", ["Confidential policy."], encrypted=True)
        with self.assertRaisesRegex(ValueError, "encrypted PDFs"):
            ingest.build_index()
        self.assertFalse(self.db.exists())

    def test_page_and_file_size_limits(self):
        write_pdf(self.folder / "large.pdf", ["Page one text.", "Page two text."])
        with patch.object(ingest, "MAX_PDF_PAGES", 1):
            with self.assertRaisesRegex(ValueError, "1 pages"):
                ingest.build_index()
        with patch.object(ingest, "MAX_PDF_BYTES", 50):
            with self.assertRaisesRegex(ValueError, "exceeds"):
                ingest.build_index()
        self.assertFalse(self.db.exists())

    def test_no_knowledge_files_does_not_create_index(self):
        with self.assertRaisesRegex(RuntimeError, "No Markdown or PDF"):
            ingest.build_index()
        self.assertFalse(self.db.exists())


if __name__ == "__main__":
    unittest.main()
