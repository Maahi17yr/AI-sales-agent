"""Build a transactional SQLite FTS5 index of Markdown policy sections."""
from __future__ import annotations
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent
KNOWLEDGE_DIR = BASE / "knowledge"
INDEX_PATH = BASE / "knowledge_index.db"


def split_markdown(text: str) -> list[dict[str, str]]:
    """Chunk by ## and ### headings, retaining the parent document/section."""
    title = "Document"
    section = "Overview"
    subsection = ""
    lines: list[str] = []
    chunks: list[dict[str, str]] = []

    def flush() -> None:
        content = "\n".join(lines).strip()
        if content:
            heading = " — ".join(p for p in (title, section, subsection) if p)
            chunks.append({"heading": heading, "content": content})

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("### "):
            flush()
            subsection = stripped[4:].strip()
            lines = []
        elif stripped.startswith("## "):
            flush()
            section = stripped[3:].strip()
            subsection = ""
            lines = []
        elif stripped.startswith("# "):
            flush()
            title = stripped[2:].strip()
            section = "Overview"
            subsection = ""
            lines = []
        else:
            lines.append(line)
    flush()
    return chunks


def build_index() -> dict[str, int]:
    docs = sorted(KNOWLEDGE_DIR.glob("*.md"))
    if not docs:
        raise RuntimeError(f"No Markdown files in {KNOWLEDGE_DIR}")
    rows: list[tuple[str, str, str]] = []
    for doc in docs:
        for chunk in split_markdown(doc.read_text(encoding="utf-8")):
            rows.append((doc.name, chunk["heading"], chunk["content"]))
    if not rows:
        raise RuntimeError("No indexable sections")

    # Update the existing index inside one transaction. This avoids Windows
    # os.replace / WinError 32 failures when another process has the DB open.
    # Readers see the previous complete index until the transaction commits.
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
