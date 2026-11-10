"""Build a transactional SQLite FTS5 index from Markdown and text-based PDFs.

PDF pages are normalized to Markdown-style sections with page citations. The
retriever, FTS5 schema, and existing Markdown documents stay compatible.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import sqlite3
import unicodedata

from pypdf import PdfReader

BASE = Path(__file__).resolve().parent
KNOWLEDGE_DIR = BASE / "knowledge"
INDEX_PATH = BASE / "knowledge_index.db"

CHUNK_MAX_CHARS = 1500
CHUNK_OVERLAP_CHARS = 150
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 200
MAX_PDF_CHARS = 2_000_000


def _split_content(content: str) -> list[str]:
    """Bound long sections without losing text, with modest adjacent overlap."""
    if len(content) <= CHUNK_MAX_CHARS:
        return [content]
    parts = []
    start = 0
    while start < len(content):
        end = min(start + CHUNK_MAX_CHARS, len(content))
        if end < len(content):
            minimum = start + CHUNK_MAX_CHARS // 2
            for separator in ("\n\n", "\n", " "):
                boundary = content.rfind(separator, minimum, end)
                if boundary > minimum:
                    end = boundary
                    break
        part = content[start:end].strip()
        if part:
            parts.append(part)
        if end >= len(content):
            break
        next_start = max(start + 1, end - CHUNK_OVERLAP_CHARS)
        # Resume at a word boundary, not in the middle of a word.
        while next_start < end and next_start > 0 and not content[next_start - 1].isspace():
            next_start += 1
        start = next_start if next_start < end else end
        while start < len(content) and content[start].isspace():
            start += 1
    return parts


def split_markdown(text: str) -> list[dict[str, str]]:
    """Chunk by ## and ### headings; split long sections with bounded overlap."""
    title = "Document"
    section = "Overview"
    subsection = ""
    lines: list[str] = []
    chunks: list[dict[str, str]] = []

    def flush() -> None:
        content = "\n".join(lines).strip()
        if content:
            heading = " — ".join(p for p in (title, section, subsection) if p)
            for part in _split_content(content):
                chunks.append({"heading": heading, "content": part})

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("### "):
            flush()
            subsection = stripped[4:].strip()
            lines.clear()
        elif stripped.startswith("## "):
            flush()
            section = stripped[3:].strip()
            subsection = ""
            lines.clear()
        elif stripped.startswith("# "):
            flush()
            title = stripped[2:].strip()
            section = "Overview"
            subsection = ""
            lines.clear()
        else:
            lines.append(line)
    flush()
    return chunks


def _normalize_pdf_page(text: str) -> str:
    """Remove extraction noise while preserving line and paragraph boundaries."""
    text = unicodedata.normalize("NFKC", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = text.replace("\u00ad", "").replace("\x00", "")
    lines = []
    for raw in text.splitlines():
        line = re.sub(r"[ \t]+", " ", raw).strip()
        if line:
            lines.append(line)
        elif lines and lines[-1]:
            lines.append("")
    return "\n".join(lines).strip()


def _remove_repeated_margins(pages: list[str]) -> list[str]:
    """Conservatively omit short headers/footers repeated on >=80% of 3+ pages."""
    if len(pages) < 3:
        return pages
    line_sets = [[line for line in text.splitlines() if line.strip()] for text in pages]
    firsts = Counter(lines[0] for lines in line_sets if lines)
    lasts = Counter(lines[-1] for lines in line_sets if lines)

    def repeated(counts: Counter) -> set[str]:
        threshold = max(3, (len(pages) * 4 + 4) // 5)
        return {
            line for line, count in counts.items()
            if count >= threshold and 3 <= len(line) <= 100
        }

    headers, footers = repeated(firsts), repeated(lasts)
    cleaned = []
    for lines in line_sets:
        if lines and lines[0] in headers:
            lines = lines[1:]
        if lines and lines[-1] in footers:
            lines = lines[:-1]
        cleaned.append("\n".join(lines).strip())
    return cleaned


def pdf_to_markdown(path: Path) -> str:
    """Extract selectable PDF text as Markdown-like per-page sections.

    Fail closed on empty/scanned/mixed pages: silently losing a page can cause
    the assistant to invent or omit an important business policy. OCR is a
    separate, intentionally unsupported feature.
    """
    if path.stat().st_size > MAX_PDF_BYTES:
        raise ValueError(f"{path.name}: PDF exceeds the {MAX_PDF_BYTES // (1024 * 1024)} MB limit")
    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            raise ValueError(f"{path.name}: encrypted PDFs are not supported")
        page_count = len(reader.pages)
        if not 0 < page_count <= MAX_PDF_PAGES:
            raise ValueError(f"{path.name}: PDFs must contain 1–{MAX_PDF_PAGES} pages")
        pages: list[str] = []
        total_chars = 0
        for number, page in enumerate(reader.pages, start=1):
            text = _normalize_pdf_page(page.extract_text() or "")
            if not text:
                raise ValueError(
                    f"{path.name}: page {number} has no selectable text "
                    "(blank/scanned page; OCR or manual review required)"
                )
            total_chars += len(text)
            if total_chars > MAX_PDF_CHARS:
                raise ValueError(f"{path.name}: extracted text exceeds the size limit")
            pages.append(text)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"{path.name}: could not extract PDF text ({type(exc).__name__})") from exc

    pages = _remove_repeated_margins(pages)
    if any(not text.strip() for text in pages):
        raise ValueError(f"{path.name}: a page contained only repeating headers/footers")

    # Escape literal Markdown headings from PDFs so page boundaries remain
    # authoritative. Do not guess section headings from visual PDF typography.
    result = [f"# {path.stem}"]
    for number, text in enumerate(pages, start=1):
        safe_text = "\n".join(
            "\\" + line if line.startswith(("# ", "## ", "### ")) else line
            for line in text.splitlines()
        )
        result.extend([f"## Page {number}", safe_text])
    return "\n".join(result)


def build_index() -> dict[str, int]:
    docs = sorted(
        (path for path in KNOWLEDGE_DIR.iterdir()
         if path.is_file() and path.suffix.lower() in {".md", ".pdf"}),
        key=lambda path: path.name.lower(),
    ) if KNOWLEDGE_DIR.exists() else []
    if not docs:
        raise RuntimeError(f"No Markdown or PDF files in {KNOWLEDGE_DIR}")
    rows: list[tuple[str, str, str]] = []
    for doc in docs:
        text = pdf_to_markdown(doc) if doc.suffix.lower() == ".pdf" else doc.read_text(encoding="utf-8")
        for chunk in split_markdown(text):
            rows.append((doc.name, chunk["heading"], chunk["content"]))
    if not rows:
        raise RuntimeError("No indexable sections")

    # Build every document first. If any PDF is unreadable, leave the previous
    # working FTS5 index intact. Readers see old or new, never a partial index.
    conn = sqlite3.connect(INDEX_PATH, timeout=15)
    try:
        conn.execute("PRAGMA busy_timeout = 15000")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge "
            "USING fts5(source UNINDEXED, heading, content, tokenize='unicode61')"
        )
        conn.execute("DELETE FROM knowledge")
        conn.executemany(
            "INSERT INTO knowledge(source, heading, content) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"documents": len(docs), "sections": len(rows)}


if __name__ == "__main__":
    print(build_index())
