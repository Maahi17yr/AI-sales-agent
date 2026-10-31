"""Privileged account provisioning; intentionally not exposed as a public API."""
from contextlib import closing

from core.services.auth_service import _email
from data.database.models import get_connection


def promote_registered_customer(email: str) -> dict:
    """Promote an existing password-backed account and revoke old sessions.

    Only call this from a trusted operator-controlled CLI or migration.
    The web application never accepts a role from registration or login input.
    """
    normalized = _email(email)
    with closing(get_connection()) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            account = conn.execute(
                "SELECT id, email, role, password_hash FROM customers WHERE email = ?",
                (normalized,),
            ).fetchone()
            if not account or not account["password_hash"]:
                raise ValueError("A registered account with a password is required.")
            if account["role"] != "customer":
                raise ValueError("Account is not eligible for promotion.")
            changed = conn.execute(
                "UPDATE customers SET role = 'owner' WHERE id = ? AND role = 'customer'",
                (account["id"],),
            )
            if changed.rowcount != 1:
                raise ValueError("Account role changed; promotion cancelled.")
            conn.execute("DELETE FROM auth_sessions WHERE customer_id = ?", (account["id"],))
            conn.execute(
                "INSERT INTO owner_audit_log(actor, action, target_customer_id) VALUES(?, ?, ?)",
                ("trusted_cli", "promote_to_owner", account["id"]),
            )
            conn.commit()
            return {"id": account["id"], "email": account["email"], "role": "owner"}
        except Exception:
            conn.rollback()
            raise
