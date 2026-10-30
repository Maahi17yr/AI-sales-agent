"""Seed approved meal customization rules without resetting existing data.

Run: python -m data.database.seed_customizations
This is an administrator-only setup script, not an AI tool.
"""
from data.database.models import get_connection
from data.database.customization_schema import create_customization_tables

# Meal components explicitly described in the existing product catalogue.
# Only listed components can be removed. Removing a component has no refund.
MEAL_COMPONENTS = {
    'Party Jollof Rice': ('Fried Plantain', 'Boiled Egg', 'Beef'),
    'Fried Rice': ('Fried Plantain', 'Boiled Egg', 'Fried Chicken'),
    'White Rice and Stew': ('Extra Stew', 'Beef'),
    'Coconut Rice': ('Fried Plantain', 'Fried Chicken'),
    'Beans and Plantain': ('Fried Plantain',),
    'Boiled Yam and Egg Sauce': ('Boiled Egg',),
}

# Only extras sold as independently priced menu products are offered here.
# Prices are read live from products, not hardcoded.
APPROVED_EXTRAS = ('Fried Plantain', 'Boiled Egg', 'Fried Chicken', 'Beef')
MAX_EXTRA_PER_MEAL = 2


def seed_customizations():
    create_customization_tables()
    conn = get_connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        names = set(APPROVED_EXTRAS)
        for components in MEAL_COMPONENTS.values():
            names.update(components)
        for name in sorted(names):
            conn.execute('''INSERT OR IGNORE INTO ingredients (name, stock_quantity)
                            VALUES (?, 0)''', (name,))
        products = {r['name']: dict(r) for r in conn.execute(
            'SELECT id, name, price FROM products')}
        ingredients = {r['name']: dict(r) for r in conn.execute(
            'SELECT id, name FROM ingredients')}
        missing = [n for n in set(MEAL_COMPONENTS) | names if n not in products]
        if missing:
            raise ValueError('Seed your product catalogue first. Missing: ' + ', '.join(sorted(missing)))

        for meal_name, components in MEAL_COMPONENTS.items():
            meal_id = products[meal_name]['id']
            for ingredient_name in components:
                conn.execute('''INSERT OR IGNORE INTO meal_ingredients
                    (product_id, ingredient_id, included_quantity, removable)
                    VALUES (?, ?, 1, 1)''',
                    (meal_id, ingredients[ingredient_name]['id']))
            for extra_name in APPROVED_EXTRAS:
                conn.execute('''INSERT OR IGNORE INTO meal_addons
                    (product_id, ingredient_id, extra_price, max_quantity)
                    VALUES (?, ?, ?, ?)''',
                    (meal_id, ingredients[extra_name]['id'],
                     products[extra_name]['price'], MAX_EXTRA_PER_MEAL))
        conn.commit()
        print(f'Customization rules seeded for {len(MEAL_COMPONENTS)} meals.')
        print('Ingredient stock defaults to zero; set real quantities before offering extras.')
        print('Existing ingredient stock and customization prices were preserved.')
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    seed_customizations()
