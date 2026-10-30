"""Cart with independently identifiable meal customizations.

IMPORTANT: Customized checkout must remain disabled until OrderService is upgraded
 to reprice, persist, and deduct ingredient stock atomically.
"""
import json
from collections import Counter

from data.database import repository
from data.database.models import get_connection
from application.services.customization_service import CustomizationService


class CartService:
    def __init__(self, session_id):
        self.session_id = session_id

    def _load_session(self):
        session = repository.get_session(self.session_id)
        if session is None:
            return {"cart_data": {"items": []}, "chat_history": [],
                    "customer_id": None, "awaiting_confirmation": False}
        return session

    def _save_cart(self, items):
        session = self._load_session()
        cart_data = dict(session.get("cart_data") or {})
        cart_data["items"] = items
        cart_data.pop("pending_checkout", None)  # Changes require fresh review.
        repository.save_session(
            session_id=self.session_id, cart_data=cart_data,
            chat_history=session.get("chat_history") or [],
            customer_id=session.get("customer_id"), awaiting_confirmation=False,
        )

    def get_cart(self):
        return self._load_session().get("cart_data", {}).get("items", [])

    @staticmethod
    def _mod_key(modifications):
        return json.dumps(modifications, sort_keys=True, separators=(",", ":"))

    def _check_all_stock(self, items):
        """Validate totals across standard/customized cart lines (no reservations)."""
        product_totals = Counter()
        extra_totals = Counter()
        for item in items:
            product_totals[(item["product_id"], item.get("variant", "standard"))] += item["quantity"]
            for mod in item.get("modifications", []):
                if mod["action"] == "add":
                    extra_totals[mod["ingredient_id"]] += mod["quantity"] * item["quantity"]
        for (product_id, variant), count in product_totals.items():
            if repository.get_stock(product_id, variant) < count:
                return "Not enough meal stock for the full cart."
        if extra_totals:
            conn = get_connection()
            try:
                for ingredient_id, count in extra_totals.items():
                    row = conn.execute(
                        "SELECT name, stock_quantity, available FROM ingredients WHERE id = ?",
                        (ingredient_id,),
                    ).fetchone()
                    if not row or not row["available"] or row["stock_quantity"] < count:
                        return f"Not enough ingredient stock for the full cart (ingredient {ingredient_id})."
            finally:
                conn.close()
        return None

    def add_item(self, product_id, quantity=1, variant="standard", remove=None, add=None):
        if type(product_id) is not int or type(quantity) is not int or quantity <= 0:
            return {"success": False, "message": "Invalid product or quantity."}
        if not isinstance(variant, str) or not variant.strip():
            return {"success": False, "message": "Invalid variant."}
        product = repository.get_product(product_id)
        if not product or not product["available"]:
            return {"success": False, "message": "Product unavailable."}
        custom = bool(remove or add)
        if custom and variant != "standard":
            return {"success": False, "message": "Customized meals currently require the standard variant."}
        modifications = []
        if custom:
            quote = CustomizationService.quote(product_id, quantity, remove, add)
            if not quote["success"]:
                return quote
            modifications = quote["modifications"]
        items = self.get_cart()
        key = self._mod_key(modifications)
        existing = next((item for item in items
                         if item["product_id"] == product_id
                         and item.get("variant", "standard") == variant
                         and self._mod_key(item.get("modifications", [])) == key), None)
        if existing:
            existing["quantity"] += quantity
        else:
            items.append({"product_id": product_id, "variant": variant,
                          "quantity": quantity, "modifications": modifications})
        error = self._check_all_stock(items)
        if error:
            return {"success": False, "message": error}
        self._save_cart(items)
        return {"success": True, "message": f"{product['name']} added to cart.",
                "cart": self.view_cart()}

    def update_quantity(self, product_id, quantity, variant="standard", modifications=None):
        if type(quantity) is not int or quantity < 0:
            return {"success": False, "message": "Invalid quantity."}
        items = self.get_cart()
        matches = [item for item in items if item["product_id"] == product_id
                   and item.get("variant", "standard") == variant]
        if modifications is not None:
            key = self._mod_key(modifications)
            matches = [item for item in matches if self._mod_key(item.get("modifications", [])) == key]
        if len(matches) != 1:
            return {"success": False, "message": "Specify the exact cart customization." if matches else "Item not in cart."}
        target = matches[0]
        if quantity == 0:
            items.remove(target)
        else:
            target["quantity"] = quantity
        error = self._check_all_stock(items)
        if error:
            return {"success": False, "message": error}
        self._save_cart(items)
        return {"success": True, "message": "Cart updated.", "cart": self.view_cart()}

    def remove_item(self, product_id, variant="standard", modifications=None):
        return self.update_quantity(product_id, 0, variant, modifications)

    def view_cart(self):
        detailed, subtotal = [], 0
        for item in self.get_cart():
            product = repository.get_product(item["product_id"])
            if not product:
                continue
            mods = item.get("modifications", [])
            extra_cost = sum(m["quantity"] * m["unit_price_delta"]
                             for m in mods if m["action"] == "add")
            unit_price = product["price"] + extra_cost
            line_total = unit_price * item["quantity"]
            subtotal += line_total
            detailed.append({"product_id": item["product_id"], "name": product["name"],
                             "variant": item.get("variant", "standard"),
                             "quantity": item["quantity"], "modifications": mods,
                             "unit_price": unit_price, "line_total": line_total})
        return {"items": detailed, "subtotal": subtotal, "currency": "NGN"}

    def clear_cart(self):
        self._save_cart([])
        return {"success": True, "message": "Cart cleared."}
