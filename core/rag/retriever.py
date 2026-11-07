"""SQLite FTS5 retrieval with lightweight query expansion and source citations."""
from __future__ import annotations
import re
import sqlite3
import time
from core.observability.metrics import record_duration
from .ingest import INDEX_PATH

STOP = set("a an the i me my we you your is are was were do does did can could would should what when where why how if and or to for of in on at with about please tell want need much have get give any there this that our their it".split())
EXPANSIONS = {
    "refund": ["refund", "returns"], "return": ["refund", "returns"],
    "money": ["refund", "payment"], "cancel": ["cancellation", "cancel"],
    "late": ["delivery", "delays"], "delay": ["delivery", "delays"],
    "deliver": ["delivery"], "shipping": ["delivery"],
    "party": ["birthday", "catering", "bulk"], "birthday": ["birthday", "bulk"],
    "discount": ["discount", "pricing"], "dash": ["discount", "negotiation"],
    "cheap": ["discount", "pricing"], "allergy": ["allergens", "ingredients"],
    "allergic": ["allergens", "ingredients"], "unpaid": ["unpaid", "payment"],
    "expire": ["expiry", "unpaid"], "closing": ["hours", "cut-off"],
}


def _terms(question: str) -> list[str]:
    words = re.findall(r"[^\W_]+", question.lower(), flags=re.UNICODE)
    out = [w for w in words if w not in STOP and len(w) > 1]
    for word in words:
        out.extend(EXPANSIONS.get(word, []))
    return list(dict.fromkeys(out))[:16]


def search_knowledge_base(query: str, limit: int = 5) -> dict:
    started = time.perf_counter()
    result = _search_knowledge_base(query, limit)
    record_duration("rag", "retrieve", (time.perf_counter() - started) * 1000,
                    "success" if result.get("success") else "error")
    return result


def _search_knowledge_base(query: str, limit: int = 5) -> dict:
    if not isinstance(query, str) or not query.strip():
        return {"success": False, "results": [], "message": "Provide a question."}
    if not INDEX_PATH.exists():
        return {"success": False, "results": [], "message": "Index missing. Run python -m core.rag.ingest."}
    terms = _terms(query)
    if not terms:
        return {"success": True, "results": [], "message": "No searchable terms."}
    match = " OR ".join('"' + w.replace('"', '""') + '"' for w in terms)
    try:
        conn = sqlite3.connect(f"file:{INDEX_PATH}?mode=ro", uri=True, timeout=15)
        try:
            conn.row_factory = sqlite3.Row
            hits = conn.execute(
                "SELECT source, heading, content, bm25(knowledge, 0.0, 3.0, 1.0) AS score "
                "FROM knowledge WHERE knowledge MATCH ? ORDER BY score LIMIT ?",
                (match, max(1, min(int(limit), 10))),
            ).fetchall()
        finally:
            conn.close()
    except (sqlite3.Error, ValueError) as exc:
        return {"success": False, "results": [], "message": f"Retrieval error: {exc}"}
    results = [{"source": r["source"], "heading": r["heading"], "content": r["content"]} for r in hits]
    return {"success": True, "query": query, "results": results, "message": f"Found {len(results)} sections. Results may be only loosely related; verify relevance before answering."}
