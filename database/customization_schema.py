"""Additive database schema for meal customization.

Run from the project root with: python -m database.customization_schema
Do not expose database administration functions to the AI agent.
"""

from database.models import get_connection


def create_customization_tables():
    """Create customization metadata without modifying existing orders or stock."""
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ingredients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                stock_quantity INTEGER NOT NULL DEFAULT 0
                    CHECK (stock_quantity >= 0),
                available INTEGER NOT NULL DEFAULT 1
                    CHECK (available IN (0, 1))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meal_ingredients (
                product_id INTEGER NOT NULL,
                ingredient_id INTEGER NOT NULL,
                included_quantity INTEGER NOT NULL DEFAULT 1
                    CHECK (included_quantity > 0),
                removable INTEGER NOT NULL DEFAULT 0
                    CHECK (removable IN (0, 1)),
                PRIMARY KEY (product_id, ingredient_id),
                FOREIGN KEY (product_id) REFERENCES products(id)
                    ON DELETE RESTRICT,
                FOREIGN KEY (ingredient_id) REFERENCES ingredients(id)
                    ON DELETE RESTRICT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meal_addons (
                product_id INTEGER NOT NULL,
                ingredient_id INTEGER NOT NULL,
                extra_price INTEGER NOT NULL CHECK (extra_price >= 0),
                max_quantity INTEGER NOT NULL DEFAULT 1
                    CHECK (max_quantity > 0),
                PRIMARY KEY (product_id, ingredient_id),
                FOREIGN KEY (product_id) REFERENCES products(id)
                    ON DELETE RESTRICT,
                FOREIGN KEY (ingredient_id) REFERENCES ingredients(id)
                    ON DELETE RESTRICT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS order_item_customizations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_item_id INTEGER NOT NULL,
                ingredient_id INTEGER NOT NULL,
                action TEXT NOT NULL CHECK (action IN ('add', 'remove')),
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                unit_price_delta INTEGER NOT NULL DEFAULT 0
                    CHECK (unit_price_delta >= 0),
                FOREIGN KEY (order_item_id) REFERENCES order_items(id)
                    ON DELETE RESTRICT,
                FOREIGN KEY (ingredient_id) REFERENCES ingredients(id)
                    ON DELETE RESTRICT
            )
        """)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    create_customization_tables()
    print('Customization tables created (existing data preserved).')
