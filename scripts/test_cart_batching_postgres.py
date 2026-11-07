"""PostgreSQL-only old/new cart equivalence using identical fictional fixtures.

Requires disposable postgres:16 database from GitHub Actions; never shared DBs.
"""
import json
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from core.services.cart import CartService
from core.services.order_service import OrderService
from data.database import models, repository
from scripts.benchmark_cart_ab_postgres import DATABASE, old_view, old_stock


class CartPostgresEquivalence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if (os.getenv("NAIJA_BITES_POSTGRES_AB") != "1" or
                os.getenv("DATABASE_URL") != DATABASE):
            raise unittest.SkipTest("Requires disposable PostgreSQL A/B database")
        if os.getenv("OBSERVABILITY_MODE", "off") != "off":
            raise unittest.SkipTest("Raw tracing disabled for this test")
        models.create_tables()

    def setUp(self):
        self.session = "fictional-pg-cart-" + uuid4().hex
        self.other_session = "fictional-pg-other-" + uuid4().hex
        self.ids = []
        self.cart = CartService(self.session)
        with models.get_connection() as conn:
            for n in range(10):
                pid = conn.execute(
                    "INSERT INTO products (name,category,price,available) VALUES (?,?,?,?)",
                    ("Fictional meal " + self.session + str(n), "rice", 1000 + n * 100, 1),
                ).lastrowid
                self.ids.append(pid)
                for variant, count in (("standard", 20), ("large", 8)):
                    conn.execute("INSERT INTO inventory (product_id,variant,quantity) VALUES (?,?,?)",
                                 (pid, variant, count))
            for session in (self.session, self.other_session):
                conn.execute("INSERT INTO conversation_sessions (session_id,cart_data) VALUES (?,?)",
                             (session, '{"items":[]}'))
            conn.execute(
                "CREATE TABLE IF NOT EXISTS ingredients "
                "(id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                "stock_quantity INTEGER NOT NULL DEFAULT 0, available INTEGER NOT NULL DEFAULT 1)"
            )
            self.ingredient = 1000000 + int(uuid4().hex[:6], 16)
            conn.execute("INSERT INTO ingredients(id,name,stock_quantity,available) VALUES (?,?,?,?)",
                         (self.ingredient, "Fictional extra", 10, 1))

    def put(self, items, session=None):
        with models.get_connection() as conn:
            conn.execute("UPDATE conversation_sessions SET cart_data=? WHERE session_id=?",
                         (json.dumps({"items": items}), session or self.session))

    def items(self, n):
        return [{"product_id": pid, "variant": "standard", "quantity": 1,
                 "modifications": []} for pid in self.ids[:n]]

    def equivalent(self, items):
        self.put(items)
        self.assertEqual(old_view(self.cart), self.cart.view_cart())
        self.assertEqual(old_stock(self.cart, items), self.cart._check_all_stock(items))

    def test_empty_1_5_10_order_duplicates_and_variants(self):
        for n in (0, 1, 5, 10):
            with self.subTest(n=n):
                self.equivalent(self.items(n))
        p, q = self.ids[:2]
        duplicated = [
            {"product_id": q, "variant": "large", "quantity": 2, "modifications": []},
            {"product_id": p, "variant": "standard", "quantity": 3, "modifications": []},
            {"product_id": q, "variant": "large", "quantity": 7, "modifications": []},
        ]
        self.equivalent(duplicated)
        self.assertEqual([x["product_id"] for x in self.cart.view_cart()["items"]], [q, p, q])
        self.assertEqual(self.cart._check_all_stock(duplicated),
                         "Not enough meal stock for the full cart.")

    def test_missing_unavailable_and_out_of_stock(self):
        lines = self.items(2) + [
            {"product_id": 99999999, "variant": "standard", "quantity": 1, "modifications": []},
            {"product_id": self.ids[0], "variant": "missing", "quantity": 1, "modifications": []},
        ]
        self.equivalent(lines)
        with models.get_connection() as conn:
            conn.execute("UPDATE products SET available=0 WHERE id=?", (self.ids[0],))
            conn.execute("UPDATE inventory SET quantity=0 WHERE product_id=?", (self.ids[1],))
        self.equivalent(lines)
        self.assertEqual(self.cart.view_cart()["items"][0]["product_id"], self.ids[0])

    def test_customizations_and_stock_errors(self):
        mod = [{"action": "add", "ingredient_id": self.ingredient, "name": "Fictional extra",
                "quantity": 2, "unit_price_delta": 125}]
        item = self.items(1)[0]
        item["quantity"] = 2
        item["modifications"] = mod
        self.equivalent([item])
        self.assertEqual(self.cart.view_cart()["subtotal"], 2500)
        with models.get_connection() as conn:
            conn.execute("UPDATE ingredients SET stock_quantity=1 WHERE id=?", (self.ingredient,))
        self.equivalent([item])
        self.assertIn(str(self.ingredient), self.cart._check_all_stock([item]))
        # Actual invalid meal combinations remain subject to checkout's independent validation.
        self.assertFalse(self.cart.add_item(self.ids[0], variant="large", add={"bad": 1})["success"])

    def test_account_cart_isolation_and_discount_policy(self):
        owner_items = self.items(5)
        other_items = self.items(1)
        self.put(owner_items)
        self.put(other_items, self.other_session)
        self.assertEqual(old_view(self.cart), self.cart.view_cart())
        other = CartService(self.other_session)
        self.assertEqual(old_view(other), other.view_cart())
        self.assertNotEqual(self.cart.view_cart()["subtotal"], other.view_cart()["subtotal"])
        # The discount policy belongs to checkout, not cart display.
        subtotal = self.cart.view_cart()["subtotal"]
        self.assertEqual(OrderService._calculate_discount(subtotal),
                         OrderService._calculate_discount(old_view(self.cart)["subtotal"]))

    def test_large_cart_chunking_matches_old(self):
        # Repeated variants force dedupe and chunks only for unique identifiers.
        items = self.items(10) * 45
        self.equivalent(items)
        self.assertEqual(len(self.cart.view_cart()["items"]), 450)
        # Over 400 distinct keys too: missing products never become visible.
        with models.get_connection() as conn:
            additional = []
            for i in range(401):
                pid = conn.execute(
                    "INSERT INTO products (name,category,price,available) VALUES (?,?,?,?)",
                    ("fictional-chunk-" + self.session + str(i), "rice", 100, 1),
                ).lastrowid
                additional.append(pid)
                conn.execute("INSERT INTO inventory (product_id,variant,quantity) VALUES (?,?,?)",
                             (pid, "standard", 9))
        self.equivalent([{"product_id": pid, "variant": "standard", "quantity": 1,
                          "modifications": []} for pid in additional])


if __name__ == "__main__":
    unittest.main()
