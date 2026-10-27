
import sqlite3

try:
    from .models import get_connection, create_tables
except ImportError:
    from models import get_connection, create_tables


# All prices are in Nigerian Naira (NGN).
# Each item is sold as one listed portion or serving.

PRODUCTS = [
    # MAIN MEALS
    ("Party Jollof Rice", "Main Meals",
     "Smoky Nigerian jollof rice. Standard meal includes plantain, egg and beef.",
     4500, 30, 25),

    ("Fried Rice", "Main Meals",
     "Nigerian fried rice with vegetables. Standard meal includes plantain, egg and chicken.",
     5000, 30, 20),

    ("White Rice and Stew", "Main Meals",
     "White rice with Nigerian tomato stew and beef.",
     4000, 25, 20),

    ("Ofada Rice and Ayamase", "Main Meals",
     "Local Ofada rice served with spicy green pepper sauce and assorted meat.",
     5500, 35, 15),

    ("Coconut Rice", "Main Meals",
     "Nigerian coconut rice served with plantain and chicken.",
     5000, 35, 15),

    ("Beans and Plantain", "Main Meals",
     "Soft Nigerian beans served with fried ripe plantain.",
     3500, 30, 20),

    ("Yam Porridge", "Main Meals",
     "Nigerian asaro cooked with palm oil, peppers and vegetables.",
     3500, 35, 15),

    ("Ewa Agoyin", "Main Meals",
     "Mashed beans with spicy Ewa Agoyin sauce.",
     3000, 30, 20),

    ("Moi Moi", "Main Meals",
     "Steamed Nigerian bean pudding with pepper and seasoning.",
     1500, 40, 30),

    ("Boiled Yam and Egg Sauce", "Main Meals",
     "Boiled yam served with Nigerian tomato and egg sauce.",
     4000, 30, 15),

    # SOUPS
    ("Egusi Soup", "Soups",
     "Nigerian melon seed soup with vegetables and assorted meat. One bowl.",
     4000, 45, 15),

    ("Efo Riro", "Soups",
     "Yoruba vegetable soup with peppers and assorted meat. One bowl.",
     4000, 40, 15),

    ("Ogbono Soup", "Soups",
     "Traditional Nigerian ogbono soup with assorted meat. One bowl.",
     4000, 45, 15),

    ("Afang Soup", "Soups",
     "Nigerian Afang soup made with Afang and waterleaf. One bowl.",
     4500, 45, 12),

    ("Edikang Ikong", "Soups",
     "Rich Nigerian vegetable soup with assorted meat. One bowl.",
     4500, 45, 12),

    # SWALLOWS
    ("Pounded Yam", "Swallows",
     "One serving of smooth pounded yam.",
     1500, 15, 30),

    ("Eba", "Swallows",
     "One serving of garri-based Nigerian swallow.",
     800, 10, 40),

    ("Amala", "Swallows",
     "One serving of traditional yam flour swallow.",
     1000, 15, 35),

    ("Semo", "Swallows",
     "One serving of semolina swallow.",
     1000, 15, 30),

    # PROTEINS
    ("Fried Chicken", "Proteins",
     "One piece of Nigerian-style seasoned fried chicken.",
     2500, 25, 30),

    ("Beef", "Proteins",
     "One piece of peppered beef.",
     1200, 20, 35),

    ("Assorted Meat", "Proteins",
     "One serving of Nigerian assorted meat.",
     2000, 30, 20),

    ("Fried Fish", "Proteins",
     "One piece of seasoned fried fish.",
     2500, 25, 20),

    ("Ponmo", "Proteins",
     "One piece of peppered cow skin.",
     700, 20, 30),

    # SIDES
    ("Fried Plantain", "Sides",
     "One serving of fried ripe plantain (dodo).",
     1000, 15, 40),

    ("Boiled Egg", "Sides",
     "One boiled egg.",
     500, 10, 50),

    ("Coleslaw", "Sides",
     "One serving of Nigerian-style coleslaw.",
     800, 15, 25),

    ("Extra Stew", "Sides",
     "One serving of Nigerian tomato stew.",
     700, 10, 30),

    # SNACKS
    ("Small Chops Pack", "Snacks",
     "A pack containing puff-puff, samosa, spring rolls and peppered chicken.",
     3500, 35, 25),

    ("Puff-Puff", "Snacks",
     "One pack of six Nigerian puff-puff balls.",
     1000, 20, 40),
]


# Delivery fees are fictional and can be changed later.
# Format: area, fee, minimum hours, maximum hours

DELIVERY_ZONES = [
    ("Ikeja", 1500, 1, 2),
    ("Maryland", 1800, 1, 3),
    ("Ogba", 1800, 1, 3),
    ("Yaba", 2500, 2, 4),
    ("Surulere", 2800, 2, 4),
    ("Gbagada", 2500, 2, 4),
    ("Magodo", 2500, 2, 4),
    ("Victoria Island", 4000, 3, 5),
    ("Lekki Phase 1", 4500, 3, 5),
    ("Ajah", 5500, 4, 6),
]


def seed_products(conn):
    """Insert products and their starting inventory."""

    added = 0

    for name, category, description, price, prep_time, stock in PRODUCTS:

        existing = conn.execute(
            "SELECT id FROM products WHERE name = ?",
            (name,)
        ).fetchone()

        if existing:
            product_id = existing["id"]
        else:
            cursor = conn.execute("""
                INSERT INTO products
                (name, category, description, price, preparation_minutes)
                VALUES (?, ?, ?, ?, ?)
            """, (name, category, description, price, prep_time))

            product_id = cursor.lastrowid
            added += 1

        # Do not overwrite stock that already exists.
        conn.execute("""
            INSERT OR IGNORE INTO inventory
            (product_id, variant, quantity)
            VALUES (?, 'standard', ?)
        """, (product_id, stock))

    return added


def seed_delivery_zones(conn):
    """Insert sample delivery areas."""

    added = 0

    for area, fee, min_hours, max_hours in DELIVERY_ZONES:

        cursor = conn.execute("""
            INSERT OR IGNORE INTO delivery_zones
            (area, fee, estimated_min_hours, estimated_max_hours)
            VALUES (?, ?, ?, ?)
        """, (area, fee, min_hours, max_hours))

        added += cursor.rowcount

    return added


def show_catalogue(conn):
    """Display all products with prices and stock."""

    rows = conn.execute("""
        SELECT
            p.id,
            p.name,
            p.category,
            p.price,
            i.quantity
        FROM products p
        JOIN inventory i ON p.id = i.product_id
        ORDER BY p.category, p.name
    """).fetchall()

    print("\nFOOD CATALOGUE")
    print("-" * 75)

    for row in rows:
        print(
            f"{row['id']:>2} | "
            f"{row['name']:<25} | "
            f"NGN {row['price']:>6,} | "
            f"Stock: {row['quantity']}"
        )


def main():
    create_tables()

    try:
        with get_connection() as conn:
            products_added = seed_products(conn)
            zones_added = seed_delivery_zones(conn)

            print(f"\n{products_added} new products added.")
            print(f"{zones_added} new delivery zones added.")

            show_catalogue(conn)

        print("\nDatabase seeding completed successfully!")

    except sqlite3.Error as error:
        print(f"Database error: {error}")
        raise


if __name__ == "__main__":
    main()
