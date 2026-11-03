"""Persistent Strands conversation history for verified sessions.

Strands owns model/tool messages; SQLite continues to own carts, orders,
and the trusted session-to-customer binding.
"""
from pathlib import Path
import hashlib
import os

from data.database import repository

_DATA_DIR = os.getenv("NAIJA_BITES_DATA_DIR", "").strip()
MEMORY_DIR = (Path(_DATA_DIR).expanduser() / "sessions"
              if _DATA_DIR else Path(__file__).resolve().parent / "sessions")


def make_session_manager(session_id: str, thread_id: str | None = None):
    """Create a Strands file-backed manager for a verified customer and chat.

    The AG-UI thread ID is client-controlled. Use it only as data inside a
    server-scoped hash, never as a path or authorization credential.
    """
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("A trusted session ID is required.")
    session = repository.get_session(session_id)
    if not session or session.get("customer_id") is None:
        raise PermissionError("A verified customer session is required.")

    # Import lazily so unit tests can check authorization without AWS/Strands.
    from strands.session.file_session_manager import FileSessionManager

    scoped_id = session_id
    if thread_id is not None:
        if not isinstance(thread_id, str) or not 1 <= len(thread_id.strip()) <= 128:
            raise ValueError("A valid chat thread ID is required.")
        scoped_id = "chat-" + hashlib.sha256(
            (session_id + ":" + thread_id.strip()).encode("utf-8")
        ).hexdigest()

    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    return FileSessionManager(
        session_id=scoped_id,
        storage_dir=str(MEMORY_DIR),
    )
