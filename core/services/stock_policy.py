"""The single authoritative stock policy for prepared and legacy menu lines.

Customer-facing callers see only portions and prices. Internal units never
enter customer cart or checkout summaries. The existing checkout commit remains
the only place that deducts inventory; payment verification never deducts.
"""
import os
from collections import Counter

def _postgres():
    return bool(os.getenv("DATABASE_URL", "").strip())


def active_prepared(conn, product_id, variant="standard", *, lock=False):
    suffix = " FOR UPDATE" if lock and _postgres() else ""
    return conn.execute("""
        SELECT product_id, variant, active, unit, quantity_units,
               serving_units, tolerance_units, extra_units,
               extra_tolerance_units, extra_price
        FROM prepared_stock WHERE product_id = ? AND variant = ?
    """ + suffix, (product_id, variant)).fetchone()


def addon_link(conn, product_id, ingredient_id):
    return conn.execute("""
        SELECT source, target_product_id, target_variant
        FROM prepared_stock_addon_links
        WHERE product_id = ? AND ingredient_id = ?
    """, (product_id, ingredient_id)).fetchone()


def active_in_cart(conn, items):
    ids = sorted({item.get("product_id") for item in items
                  if isinstance(item, dict) and type(item.get("product_id")) is int})
    if not ids:
        return False
    marks = ",".join("?" for _ in ids)
    return conn.execute(
        "SELECT 1 FROM prepared_stock WHERE active = 1 AND product_id IN ("
        + marks + ") LIMIT 1", ids
    ).fetchone() is not None


def lock_base_products(conn, items):
    """Serialize owner source/mapping changes with checkout on PostgreSQL.

    Every order uses sorted product rows; owners take the same row lock before
    changing activation or addon mapping. SQLite BEGIN IMMEDIATE already
    serializes writes.
    """
    if not _postgres():
        return
    ids = sorted({item.get("product_id") for item in items
                  if isinstance(item, dict) and type(item.get("product_id")) is int})
    if not ids:
        return
    marks = ",".join("?" for _ in ids)
    conn.execute(
        "SELECT id FROM products WHERE id IN (" + marks + ") ORDER BY id FOR UPDATE", ids
    ).fetchall()


def lock_prepared_sources(conn, items):
    """Lock all source rows before reading serving quantities and prices.

    Checkout has already locked base product rows, stabilizing their mappings.
    Include any separately stocked add-on targets in deterministic lock order.
    SQLite BEGIN IMMEDIATE already serializes all writers.
    """
    if not _postgres():
        return
    keys = {(item["product_id"], item.get("variant", "standard"))
            for item in items if isinstance(item, dict)
            and type(item.get("product_id")) is int}
    ids = sorted({pid for pid, _ in keys})
    if ids:
        marks = ",".join("?" for _ in ids)
        targets = conn.execute("""
            SELECT target_product_id, target_variant
            FROM prepared_stock_addon_links
            WHERE source = 'prepared_other' AND product_id IN (
        """ + marks + ")", ids).fetchall()
        keys.update((row["target_product_id"], row["target_variant"])
                    for row in targets)
    for pid, variant in sorted(keys):
        active_prepared(conn, pid, variant, lock=True)


def addon_selection(conn, product_id, variant, ingredient_id):
    """Return either legacy ingredient, same-pot extra, or other prepared side.

    The existing meal_addons rule supplies the per-meal price for independent
    add-ons. Only same-pot extras use the owner-configured extra price.
    """
    if variant != "standard":
        return None
    link = addon_link(conn, product_id, ingredient_id)
    if not link:
        return None
    base = active_prepared(conn, product_id, variant)
    if not base or not base["active"]:
        raise ValueError("This extra needs an active prepared meal.")
    if link["source"] == "self_extra":
        return {"source": "self_extra", "price": base["extra_price"],
                "target_product_id": product_id, "target_variant": variant}
    if link["source"] == "prepared_other":
        target = active_prepared(conn, link["target_product_id"], link["target_variant"])
        if not target or not target["active"]:
            raise ValueError("This extra is currently unavailable.")
        return {"source": "prepared_other", "price": None,
                "target_product_id": link["target_product_id"],
                "target_variant": link["target_variant"]}
    raise ValueError("Unsupported extra stock source.")


