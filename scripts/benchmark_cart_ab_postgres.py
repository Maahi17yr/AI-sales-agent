"""Same-runner A/B on disposable PostgreSQL, with untouched cart service logic.

Only allowed against the CI naija_ab database. No production, payment, or model calls.
"""
import argparse
from collections import Counter
from contextlib import contextmanager
import json
import logging
import os
import platform
import statistics
import time
from unittest.mock import patch
from uuid import uuid4

from core.observability import metrics
from core.services.cart import CartService
from data.database import models, postgres, repository


DATABASE = "postgresql://postgres:postgres@127.0.0.1:5432/naija_ab"


def guard():
    if (os.getenv("NAIJA_BITES_POSTGRES_AB") != "1" or
            os.getenv("DATABASE_URL") != DATABASE or
            os.getenv("OBSERVABILITY_MODE", "off") != "off"):
        raise RuntimeError("Refusing non-disposable PostgreSQL A/B configuration.")


def percentile(samples, pct):
    ordered = sorted(samples)
    return ordered[max(0, (len(ordered) * pct + 99) // 100 - 1)]


class DbMetrics(logging.Handler):
    def __init__(self):
        super().__init__()
        self.count = 0

    def emit(self, record):
        if str(record.msg).startswith("db_metric "):
            self.count += 1


def old_view(self):
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


def old_stock(self, items):
    product_totals = Counter()
    extra_totals = Counter()
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
                row = conn.execute(
                    "SELECT name, stock_quantity, available FROM ingredients WHERE id = ?",
                    (ingredient_id,),
                ).fetchone()
                if not row or not row["available"] or row["stock_quantity"] < count:
                    return f"Not enough ingredient stock for the full cart (ingredient {ingredient_id})."
        finally:
            conn.close()
    return None


def run(iterations=30, warmup=5, repetitions=3):
    guard()
    if iterations < 30 or warmup < 5 or repetitions < 2:
        raise ValueError("Require >=30 measured, >=5 warmups and >=2 paired repetitions.")
    models.create_tables()
    label = "fictional-ab-" + uuid4().hex[:12]
    customer = "customer-" + label
    ids = []
    with models.get_connection() as conn:
        for index in range(10):
            pid = conn.execute(
                "INSERT INTO products (name,category,price,available) VALUES (?,?,?,?)",
                (f"Fictional A/B meal {label} {index}", "rice", 2000 + index * 100, 1),
            ).lastrowid
            ids.append(pid)
            for variant, quantity in (("standard", 100), ("large", 20)):
                conn.execute(
                    "INSERT INTO inventory (product_id,variant,quantity) VALUES (?,?,?)",
                    (pid, variant, quantity),
                )
        conn.execute(
            "INSERT INTO conversation_sessions(session_id,cart_data) VALUES (?,?)",
            (customer, '{"items":[]}'),
        )

    counters = DbMetrics()
    logger = logging.getLogger("naija_bites.metrics")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(counters)
    connects = [0]
    original_connect = postgres.psycopg.connect

    def counted_connect(*args, **kwargs):
        connects[0] += 1
        return original_connect(*args, **kwargs)

    results = []

    def prepare(items):
        # Done once per workload, outside warmups and measured operations.
        with models.get_connection() as conn:
            conn.execute("UPDATE conversation_sessions SET cart_data=? WHERE session_id=?",
                         (json.dumps({"items": items}), customer))

    def measure(fn, expected, operation):
        before_conn, before_query = connects[0], counters.count
        started = time.perf_counter()
        try:
            actual = fn()
            ok = actual == expected
        except Exception:
            # Do not print exception text; it may contain private details.
            ok = False
        elapsed = (time.perf_counter() - started) * 1000
        return (elapsed, counters.count - before_query, connects[0] - before_conn, ok)

    with patch.object(postgres.psycopg, "connect", side_effect=counted_connect):
        try:
            for size in (1, 5, 10):
                items = [{"product_id": pid, "quantity": 1, "variant": "standard",
                          "modifications": []} for pid in ids[:size]]
                prepare(items)
                cart = CartService(customer)
                for operation, original, optimized in (
                    ("cart_view", lambda: old_view(cart), lambda: cart.view_cart()),
                    ("stock_validation", lambda: old_stock(cart, items),
                     lambda: cart._check_all_stock(items)),
                ):
                    expected = original()
                    if optimized() != expected:
                        raise AssertionError("A/B results differ before measurement")
                    records = {"original": [], "batched": []}
                    for repeat in range(repetitions):
                        for iteration in range(warmup + iterations):
                            # Reverse the order every iteration; repetition parity
                            # shifts which version gets the first warmed slot.
                            order = ("original", "batched") if (repeat + iteration) % 2 == 0 else (
                                "batched", "original")
                            for name in order:
                                fn = original if name == "original" else optimized
                                observed = measure(fn, expected, operation)
                                if iteration >= warmup:
                                    records[name].append(observed)
                    def stats(data):
                        times = [entry[0] for entry in data]
                        return {
                            "p50_ms": round(percentile(times, 50), 3),
                            "p95_ms": round(percentile(times, 95), 3),
                            "mean_ms": round(statistics.mean(times), 3),
                            "queries_per_call": round(statistics.mean(x[1] for x in data), 2),
                            "connections_per_call": round(statistics.mean(x[2] for x in data), 2),
                            "errors": sum(not x[3] for x in data),
                        }
                    before, after = stats(records["original"]), stats(records["batched"])
                    if before["errors"] or after["errors"]:
                        raise AssertionError("A/B results failed equivalence")
                    delta = (before["p50_ms"] - after["p50_ms"]) / before["p50_ms"] * 100
                    result = {
                        "scenario": operation, "items": size,
                        "iterations_per_repeat": iterations, "warmup_per_repeat": warmup,
                        "repetitions": repetitions, "samples_per_variant": iterations * repetitions,
                        "original": before, "batched": after,
                        "p50_reduction_percent": round(delta, 2),
                    }
                    results.append(result)
                    print("AB_RESULT " + json.dumps(result, sort_keys=True))
        finally:
            logger.removeHandler(counters)
            logger.setLevel(old_level)

    print("AB_ENV " + json.dumps({
        "database": "disposable PostgreSQL 16 on CI localhost",
        "python": platform.python_version(),
        "model_calls": 0, "provider_calls": 0,
        "alternating_order": True, "repetitions": repetitions,
    }, sort_keys=True))
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    run(args.iterations, args.warmup, args.repetitions)


if __name__ == "__main__":
    main()
