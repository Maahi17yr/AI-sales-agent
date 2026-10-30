"""Explicit, backup-first SQLite migration for email/password accounts.

Run from project root: python -m data.database.migrate_auth
Never runs automatically during application startup.
"""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from data.database.models import DB_PATH


def migrate(path=DB_PATH):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Existing database not found: {path}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.stem}.pre-auth-{stamp}.backup.sqlite3")
    with closing(sqlite3.connect(path)) as source:
        with closing(sqlite3.connect(backup)) as target:
            source.backup(target)
    print(f"Backup created: {backup}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(customers)")}
        if not cols:
            raise RuntimeError("customers table missing; initialize database first")
        if "email" not in cols or "password_hash" not in cols or cols["phone"]["notnull"]:
            # SQLite cannot remove NOT NULL in place. Keep IDs unchanged so orders
            # and existing conversation sessions retain their customer references.
            conn.execute("""CREATE TABLE customers_auth_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                email TEXT UNIQUE COLLATE NOCASE,
                password_hash TEXT,
                phone TEXT UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            email_expr = "email" if "email" in cols else "NULL"
            hash_expr = "password_hash" if "password_hash" in cols else "NULL"
            conn.execute(f"""INSERT INTO customers_auth_new
                (id, name, email, password_hash, phone, created_at)
                SELECT id, name, {email_expr}, {hash_expr}, phone, created_at
                FROM customers""")
            conn.execute("DROP TABLE customers")
            conn.execute("ALTER TABLE customers_auth_new RENAME TO customers")
        conn.execute("""CREATE TABLE IF NOT EXISTS auth_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE CASCADE
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_customer ON auth_sessions(customer_id)")
        issues = conn.execute("PRAGMA foreign_key_check").fetchall()
        if issues:
            raise RuntimeError(f"Foreign key violations detected: {len(issues)}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print("Authentication schema migration complete.")


if __name__ == "__main__":
    migrate()
