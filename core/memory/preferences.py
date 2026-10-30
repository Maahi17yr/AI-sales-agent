"""Customer-scoped, opt-in food preferences. Never stores orders or payment data."""
from data.database import repository
import secrets
import hashlib
import json
from datetime import datetime, timezone, timedelta

ALLOWED_KEYS = {"spice_level", "favorite_food", "disliked_food", "dietary_preference", "food_allergy", "other_food_preference"}
MAX_VALUE_LENGTH = 240


def _customer_id(session_id):
    if not isinstance(session_id, str) or not session_id.strip():
        raise PermissionError("Verified session required")
    session = repository.get_session(session_id)
    if not session or session.get("customer_id") is None:
        raise PermissionError("Verified customer session required")
    return int(session["customer_id"])


def _ensure_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS customer_preferences (
        customer_id INTEGER NOT NULL,
        preference_key TEXT NOT NULL,
        preference_value TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(customer_id, preference_key),
        FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE CASCADE
    )""")


def _ensure_pending_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS pending_customer_preferences (
        session_id TEXT PRIMARY KEY,
        customer_id INTEGER NOT NULL,
        preference_key TEXT NOT NULL,
        preference_value TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE CASCADE
    )""")


def request_preference(session_id, key, value):
    customer_id = _customer_id(session_id)
    if key not in ALLOWED_KEYS:
        raise ValueError("Unsupported food preference category")
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > MAX_VALUE_LENGTH:
        raise ValueError("Preference must contain 1–240 characters")
    value = value.strip()
    now = datetime.now(timezone.utc)
    # A fresh random identifier binds the UI approval to the exact proposal.
    request_hash = secrets.token_hex(24)
    conn = repository.get_connection()
    try:
        with conn:
            _ensure_pending_schema(conn)
            conn.execute("""INSERT INTO pending_customer_preferences
                (session_id, customer_id, preference_key, preference_value, request_hash, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                customer_id=excluded.customer_id,
                preference_key=excluded.preference_key,
                preference_value=excluded.preference_value,
                request_hash=excluded.request_hash,
                created_at=excluded.created_at,
                expires_at=excluded.expires_at""",
                (session_id, customer_id, key, value, request_hash,
                 now.isoformat(), (now + timedelta(minutes=15)).isoformat()))
    finally:
        conn.close()
    return {"key": key, "value": value, "request_id": request_hash, "expires_in_minutes": 15}


def get_pending_preference(session_id):
    customer_id = _customer_id(session_id)
    conn = repository.get_connection()
    try:
        _ensure_pending_schema(conn)
        row = conn.execute("""SELECT preference_key, preference_value, request_hash, expires_at
            FROM pending_customer_preferences WHERE session_id=? AND customer_id=?""",
            (session_id, customer_id)).fetchone()
        if row is None:
            return None
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
            return None
        return {"key": row["preference_key"], "value": row["preference_value"],
                "request_id": row["request_hash"]}
    finally:
        conn.close()


def resolve_preference(session_id, request_id, approved):
    customer_id = _customer_id(session_id)
    if not isinstance(approved, bool) or not isinstance(request_id, str):
        raise ValueError("Explicit approval decision and request ID required")
    conn = repository.get_connection()
    try:
        with conn:
            _ensure_schema(conn)
            _ensure_pending_schema(conn)
            row = conn.execute("""SELECT preference_key, preference_value, request_hash, expires_at
                FROM pending_customer_preferences WHERE session_id=? AND customer_id=?""",
                (session_id, customer_id)).fetchone()
            if row is None or not secrets.compare_digest(row["request_hash"], request_id):
                raise ValueError("No matching pending preference request")
            conn.execute("DELETE FROM pending_customer_preferences WHERE session_id=? AND customer_id=?",
                         (session_id, customer_id))
            if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
                raise ValueError("Preference request expired; please request it again")
            if approved:
                conn.execute("""INSERT INTO customer_preferences(customer_id, preference_key, preference_value)
                    VALUES (?, ?, ?) ON CONFLICT(customer_id, preference_key)
                    DO UPDATE SET preference_value=excluded.preference_value, updated_at=CURRENT_TIMESTAMP""",
                    (customer_id, row["preference_key"], row["preference_value"]))
            return {"saved": approved, "key": row["preference_key"], "value": row["preference_value"]}
    finally:
        conn.close()


def get_preferences(session_id):
    customer_id = _customer_id(session_id)
    conn = repository.get_connection()
    try:
        _ensure_schema(conn)
        rows = conn.execute("SELECT preference_key, preference_value FROM customer_preferences WHERE customer_id = ? ORDER BY preference_key", (customer_id,)).fetchall()
        return {row["preference_key"]: row["preference_value"] for row in rows}
    finally:
        conn.close()


def remember_preference(session_id, key, value):
    customer_id = _customer_id(session_id)
    if key not in ALLOWED_KEYS:
        raise ValueError("Unsupported food preference category")
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > MAX_VALUE_LENGTH:
        raise ValueError("Preference must contain 1–240 characters")
    conn = repository.get_connection()
    try:
        with conn:
            _ensure_schema(conn)
            conn.execute("""INSERT INTO customer_preferences(customer_id, preference_key, preference_value)
                VALUES (?, ?, ?) ON CONFLICT(customer_id, preference_key)
                DO UPDATE SET preference_value=excluded.preference_value, updated_at=CURRENT_TIMESTAMP""",
                (customer_id, key, value.strip()))
    finally:
        conn.close()
    return {"key": key, "value": value.strip()}


def forget_preference(session_id, key):
    customer_id = _customer_id(session_id)
    if key not in ALLOWED_KEYS:
        raise ValueError("Unsupported food preference category")
    conn = repository.get_connection()
    try:
        with conn:
            _ensure_schema(conn)
            deleted = conn.execute("DELETE FROM customer_preferences WHERE customer_id = ? AND preference_key = ?", (customer_id, key)).rowcount
    finally:
        conn.close()
    return bool(deleted)
