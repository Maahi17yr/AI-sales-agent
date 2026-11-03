
import sqlite3
from contextlib import closing
from pathlib import Path
import os

# Optional storage root for hosted demo. Render free instances do not provide
# durable disk storage: /tmp data can disappear on restart/redeploy.
_DATA_DIR = os.getenv("NAIJA_BITES_DATA_DIR", "").strip()
DB_PATH = (Path(_DATA_DIR).expanduser() / "smallchops.db"
           if _DATA_DIR else Path(__file__).resolve().parent / "smallchops.db")


def get_connection():
    """Connect to hosted PostgreSQL when configured, otherwise local SQLite."""
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        if not url.startswith(("postgresql://", "postgres://")):
            raise ValueError("DATABASE_URL must be a PostgreSQL URI.")
        from data.database.postgres import PostgresConnection
        return PostgresConnection(url)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def create_tables():
    """Create and safely extend the customer and owner data schema."""

    with closing(get_connection()) as conn, conn:
        if os.getenv("DATABASE_URL", "").strip():
            conn.execute("CREATE EXTENSION IF NOT EXISTS citext")

        # 1. PRODUCTS
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT,
                price INTEGER NOT NULL CHECK (price >= 0),
                preparation_minutes INTEGER DEFAULT 0
                    CHECK (preparation_minutes >= 0),
                available INTEGER NOT NULL DEFAULT 1
                    CHECK (available IN (0, 1))
            )
        """)

        # 2. INVENTORY
        conn.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                variant TEXT NOT NULL DEFAULT 'standard',
                quantity INTEGER NOT NULL DEFAULT 0
                    CHECK (quantity >= 0),

                UNIQUE (product_id, variant),

                FOREIGN KEY (product_id)
                    REFERENCES products(id)
                    ON DELETE RESTRICT
            )
        """)

        # 3. CUSTOMERS
        conn.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                email TEXT UNIQUE COLLATE NOCASE,
                password_hash TEXT,
                role TEXT NOT NULL DEFAULT 'customer'
                    CHECK (role IN ('customer', 'owner')),
                phone TEXT UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Existing databases predate roles. Add the column without recreating
        # customers or invalidating their orders and chat history.
        if os.getenv("DATABASE_URL", "").strip():
            role_exists = conn.execute("""
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'customers' AND column_name = 'role'
            """).fetchone() is not None
        else:
            role_exists = any(row["name"] == "role"
                              for row in conn.execute("PRAGMA table_info(customers)").fetchall())
        if not role_exists:
            conn.execute("""ALTER TABLE customers ADD COLUMN role TEXT NOT NULL
                DEFAULT 'customer' CHECK (role IN ('customer', 'owner'))""")

        # 4. AUTHENTICATION SESSIONS
        conn.execute("""
            CREATE TABLE IF NOT EXISTS auth_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
            )
        """)

        # Restricted management changes are recorded independently of chats.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS owner_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                target_customer_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (target_customer_id) REFERENCES customers(id)
            )
        """)

        # 5. DELIVERY ZONES
        conn.execute("""
            CREATE TABLE IF NOT EXISTS delivery_zones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                area TEXT NOT NULL UNIQUE,
                fee INTEGER NOT NULL CHECK (fee >= 0),
                estimated_min_hours INTEGER NOT NULL
                    CHECK (estimated_min_hours >= 0),
                estimated_max_hours INTEGER NOT NULL
                    CHECK (estimated_max_hours >= estimated_min_hours),
                available INTEGER NOT NULL DEFAULT 1
                    CHECK (available IN (0, 1))
            )
        """)

        # 5. ORDERS
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                delivery_zone_id INTEGER NOT NULL,
                delivery_address TEXT NOT NULL,

                subtotal INTEGER NOT NULL CHECK (subtotal >= 0),
                delivery_fee INTEGER NOT NULL
                    CHECK (delivery_fee >= 0),
                discount INTEGER NOT NULL DEFAULT 0
                    CHECK (discount >= 0),

                total INTEGER NOT NULL CHECK (
                    total >= 0
                    AND total = subtotal + delivery_fee - discount
                ),

                status TEXT NOT NULL DEFAULT 'pending_payment'
                    CHECK (status IN (
                        'pending_payment',
                        'confirmed',
                        'preparing',
                        'out_for_delivery',
                        'delivered',
                        'cancelled'
                    )),

                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (customer_id)
                    REFERENCES customers(id),

                FOREIGN KEY (delivery_zone_id)
                    REFERENCES delivery_zones(id)
            )
        """)

        # 6. ORDER ITEMS
        conn.execute("""
            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                variant TEXT NOT NULL DEFAULT 'standard',
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                unit_price INTEGER NOT NULL CHECK (unit_price >= 0),

                FOREIGN KEY (order_id)
                    REFERENCES orders(id)
                    ON DELETE RESTRICT,

                FOREIGN KEY (product_id)
                    REFERENCES products(id)
                    ON DELETE RESTRICT
            )
        """)

        # 7. PAYMENTS
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL UNIQUE,
                amount INTEGER NOT NULL CHECK (amount >= 0),
                payment_reference TEXT UNIQUE,
                payment_link TEXT,

                status TEXT NOT NULL DEFAULT 'unpaid'
                    CHECK (status IN (
                        'unpaid',
                        'paid',
                        'failed',
                        'refunded'
                    )),

                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                paid_at TEXT,

                FOREIGN KEY (order_id)
                    REFERENCES orders(id)
                    ON DELETE RESTRICT
            )
        """)

        # 8. CONVERSATION SESSIONS
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL UNIQUE,
                customer_id INTEGER,

                cart_data TEXT NOT NULL DEFAULT '{}',
                chat_history TEXT NOT NULL DEFAULT '[]',

                awaiting_confirmation INTEGER NOT NULL DEFAULT 0
                    CHECK (awaiting_confirmation IN (0, 1)),

                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (customer_id)
                    REFERENCES customers(id)
                    ON DELETE SET NULL
            )
        """)

        # Durable chat history, scoped to the authenticated customer.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS customer_chats (
                customer_id INTEGER NOT NULL,
                chat_id TEXT NOT NULL,
                title TEXT NOT NULL,
                messages TEXT NOT NULL,
                updated_at BIGINT NOT NULL,
                PRIMARY KEY (customer_id, chat_id),
                FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_customer_chats_recent
            ON customer_chats(customer_id, updated_at)
        """)

        # Per-request model token usage. No prompts, replies, or tool payloads.
        # A customer-scoped FK prevents orphaned usage records.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                model_id TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0 CHECK(input_tokens >= 0),
                output_tokens INTEGER NOT NULL DEFAULT 0 CHECK(output_tokens >= 0),
                total_tokens INTEGER NOT NULL DEFAULT 0 CHECK(total_tokens >= 0),
                cache_read_tokens INTEGER NOT NULL DEFAULT 0 CHECK(cache_read_tokens >= 0),
                cache_write_tokens INTEGER NOT NULL DEFAULT 0 CHECK(cache_write_tokens >= 0),
                duration_ms INTEGER NOT NULL DEFAULT 0 CHECK(duration_ms >= 0),
                outcome TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_agent_usage_events_created
            ON agent_usage_events(created_at)
        """)

        # Indexes for frequently accessed records
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_orders_customer
            ON orders(customer_id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_orders_status
            ON orders(status)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_inventory_product
            ON inventory(product_id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_customer
            ON conversation_sessions(customer_id)
        """)

    print("Database initialized successfully!")
    print("Core tables are ready.")


def show_tables():
    """Display the tables currently in the database."""
    with closing(get_connection()) as conn, conn:
        tables = conn.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """).fetchall()

    print("\nDATABASE TABLES")
    print("-" * 30)

    for number, table in enumerate(tables, start=1):
        print(f"{number}. {table['name']}")


if __name__ == "__main__":
    create_tables()
    show_tables()