def demand(conn, items, *, lock_products=False):
    """Compute combined demand across the ENTIRE cart to avoid double counting.

    Returns (legacy_meal_counts, legacy_addon_counts, prepared_internal_units).
    Items may be duplicated in the cart; Counter sums their shared stock use.
    """
    legacy_meals, legacy_extras, prepared = Counter(), Counter(), Counter()
    if lock_products:
        lock_base_products(conn, items)
    for item in items:
        pid, variant, qty = item["product_id"], item.get("variant", "standard"), item["quantity"]
        if type(pid) is not int or type(qty) is not int or qty <= 0:
            raise ValueError("Invalid cart quantity.")
        base = active_prepared(conn, pid, variant)
        if base and base["active"]:
            prepared[(pid, variant)] += qty * (base["serving_units"] + base["tolerance_units"])
        else:
            legacy_meals[(pid, variant)] += qty

        for mod in item.get("modifications", []):
            if mod["action"] != "add":
                continue
            ingredient_id, count = mod["ingredient_id"], mod["quantity"]
            if type(ingredient_id) is not int or type(count) is not int or count <= 0:
                raise ValueError("Invalid extra quantity.")
            selection = addon_selection(conn, pid, variant, ingredient_id)
            if selection is None:
                legacy_extras[ingredient_id] += qty * count
            elif selection["source"] == "self_extra":
                prepared[(pid, variant)] += (
                    qty * count * (base["extra_units"] + base["extra_tolerance_units"])
                )
            else:
                target_key = (selection["target_product_id"], selection["target_variant"])
                target = active_prepared(conn, *target_key)
                if not target or not target["active"]:
                    raise ValueError("This extra is currently unavailable.")
                prepared[target_key] += qty * count * (
                    target["serving_units"] + target["tolerance_units"]
                )
    return legacy_meals, legacy_extras, prepared


def validate(conn, legacy_meals, legacy_extras, prepared, *, lock=False):
    """Final checkout calls with lock=True inside its atomic transaction."""
    for (pid, variant), needed in sorted(legacy_meals.items()):
        suffix = " FOR UPDATE" if lock and _postgres() else ""
        row = conn.execute("""
            SELECT quantity FROM inventory WHERE product_id = ? AND variant = ?
        """ + suffix, (pid, variant)).fetchone()
        if not row or row["quantity"] < needed:
            raise ValueError("Insufficient meal stock for the full order.")
    for ingredient_id, needed in sorted(legacy_extras.items()):
        suffix = " FOR UPDATE" if lock and _postgres() else ""
        row = conn.execute("""
            SELECT stock_quantity, available FROM ingredients WHERE id = ?
        """ + suffix, (ingredient_id,)).fetchone()
        if not row or not row["available"] or row["stock_quantity"] < needed:
            raise ValueError("Insufficient extra stock for the full order.")
    for (pid, variant), needed in sorted(prepared.items()):
        row = active_prepared(conn, pid, variant, lock=lock)
        if not row or not row["active"] or row["quantity_units"] < needed:
            raise ValueError("Not enough prepared portions are available.")


def deduct(conn, legacy_meals, legacy_extras, prepared, *, order_id, customer_id):
    """Only call after order + items are inserted, within that same transaction.

    Conditional writes prohibit negative stock in all three sources. The
    unique order/product/variant allocation row guarantees audit uniqueness.
    An unexpected failure rolls back the entire checkout.
    """
    for (pid, variant), needed in sorted(legacy_meals.items()):
        updated = conn.execute("""UPDATE inventory SET quantity = quantity - ?
            WHERE product_id = ? AND variant = ? AND quantity >= ?""",
            (needed, pid, variant, needed))
        if updated.rowcount != 1:
            raise ValueError("Meal stock changed during checkout.")
    for ingredient_id, needed in sorted(legacy_extras.items()):
        updated = conn.execute("""UPDATE ingredients
            SET stock_quantity = stock_quantity - ?
            WHERE id = ? AND available = 1 AND stock_quantity >= ?""",
            (needed, ingredient_id, needed))
        if updated.rowcount != 1:
            raise ValueError("Extra stock changed during checkout.")
    for (pid, variant), needed in sorted(prepared.items()):
        if needed <= 0:
            continue
        before = active_prepared(conn, pid, variant, lock=True)
        if not before or not before["active"] or before["quantity_units"] < needed:
            raise ValueError("Prepared stock changed during checkout.")
        updated = conn.execute("""UPDATE prepared_stock
            SET quantity_units = quantity_units - ?
            WHERE product_id = ? AND variant = ? AND active = 1
              AND quantity_units >= ?""", (needed, pid, variant, needed))
        if updated.rowcount != 1:
            raise ValueError("Prepared stock changed during checkout.")
        conn.execute("""INSERT INTO prepared_order_deductions
            (order_id, product_id, variant, customer_id, quantity_units, before_units, after_units)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (order_id, pid, variant, customer_id,
             needed, before["quantity_units"], before["quantity_units"] - needed))
