"""Owner-only prepared-food configuration and stock movements.

Never trust frontend-supplied owner identities. Call only after require_owner.
Amounts are integer grams, millilitres, or whole pieces. Customer storefront
and checkout only consume this table for explicitly activated variants.
"""
import os
from contextlib import closing

from data.database.models import get_connection
from core.services import stock_policy

MAX_UNITS = 1_000_000_000
UNITS = frozenset(("g", "ml", "piece"))
ACTIONS = frozenset(("add", "remove", "set"))


def _checked_int(value, name, *, minimum=0, maximum=MAX_UNITS):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be a whole number from {minimum} to {maximum}.")
    return value


def _variant(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 40:
        raise ValueError("Invalid meal variant.")
    return value.strip()


def _present(row):
    result = dict(row)
    max_standard = result["serving_units"] + result["tolerance_units"]
    max_extra = result["extra_units"] + result["extra_tolerance_units"]
    result["safe_standard_servings"] = result["quantity_units"] // max_standard
    result["safe_extra_servings"] = result["quantity_units"] // max_extra
    result["active"] = bool(result.get("active", 0))
    result["inventory_source"] = "prepared" if result["active"] else "legacy"
    result["checkout_enabled"] = result["active"]
    return result


def list_stocks():
    with closing(get_connection()) as conn:
        rows = conn.execute("""
            SELECT ps.product_id, p.name AS product_name, ps.variant, ps.unit,
                   ps.quantity_units, ps.serving_units, ps.tolerance_units,
                   ps.extra_units, ps.extra_tolerance_units, ps.extra_price, ps.active,
                   p.available AS product_available
            FROM prepared_stock ps
            JOIN products p ON p.id = ps.product_id
            ORDER BY p.name, ps.variant
        """).fetchall()
    return [_present(row) for row in rows]


def recent_movements(limit=50):
    with closing(get_connection()) as conn:
        manual = conn.execute("""
            SELECT m.id, m.product_id, p.name AS product_name, m.variant,
                   m.action, m.change_units, m.before_units, m.after_units,
                   m.reason, m.created_at, c.name AS actor_name, NULL AS order_id
            FROM prepared_stock_movements m
            JOIN products p ON p.id = m.product_id
            JOIN customers c ON c.id = m.actor_customer_id
            ORDER BY m.id DESC LIMIT ?
        """, (limit,)).fetchall()
        orders = conn.execute("""
            SELECT d.order_id AS id, d.product_id, p.name AS product_name,
                   d.variant, 'order' AS action, -d.quantity_units AS change_units,
                   d.before_units, d.after_units, 'Customer order' AS reason,
                   d.created_at, c.name AS actor_name, d.order_id
            FROM prepared_order_deductions d
            JOIN products p ON p.id = d.product_id
            JOIN customers c ON c.id = d.customer_id
            ORDER BY d.order_id DESC LIMIT ?
        """, (limit,)).fetchall()
    events = [dict(row) for row in manual] + [dict(row) for row in orders]
    events.sort(key=lambda event: (str(event["created_at"]), event["id"]),
                reverse=True)
    return events[:limit]


def configure(owner, config):
    product_id = _checked_int(config.get("product_id"), "Product ID", minimum=1)
    variant = _variant(config.get("variant", "standard"))
    unit = config.get("unit")
    if unit not in UNITS:
        raise ValueError("Unit must be g, ml, or piece.")
    serving = _checked_int(config.get("serving_units"), "Standard serving", minimum=1)
    tolerance = _checked_int(config.get("tolerance_units"), "Standard tolerance")
    extra = _checked_int(config.get("extra_units"), "Extra serving", minimum=1)
    extra_tolerance = _checked_int(config.get("extra_tolerance_units"), "Extra tolerance")
    price = _checked_int(config.get("extra_price"), "Extra serving price", maximum=100000000)
    if tolerance >= serving or extra_tolerance >= extra:
        raise ValueError("Serving tolerance must be less than its target.")
    if unit == "piece" and (serving != 1 or tolerance or extra != 1 or extra_tolerance):
        raise ValueError("Whole-piece stock uses one piece per serving with zero tolerance.")

    with closing(get_connection()) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            # Follow checkout's product-first lock order before changing serving
            # estimates, extra prices or units on a prepared stock record.
            _lock_product(conn, product_id)
            inventory = conn.execute("""
                SELECT 1 FROM inventory WHERE product_id = ? AND variant = ?
            """, (product_id, variant)).fetchone()
            if inventory is None:
                raise LookupError("Select an existing menu item and variant.")
            lock = " FOR UPDATE" if os.getenv("DATABASE_URL", "").strip() else ""
            existing = conn.execute("""
                SELECT quantity_units, unit, active,
                       serving_units, tolerance_units, extra_units,
                       extra_tolerance_units FROM prepared_stock
                WHERE product_id = ? AND variant = ?
            """ + lock, (product_id, variant)).fetchone()
            if existing:
                # Already placed orders retain the serving allowance that
                # was allocated before payment; never silently resize it.
                if existing["active"] and any((
                    existing["unit"] != unit,
                    existing["serving_units"] != serving,
                    existing["tolerance_units"] != tolerance,
                    existing["extra_units"] != extra,
                    existing["extra_tolerance_units"] != extra_tolerance,
                )):
                    _require_no_open_orders(conn, product_id, variant)
                if existing["unit"] != unit and existing["quantity_units"]:
                    raise ValueError("Set stock to zero before changing its unit.")
                conn.execute("""
                    UPDATE prepared_stock SET unit = ?, serving_units = ?,
                        tolerance_units = ?, extra_units = ?,
                        extra_tolerance_units = ?, extra_price = ?
                    WHERE product_id = ? AND variant = ?
                """, (unit, serving, tolerance, extra, extra_tolerance, price,
                      product_id, variant))
            else:
                conn.execute("""
                    INSERT INTO prepared_stock
                    (product_id, variant, unit, serving_units, tolerance_units,
                     extra_units, extra_tolerance_units, extra_price)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (product_id, variant, unit, serving, tolerance, extra,
                      extra_tolerance, price))
            conn.execute("""
                INSERT INTO owner_audit_log(actor, action, target_customer_id)
                VALUES (?, ?, ?)
            """, (owner["email"], f"prepared_stock:config:{product_id}:{variant}", owner["id"]))
            row = conn.execute("""
                SELECT product_id, variant, unit, quantity_units, active,
                       serving_units, tolerance_units, extra_units,
                       extra_tolerance_units, extra_price
                FROM prepared_stock WHERE product_id = ? AND variant = ?
            """, (product_id, variant)).fetchone()
            conn.commit()
            return _present(row)
        except Exception:
            conn.rollback()
            raise


def adjust(owner, adjustment):
    product_id = _checked_int(adjustment.get("product_id"), "Product ID", minimum=1)
    variant = _variant(adjustment.get("variant", "standard"))
    action = adjustment.get("action")
    if action not in ACTIONS:
        raise ValueError("Action must be add, remove or set.")
    amount = _checked_int(adjustment.get("quantity_units"), "Stock amount")
    if action in ("add", "remove") and amount == 0:
        raise ValueError("Add/remove quantity must be greater than zero.")
    reason = adjustment.get("reason")
    if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 240:
        raise ValueError("Enter a reason (3–240 characters).")
    reason = reason.strip()

    with closing(get_connection()) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            lock = " FOR UPDATE" if os.getenv("DATABASE_URL", "").strip() else ""
            row = conn.execute("""
                SELECT quantity_units, unit, active, serving_units, tolerance_units,
                       extra_units, extra_tolerance_units, extra_price
                FROM prepared_stock WHERE product_id = ? AND variant = ?
            """ + lock, (product_id, variant)).fetchone()
            if not row:
                raise LookupError("Configure prepared stock before adjusting it.")
            before = row["quantity_units"]
            after = {"add": before + amount, "remove": before - amount, "set": amount}[action]
            if after < 0:
                raise ValueError("Cannot remove more stock than is available.")
            if after > MAX_UNITS:
                raise ValueError("Stock quantity exceeds maximum supported amount.")
            changed = conn.execute("""
                UPDATE prepared_stock SET quantity_units = ?
                WHERE product_id = ? AND variant = ? AND quantity_units = ?
            """, (after, product_id, variant, before))
            if changed.rowcount != 1:
                raise ValueError("Stock changed concurrently; refresh and try again.")
            conn.execute("""
                INSERT INTO prepared_stock_movements(
                    product_id, variant, actor_customer_id, action,
                    change_units, before_units, after_units, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (product_id, variant, owner["id"], action, after - before,
                  before, after, reason))
            conn.execute("""
                INSERT INTO owner_audit_log(actor, action, target_customer_id)
                VALUES (?, ?, ?)
            """, (owner["email"],
                  f"prepared_stock:{action}:{product_id}:{variant}:{before}->{after}",
                  owner["id"]))
            result = _present({
                "product_id": product_id, "variant": variant,
                "quantity_units": after, "unit": row["unit"],
                "active": row["active"],
                "serving_units": row["serving_units"],
                "tolerance_units": row["tolerance_units"],
                "extra_units": row["extra_units"],
                "extra_tolerance_units": row["extra_tolerance_units"],
                "extra_price": row["extra_price"],
            })
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise


def _lock_product(conn, product_id):
    suffix = " FOR UPDATE" if os.getenv("DATABASE_URL", "").strip() else ""
    row = conn.execute("SELECT id FROM products WHERE id = ?" + suffix,
                       (product_id,)).fetchone()
    if row is None:
        raise LookupError("Menu item not found.")


def _unresolved_for_variant(conn, product_id, variant):
    # Historical completed orders do not prevent cutovers. Existing placed
    # orders awaiting payment/fulfilment do; their reservations were calculated
    # using the older source and must not be silently reinterpreted.
    open_base = conn.execute("""
        SELECT 1 FROM orders o JOIN order_items oi ON oi.order_id = o.id
        WHERE oi.product_id = ? AND oi.variant = ?
          AND o.status IN ('pending_payment','confirmed','preparing','out_for_delivery')
        LIMIT 1
    """, (product_id, variant)).fetchone()
    open_addons = conn.execute("""
        SELECT 1 FROM orders o
        JOIN prepared_order_deductions d ON d.order_id = o.id
        WHERE d.product_id = ? AND d.variant = ?
          AND o.status IN ('pending_payment','confirmed','preparing','out_for_delivery')
        LIMIT 1
    """, (product_id, variant)).fetchone()
    return bool(open_base or open_addons)


def _require_no_open_orders(conn, product_id, variant):
    if _unresolved_for_variant(conn, product_id, variant):
        raise ValueError("Resolve existing pending or active orders before switching stock sources.")


def set_active(owner, product_id, variant, active):
    product_id = _checked_int(product_id, "Product ID", minimum=1)
    variant = _variant(variant)
    if type(active) is not bool:
        raise ValueError("Activation must be true or false.")
    with closing(get_connection()) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            _lock_product(conn, product_id)
            lock = " FOR UPDATE" if os.getenv("DATABASE_URL", "").strip() else ""
            row = conn.execute("""
                SELECT product_id, variant, unit, quantity_units, active,
                       serving_units, tolerance_units, extra_units,
                       extra_tolerance_units, extra_price
                FROM prepared_stock WHERE product_id = ? AND variant = ?
            """ + lock, (product_id, variant)).fetchone()
            if row is None:
                raise LookupError("Configure prepared stock before activating it.")
            if bool(row["active"]) != active:
                _require_no_open_orders(conn, product_id, variant)
                if not active:
                    referenced = conn.execute("""
                        SELECT 1 FROM prepared_stock_addon_links
                        WHERE (product_id = ? AND ? = 'standard')
                           OR (source = 'prepared_other' AND target_product_id = ?
                               AND target_variant = ?)
                        LIMIT 1
                    """, (product_id, variant, product_id, variant)).fetchone()
                    if referenced:
                        raise ValueError("Remove prepared add-on mappings before deactivating this stock.")
                conn.execute("""UPDATE prepared_stock SET active = ?
                    WHERE product_id = ? AND variant = ?""",
                    (int(active), product_id, variant))
                conn.execute("""INSERT INTO owner_audit_log
                    (actor, action, target_customer_id) VALUES (?, ?, ?)""",
                    (owner["email"], f"prepared_stock:activate:{product_id}:{variant}:{int(active)}",
                     owner["id"]))
            result = dict(row)
            result["active"] = int(active)
            conn.commit()
            return _present(result)
        except Exception:
            conn.rollback()
            raise


def list_addon_links(product_id):
    product_id = _checked_int(product_id, "Product ID", minimum=1)
    with closing(get_connection()) as conn:
        rows = conn.execute("""
            SELECT ma.ingredient_id, i.name, ma.extra_price, ma.max_quantity,
                   COALESCE(l.source, 'legacy_ingredient') AS source,
                   l.target_product_id, l.target_variant
            FROM meal_addons ma
            JOIN ingredients i ON i.id = ma.ingredient_id
            LEFT JOIN prepared_stock_addon_links l ON
                l.product_id = ma.product_id AND l.ingredient_id = ma.ingredient_id
            WHERE ma.product_id = ?
            ORDER BY i.name
        """, (product_id,)).fetchall()
    return [dict(row) for row in rows]


def map_addon(owner, product_id, ingredient_id, source, target_product_id=None,
              target_variant=None):
    product_id = _checked_int(product_id, "Product ID", minimum=1)
    ingredient_id = _checked_int(ingredient_id, "Extra ID", minimum=1)
    if source not in ("legacy_ingredient", "self_extra", "prepared_other"):
        raise ValueError("Invalid add-on stock source.")
    with closing(get_connection()) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            # Match checkout base-product lock, preventing concurrent mapping
            # changes from silently changing how a confirmed order is charged.
            _lock_product(conn, product_id)
            base = conn.execute("""
                SELECT active FROM prepared_stock
                WHERE product_id = ? AND variant = 'standard'
            """, (product_id,)).fetchone()
            mapping = conn.execute("""
                SELECT source, target_product_id, target_variant
                FROM prepared_stock_addon_links
                WHERE product_id = ? AND ingredient_id = ?
            """, (product_id, ingredient_id)).fetchone()
            valid = conn.execute("""
                SELECT 1 FROM meal_addons
                WHERE product_id = ? AND ingredient_id = ?
            """, (product_id, ingredient_id)).fetchone()
            if not valid:
                raise LookupError("Choose an existing approved add-on for this menu item.")
            if source != "legacy_ingredient" and (not base or not base["active"]):
                raise ValueError("Activate the standard prepared meal before mapping its extras.")
            if source == "prepared_other":
                target_product_id = _checked_int(target_product_id, "Target product", minimum=1)
                target_variant = _variant(target_variant)
                if target_product_id == product_id and target_variant == "standard":
                    raise ValueError("Choose self-extra for servings from the same food pot.")
                target = stock_policy.active_prepared(
                    conn, target_product_id, target_variant, lock=True)
                if not target or not target["active"]:
                    raise ValueError("Target side must have active prepared stock.")
            else:
                target_product_id = None
                target_variant = None
            changed = (not mapping and source != "legacy_ingredient") or (
                mapping and (mapping["source"], mapping["target_product_id"],
                             mapping["target_variant"]) !=
                (None if source == "legacy_ingredient" else source,
                 target_product_id, target_variant))
            if changed:
                _require_no_open_orders(conn, product_id, "standard")
                if mapping and mapping["source"] == "prepared_other":
                    _require_no_open_orders(
                        conn, mapping["target_product_id"], mapping["target_variant"])
                if source == "prepared_other":
                    _require_no_open_orders(conn, target_product_id, target_variant)
                conn.execute("""DELETE FROM prepared_stock_addon_links
                    WHERE product_id = ? AND ingredient_id = ?""",
                    (product_id, ingredient_id))
                if source != "legacy_ingredient":
                    conn.execute("""INSERT INTO prepared_stock_addon_links
                        (product_id, ingredient_id, source, target_product_id, target_variant)
                        VALUES (?, ?, ?, ?, ?)""",
                        (product_id, ingredient_id, source,
                         target_product_id, target_variant))
                conn.execute("""INSERT INTO owner_audit_log
                    (actor, action, target_customer_id) VALUES (?, ?, ?)""",
                    (owner["email"],
                     f"prepared_addon:{product_id}:{ingredient_id}:{source}", owner["id"]))
            conn.commit()
            return {"product_id": product_id, "ingredient_id": ingredient_id,
                    "source": source, "target_product_id": target_product_id,
                    "target_variant": target_variant}
        except Exception:
            conn.rollback()
            raise


def create_self_extra(owner, product_id, display_name, max_quantity=2):
    """Add a customer-friendly extra choice; this is NOT raw-ingredient accounting.

    The existing addon identity tables provide an order customization ID.
    Stock and price for this option come exclusively from prepared_stock.
    """
    product_id = _checked_int(product_id, "Product ID", minimum=1)
    max_quantity = _checked_int(max_quantity, "Maximum extras", minimum=1, maximum=20)
    if not isinstance(display_name, str) or not 3 <= len(display_name.strip()) <= 80:
        raise ValueError("Extra name must have 3–80 characters.")
    display_name = display_name.strip()
    with closing(get_connection()) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            _lock_product(conn, product_id)
            base = stock_policy.active_prepared(conn, product_id, "standard")
            if not base or not base["active"]:
                raise ValueError("Activate standard prepared stock before adding extras.")
            _require_no_open_orders(conn, product_id, "standard")
            found = conn.execute("""SELECT id FROM ingredients
                WHERE LOWER(name) = LOWER(?)""", (display_name,)).fetchone()
            if found:
                ingredient_id = found["id"]
                used_elsewhere = conn.execute("""
                    SELECT 1 FROM meal_addons WHERE ingredient_id = ? AND product_id != ?
                    LIMIT 1
                """, (ingredient_id, product_id)).fetchone()
                if used_elsewhere:
                    raise ValueError("Choose a distinct extra name for this menu item.")
            else:
                conn.execute("""INSERT INTO ingredients(name, stock_quantity, available)
                    VALUES (?, 0, 1)""", (display_name,))
                ingredient_id = conn.execute("""SELECT id FROM ingredients
                    WHERE LOWER(name) = LOWER(?)""", (display_name,)).fetchone()["id"]
            conn.execute("""INSERT INTO meal_addons(product_id, ingredient_id, extra_price, max_quantity)
                VALUES (?, ?, ?, ?) ON CONFLICT(product_id, ingredient_id)
                DO UPDATE SET max_quantity = excluded.max_quantity""",
                (product_id, ingredient_id, base["extra_price"], max_quantity))
            # A previously legacy add-on cannot be silently switched while
            # unresolved orders exist; guarded above.
            conn.execute("""DELETE FROM prepared_stock_addon_links
                WHERE product_id = ? AND ingredient_id = ?""",
                (product_id, ingredient_id))
            conn.execute("""INSERT INTO prepared_stock_addon_links
                (product_id, ingredient_id, source)
                VALUES (?, ?, 'self_extra')""", (product_id, ingredient_id))
            conn.execute("""INSERT INTO owner_audit_log
                (actor, action, target_customer_id) VALUES (?, ?, ?)""",
                (owner["email"], f"prepared_extra:create:{product_id}:{ingredient_id}",
                 owner["id"]))
            conn.commit()
            return {"product_id": product_id, "ingredient_id": ingredient_id,
                    "name": display_name, "source": "self_extra",
                    "max_quantity": max_quantity}
        except Exception:
            conn.rollback()
            raise
