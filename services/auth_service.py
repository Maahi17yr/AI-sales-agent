"""Customer authentication primitives. No public endpoints are enabled yet."""
import hashlib
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

from database.models import get_connection

_hasher = PasswordHasher()
SESSION_DAYS = 7


def _email(value):
    if not isinstance(value, str):
        raise ValueError("A valid email is required.")
    normalized = value.strip().lower()
    if len(normalized) > 254 or len(normalized) < 3 or normalized.count("@") != 1:
        raise ValueError("A valid email is required.")
    local, domain = normalized.split("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("A valid email is required.")
    return normalized


def register_customer(name, email, password):
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        raise ValueError("A name is required (maximum 120 characters).")
    email = _email(email)
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise ValueError("Password must contain at least 12 characters.")
    password_hash = _hasher.hash(password)
    with closing(get_connection()) as conn, conn:
        try:
            cursor = conn.execute(
                "INSERT INTO customers(name,email,password_hash) VALUES(?,?,?)",
                (name.strip(), email, password_hash),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Email already registered.") from exc
        customer_id = cursor.lastrowid
    return {"id": customer_id, "name": name.strip(), "email": email}


def _new_session(conn, customer_id):
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expiry = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
    conn.execute(
        "INSERT INTO auth_sessions(customer_id,token_hash,expires_at) VALUES(?,?,?)",
        (customer_id, token_hash, expiry),
    )
    return token


def login_customer(email, password):
    email = _email(email)
    if not isinstance(password, str):
        raise ValueError("Invalid email or password.")
    with closing(get_connection()) as conn, conn:
        row = conn.execute(
            "SELECT id, name, email, password_hash FROM customers WHERE email=?",
            (email,),
        ).fetchone()
        if row is None or not row["password_hash"]:
            raise ValueError("Invalid email or password.")
        try:
            _hasher.verify(row["password_hash"], password)
        except (VerifyMismatchError, VerificationError, InvalidHashError) as exc:
            raise ValueError("Invalid email or password.") from exc
        token = _new_session(conn, row["id"])
    return {"customer": {"id": row["id"], "name": row["name"], "email": row["email"]}, "session_token": token}


def authenticate_token(token):
    if not isinstance(token, str) or not token or len(token) > 256:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with closing(get_connection()) as conn, conn:
        row = conn.execute("""SELECT c.id, c.name, c.email
            FROM auth_sessions s JOIN customers c ON c.id=s.customer_id
            WHERE s.token_hash=? AND s.expires_at>?""",
            (token_hash, datetime.now(timezone.utc).isoformat()),
        ).fetchone()
    return dict(row) if row else None


def logout_customer(token):
    if not isinstance(token, str) or not token:
        return False
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with closing(get_connection()) as conn, conn:
        return conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,)).rowcount > 0
