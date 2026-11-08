"""Disposable PostgreSQL equivalence and controlled read-only batching A/B.

No production connection, external provider, or migration. PG16 CI only.
"""
import json
import os
import statistics
import time
import unittest
import uuid
from unittest.mock import patch

from core.services.cart import CartService
from data.database import models, repository

_ALLOWED = "postgresql://postgres:postgres@127.0.0.1:5432/naija_test"


class CartBatchingPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.getenv("NAIJA_BITES_POSTGRES_CI") != "1" or os.getenv("DATABASE_URL") != _ALLOWED:
            raise unittest.SkipTest("Disposable CI PostgreSQL only")
        models.create_tables()
        cls.prefix = uuid.uuid4().hex[:12]
        cls.session_id = "batch-pg-" + cls.prefix
        cls.ids = []
        with models.get_connection() as conn:
            for i in range(10):
                pid = conn.execute(
                    "INSERT INTO products(name,category,price,available) VALUES (?,?,?,?)",
                    (f"Fictional Batch PG {cls.prefix} {i}", "rice", 1000 + i * 100, 1),
                ).lastrowid
                cls.ids.append(pid)
                for variant, qty in (("standard", 20), ("large", 8)):
                    conn.execute(
                        "INSERT INTO inventory(product_id,variant,quantity) VALUES (?,?,?)",
                        (pid, variant, qty),
                    )
            conn.execute(
                "INSERT INTO conversation_sessions(session_id,cart_data) VALUES (?,?)",
                (cls.session_id, '{"items":[]}'),
            )

    @classmethod
    def tearDownClass(cls):
        if not hasattr(cls, "ids"):
            return
        with models.get_connection() as conn:
            conn.execute("DELETE FROM conversation_sessions WHERE session_id=?", (cls.session_id,))
            for pid in cls.ids:
                conn.execute("DELETE FROM inventory WHERE product_id=?", (pid,))
                conn.execute("DELETE FROM products WHERE id=?", (pid,))

    def items(self, n):
        return [{"product_id": pid, "variant": "standard", "quantity": 1,
                 "modifications": []} for pid in self.ids[:n]]

    def save(self, items):
        with models.get_connection() as conn:
            conn.execute(
                "UPDATE conversation_sessions SET cart_data=? WHERE session_id=?",
                (json.dumps({"items": items}), self.session_id),
            )

    def original_view(self, items):
        detailed, subtotal = [], 0
        for item in items:
            product = repository.get_product(item["product_id"])
            if not product:
                continue
            mods = item.get("modifications", [])
            extra = sum(m["quantity"] * m["unit_price_delta"]
                        for m in mods if m["action"] == "add")
            unit_price = product["price"] + extra
            line_total = unit_price * item["quantity"]
            subtotal += line_total
            detailed.append({"product_id": item["product_id"], "name": product["name"],
                             "variant": item.get("variant", "standard"),
                             "quantity": item["quantity"], "modifications": mods,
                             "unit_price": unit_price, "line_total": line_total})
        return {"items": detailed, "subtotal": subtotal, "currency": "NGN"}

    def original_stock(self, items):
        from collections import Counter
        totals = Counter()
        for item in items:
            totals[(item["product_id"], item.get("variant", "standard"))] += item["quantity"]
        for (pid, variant), qty in totals.items():
            if repository.get_stock(pid, variant) < qty:
                return "Not enough meal stock for the full cart."
        return None

    def test_equivalence_variants_duplicates_prices_and_missing_stock(self):
        a, b = self.ids[:2]
        items = [
            {"product_id": b, "variant": "large", "quantity": 2, "modifications": []},
            {"product_id": a, "variant": "standard", "quantity": 3, "modifications": []},
            {"product_id": b, "variant": "large", "quantity": 7, "modifications": []},
            {"product_id": 999999999, "variant": "standard", "quantity": 1, "modifications": []},
        ]
        self.save(items)
        cart = CartService(self.session_id)
        self.assertEqual(cart.view_cart(), self.original_view(items))
        self.assertEqual(cart._check_all_stock(items), self.original_stock(items))
        self.assertEqual(cart._check_all_stock(items), "Not enough meal stock for the full cart.")
        self.assertEqual([x["product_id"] for x in cart.view_cart()["items"]], [b, a, b])
        self.assertEqual(repository.get_stocks_by_variants([(a, "missing")]), {})
        self.assertEqual(repository.get_products_by_ids([999999999]), {})

    def test_chunking_one_connection_and_unchanged_inventory(self):
        before = {pid: repository.get_stock(pid) for pid in self.ids}
        with patch.object(repository, "get_connection", wraps=repository.get_connection) as opened:
            self.assertEqual(repository.get_products_by_ids([]), {})
            self.assertEqual(repository.get_stocks_by_variants([]), {})
            self.assertEqual(opened.call_count, 0)
            self.assertEqual(len(repository.get_products_by_ids(self.ids + list(range(900000, 900500)))), 10)
            self.assertEqual(opened.call_count, 1)
            self.assertEqual(len(repository.get_stocks_by_variants(
                [(pid, "standard") for pid in self.ids] +
                [(pid, "missing") for pid in range(900000, 900500)])), 10)
            self.assertEqual(opened.call_count, 2)
        self.assertEqual(before, {pid: repository.get_stock(pid) for pid in self.ids})

    def test_controlled_ab_against_original_main_implementation(self):
        cart = CartService(self.session_id)
        for n in (1, 5, 10):
            items = self.items(n)
            self.save(items)
            baseline_view = self.original_view(items)
            self.assertEqual(cart.view_cart(), baseline_view)
            self.assertEqual(cart._check_all_stock(items), self.original_stock(items))
            timings = {"main_cart": [], "batch_cart": [], "main_stock": [], "batch_stock": []}
            variants = [(x["product_id"], "standard") for x in items]
            # Same fixture, runner, process and DB. Alternate order to limit drift.
            actions = {
                "main_cart": lambda: [repository.get_product(x["product_id"]) for x in items],
                "batch_cart": lambda: repository.get_products_by_ids(x["product_id"] for x in items),
                "main_stock": lambda: [repository.get_stock(pid, variant) for pid, variant in variants],
                "batch_stock": lambda: repository.get_stocks_by_variants(variants),
            }
            for iteration in range(35):
                order = list(actions) if iteration % 2 == 0 else list(reversed(actions))
                for key in order:
                    start = time.perf_counter()
                    actions[key]()
                    if iteration >= 5:
                        timings[key].append((time.perf_counter() - start) * 1000)
            result = {key: round(statistics.median(values), 3) for key, values in timings.items()}
            result.update({"items": n, "samples": 30,
                           "main_cart_queries": n, "batch_cart_queries": 1,
                           "main_stock_queries": n, "batch_stock_queries": 1})
            print("CART_BATCH_PG_AB " + json.dumps(result), flush=True)
            self.assertEqual(len(timings["batch_cart"]), 30)


if __name__ == "__main__":
    unittest.main()
