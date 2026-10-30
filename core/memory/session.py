"""Persistent Strands conversation history for verified sessions.

Strands owns model/tool messages; SQLite continues to own carts, orders,
and the trusted session-to-customer binding.
"""
from pathlib import Path
import os

from data.database import repository

_DATA_DIR = os.getenv("NAIJA_BITES_DATA_DIR", "").strip()
MEMORY_DIR = (Path(_DATA_DIR).expanduser() / "sessions"
              if _DATA_DIR else Path(__file__).resolve().parent / "sessions")


def make_session_manager(session_id: str):
    """Create a Strands file-backed session manager for an authorized session."""
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("A trusted session ID is required.")
    session = repository.get_session(session_id)
    if not session or session.get("customer_id") is None:
        raise PermissionError("A verified customer session is required.")

    # Import lazily so unit tests can check authorization without AWS/Strands.
    from strands.session.file_session_manager import FileSessionManager

    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    return FileSessionManager(
        session_id=session_id,
        storage_dir=str(MEMORY_DIR),
    )
