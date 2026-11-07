"""Cart batching equivalence on disposable SQLite, with no provider calls."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.services.cart import CartService
from data.database import models, repository

os.environ["DATABASE_URL"] = ""


class CartBatchingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(models, "DB_PATH", Path(self.tmp.name) / "cart.db")
        self.db_patch.start()
        models.create_tables()
        # The minimal application schema doesn't create the optional ingredient
        # tables until menu/customization setup; provide this fictional fixture.
        with models.get_connection() as conn:
            conn.execute("CREATE TABLE ingredients (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                         "stock_quantity INTEGER NOT NULL DEFAULT 0, available INTEGER NOT NULL DEFAULT 1)")
        self.session = "fictional-cart-session"
        self.cart = CartService(self.session)
        self.ids = []
        with models.get_connection() as conn:
            for i in range(10):
                pid = conn.execute("INSERT INTO products (name,category,price,available) VALUES (?,?,?,?)",
                                   (f"Fictional meal {i}", "rice", 1000 + i * 100, 1)).lastrowid
                self.ids.append(pid)
                for variant, quantity in (("standard", 20), ("large", 8)):
                    conn.execute("INSERT INTO inventory(product_id,variant,quantity) VALUES (?,?,?)",
                                 (pid, variant, quantity))
            conn.execute("INSERT INTO conversation_sessions(session_id,cart_data) VALUES (?,?)",
                         (self.session, '{"items":[]}'))

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def items(self, n, *, variant="standard"):
        return [{"product_id": pid, "variant": variant, "quantity": 1, "modifications": []}
                for pid in self.ids[:n]]

    def load(self, items):
        with models.get_connection() as conn:
            conn.execute("UPDATE conversation_sessions SET cart_data=? WHERE session_id=?",
                         (json.dumps({"items": items}), self.session))

    def original_view(self, items):
        detailed, subtotal = [], 0
        for item in items:
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

    def original_stock(self, items):
        from collections import Counter
        product_totals, extra_totals = Counter(), Counter()
        for item in items:
            product_totals[(item["product_id"], item.get("variant", "standard"))] += item["quantity"]
            for mod in item.get("modifications", []):
                if mod["action"] == "add":
                    extra_totals[mod["ingredient_id"]] += mod["quantity"] * item["quantity"]
        for (pid, variant), count in product_totals.items():
            if repository.get_stock(pid, variant) < count:
                return "Not enough meal stock for the full cart."
        if extra_totals:
            conn = models.get_connection()
            try:
                for ingredient_id, count in extra_totals.items():
                    row = conn.execute("SELECT name, stock_quantity, available FROM ingredients WHERE id = ?",
                                       (ingredient_id,)).fetchone()
                    if not row or not row["available"] or row["stock_quantity"] < count:
                        return f"Not enough ingredient stock for the full cart (ingredient {ingredient_id})."
            finally:
                conn.close()
        return None

    def assert_equivalent(self, items):
        self.load(items)
        self.assertEqual(self.cart.view_cart(), self.original_view(items))
        self.assertEqual(self.cart._check_all_stock(items), self.original_stock(items))

    def test_empty_and_1_5_10_item_carts(self):
        for n in (0, 1, 5, 10):
            with self.subTest(n=n):
                self.assert_equivalent(self.items(n))

    def test_variants_quantities_duplicates_and_display_order(self):
        a, b = self.ids[:2]
        items = [
            {"product_id": b, "quantity": 2, "variant": "large", "modifications": []},
            {"product_id": a, "quantity": 3, "variant": "standard", "modifications": []},
            {"product_id": b, "quantity": 7, "variant": "large", "modifications": []},
        ]
        self.assert_equivalent(items)
        self.assertEqual([i["product_id"] for i in self.cart.view_cart()["items"]], [b, a, b])
        self.assertEqual(self.cart._check_all_stock(items), "Not enough meal stock for the full cart.")

    def test_missing_products_and_unavailable_variants(self):
        items = self.items(2) + [
            {"product_id": 9999999, "quantity": 1, "variant": "standard", "modifications": []},
            {"product_id": self.ids[0], "quantity": 1, "variant": "nonexistent", "modifications": []},
        ]
        self.assert_equivalent(items)
        with models.get_connection() as conn:
            conn.execute("UPDATE products SET available=0 WHERE id=?", (self.ids[0],))
            conn.execute("UPDATE inventory SET quantity=0 WHERE product_id=?", (self.ids[1],))
        self.assert_equivalent(items)
        self.assertEqual(self.cart.view_cart()["items"][0]["product_id"], self.ids[0])

    def test_customization_pricing_and_ingredient_validation(self):
        item = self.items(1)[0]
        item["quantity"] = 2
        item["modifications"] = [{
            "action": "add", "ingredient_id": 987654, "name": "Fictional Extra",
            "quantity": 2, "unit_price_delta": 125,
        }, {"action": "remove", "ingredient_id": 987653, "name": "Fictional Removal",
             "quantity": 1, "unit_price_delta": 0}]
        self.assert_equivalent([item])
        self.assertEqual(self.cart.view_cart()["subtotal"], 2500)
        self.assertIn("ingredient 987654", self.cart._check_all_stock([item]))

    def test_no_connections_for_empty_lookup_and_single_batch(self):
        with patch.object(repository, "get_connection", wraps=repository.get_connection) as connection:
            self.assertEqual(repository.get_products_by_ids([]), {})
            self.assertEqual(repository.get_stocks_by_variants([]), {})
            self.assertEqual(connection.call_count, 0)
            self.assertEqual(len(repository.get_products_by_ids(self.ids)), 10)
            self.assertEqual(len(repository.get_stocks_by_variants(
                [(pid, "standard") for pid in self.ids])), 10)
            self.assertEqual(connection.call_count, 2)

    def test_large_lists_chunk_without_losing_single_connection_semantics(self):
        with patch.object(repository, "get_connection", wraps=repository.get_connection) as connections:
            products = repository.get_products_by_ids(self.ids + list(range(100000, 100500)))
            self.assertEqual(set(products), set(self.ids))
            self.assertEqual(connections.call_count, 1)
            variants = [(pid, "standard") for pid in self.ids] + [
                (pid, "nonexistent") for pid in range(100000, 100500)
            ]
            stocks = repository.get_stocks_by_variants(variants)
            self.assertEqual(len(stocks), 10)
            self.assertEqual(connections.call_count, 2)

    def test_unchanged_validation_for_invalid_customizations(self):
        # Existing public API must still reject customizations outside permitted variants.
        result = self.cart.add_item(self.ids[0], variant="large", add={"Not An Addon": 1})
        self.assertFalse(result["success"])
        self.assertIn("standard variant", result["message"])


if __name__ == "__main__":
    unittest.main()
