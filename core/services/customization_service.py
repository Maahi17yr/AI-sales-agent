"""Validate and quote meal modifications; does not reserve stock or mutate carts."""
from collections import Counter
from data.database.models import get_connection
from core.services import stock_policy


class CustomizationService:
    @staticmethod
    def get_options(product_id):
        """List the allowed removals and additions for one meal."""
        if type(product_id) is not int or product_id <= 0:
            return {"success": False, "message": "Invalid product ID."}
        conn = get_connection()
        try:
            product = conn.execute(
                "SELECT id, name, price, available FROM products WHERE id = ?",
                (product_id,),
            ).fetchone()
            if not product or not product["available"]:
                return {"success": False, "message": "Meal is unavailable."}
            removals = [dict(r) for r in conn.execute("""
                SELECT i.id AS ingredient_id, i.name, mi.included_quantity
                FROM meal_ingredients mi JOIN ingredients i ON i.id = mi.ingredient_id
                WHERE mi.product_id = ? AND mi.removable = 1
                ORDER BY i.name
            """, (product_id,))]
            extra_rows = [dict(r) for r in conn.execute("""
                SELECT i.id AS ingredient_id, i.name, ma.extra_price,
                       ma.max_quantity, i.stock_quantity, i.available
                FROM meal_addons ma JOIN ingredients i ON i.id = ma.ingredient_id
                WHERE ma.product_id = ? ORDER BY i.name
            """, (product_id,))]
            extras = []
            base = stock_policy.active_prepared(conn, product_id, "standard")
            for entry in extra_rows:
                selection = stock_policy.addon_selection(
                    conn, product_id, "standard", entry["ingredient_id"])
                price = entry["extra_price"]
                available = bool(entry["available"] and entry["stock_quantity"] > 0)
                if selection and selection["source"] == "self_extra":
                    price = selection["price"]
                    available = bool(entry["available"] and base and
                        base["quantity_units"] >=
                        base["serving_units"] + base["tolerance_units"] +
                        base["extra_units"] + base["extra_tolerance_units"])
                elif selection and selection["source"] == "prepared_other":
                    target = stock_policy.active_prepared(
                        conn, selection["target_product_id"], selection["target_variant"])
                    available = bool(entry["available"] and target and target["active"] and
                        target["quantity_units"] >=
                        target["serving_units"] + target["tolerance_units"])
                # Never send physical ingredient balances or serving units to an AI.
                extras.append({"ingredient_id": entry["ingredient_id"],
                               "name": entry["name"], "extra_price": price,
                               "max_quantity": entry["max_quantity"],
                               "available": available})
            return {"success": True, "product_id": product_id,
                    "meal": product["name"], "base_price": product["price"],
                    "removable": removals, "extras": extras}
        finally:
            conn.close()

    @staticmethod
    def quote(product_id, quantity=1, remove=None, add=None):
        """Validate modifications and return a price quote, without changing stock.

        remove: list of ingredient names, e.g. ['Boiled Egg']
        add: dict of ingredient name -> quantity PER MEAL, e.g. {'Fried Plantain': 1}
        """
        if type(product_id) is not int or product_id <= 0:
            return {"success": False, "message": "Invalid product ID."}
        if type(quantity) is not int or quantity <= 0:
            return {"success": False, "message": "Meal quantity must be positive."}
        remove = [] if remove is None else remove
        add = {} if add is None else add
        if not isinstance(remove, list) or not isinstance(add, dict):
            return {"success": False, "message": "Invalid customization format."}
        if any(not isinstance(x, str) or not x.strip() for x in remove):
            return {"success": False, "message": "Invalid ingredient to remove."}
        if any(not isinstance(k, str) or not k.strip() or type(v) is not int or v <= 0
               for k, v in add.items()):
            return {"success": False, "message": "Extras need positive whole-number quantities."}
        remove_names = [x.strip().casefold() for x in remove]
        if len(set(remove_names)) != len(remove_names):
            return {"success": False, "message": "An ingredient cannot be removed twice."}
        add_names = {k.strip().casefold(): v for k, v in add.items()}
        if len(add_names) != len(add):
            return {"success": False, "message": "Duplicate extra ingredient names."}

        conn = get_connection()
        try:
            product = conn.execute(
                "SELECT id, name, price, available FROM products WHERE id = ?",
                (product_id,),
            ).fetchone()
            if not product or not product["available"]:
                raise ValueError("Meal is unavailable.")
            removable = {r["name"].casefold(): dict(r) for r in conn.execute("""
                SELECT i.id, i.name, mi.included_quantity
                FROM meal_ingredients mi JOIN ingredients i ON i.id = mi.ingredient_id
                WHERE mi.product_id = ? AND mi.removable = 1
            """, (product_id,))}
            addons = {r["name"].casefold(): dict(r) for r in conn.execute("""
                SELECT i.id, i.name, i.stock_quantity, i.available,
                       ma.extra_price, ma.max_quantity
                FROM meal_addons ma JOIN ingredients i ON i.id = ma.ingredient_id
                WHERE ma.product_id = ?
            """, (product_id,))}
            modifications = []
            for name in remove_names:
                ingredient = removable.get(name)
                if ingredient is None:
                    raise ValueError(f"Cannot remove '{name}' from this meal.")
                modifications.append({"action": "remove", "ingredient_id": ingredient["id"],
                                      "name": ingredient["name"],
                                      "quantity": ingredient["included_quantity"],
                                      "unit_price_delta": 0})
            extra_cost = 0
            for name, count in add_names.items():
                ingredient = addons.get(name)
                if ingredient is None:
                    raise ValueError(f"'{name}' is not an approved extra for this meal.")
                if count > ingredient["max_quantity"]:
                    raise ValueError(f"Maximum {ingredient['max_quantity']} extra '{ingredient['name']}' per meal.")
                selection = stock_policy.addon_selection(
                    conn, product_id, "standard", ingredient["id"])
                if not ingredient["available"] or (
                    selection is None and ingredient["stock_quantity"] < count * quantity
                ):
                    raise ValueError(f"Not enough '{ingredient['name']}' available.")
                price = (selection["price"] if selection and
                         selection["source"] == "self_extra"
                         else ingredient["extra_price"])
                extra_cost += price * count
                modifications.append({"action": "add", "ingredient_id": ingredient["id"],
                                      "name": ingredient["name"], "quantity": count,
                                      "unit_price_delta": price})
            # A prepared meal + same-pot extra must fit together. Checking
            # the extra alone would allow overselling the last serving.
            if stock_policy.active_in_cart(conn, [{"product_id": product_id}]):
                legacy, addons, prepared = stock_policy.demand(conn, [{
                    "product_id": product_id, "variant": "standard",
                    "quantity": quantity, "modifications": modifications,
                }])
                stock_policy.validate(conn, legacy, addons, prepared)
            unit_price = product["price"] + extra_cost
            return {"success": True, "product_id": product_id, "meal": product["name"],
                    "quantity": quantity, "base_price": product["price"],
                    "extra_cost_per_meal": extra_cost, "unit_price": unit_price,
                    "line_total": unit_price * quantity,
                    "modifications": sorted(modifications, key=lambda x: (x["action"], x["ingredient_id"])),
                    "currency": "NGN", "stock_reserved": False}
        except ValueError as error:
            return {"success": False, "message": str(error)}
        finally:
            conn.close()
