"""Paired same-runner pool experiment using a strictly disposable database.

Three repetitions; 5 warmups + 30 measurements per mode per workload.
Alternates pooled/unpooled order. All provider responses are mocked.
"""
import argparse
import contextlib
import concurrent.futures
import json
import logging
import os
import platform
import statistics
import time
from unittest.mock import patch
from uuid import uuid4

from core.services.cart import CartService
from core.services.checkout import CheckoutService
from core.services import auth_service, paystack_service
from core.services.order_service import fingerprint
from data.database import models, postgres, pool_experiment

DATABASE = pool_experiment._ALLOWED_URL


def percentile(samples, pct):
    ordered = sorted(samples)
    return ordered[max(0, (len(ordered) * pct + 99) // 100 - 1)]


def guard():
    pool_experiment.guard()


@contextlib.contextmanager
def mode(pooled):
    # Never persist the toggle across operations; no production config changed.
    os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "1" if pooled else "0"
    try:
        yield
    finally:
        os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "0"


def run(iterations=30, warmup=5, repetitions=3):
    guard()
    if iterations < 30 or warmup < 5 or repetitions < 2:
        raise ValueError("Require >=30 measurements, 5 warmups and 2 repetitions.")
    models.create_tables()
    label = "fictional-pool-" + uuid4().hex[:10]
    password = "Fictional-Pooling-Password-12345"
    customer = auth_service.register_customer("Fictional Customer", label + "@example.test", password)
    session = "customer-" + str(customer["id"])
    token = auth_service.login_customer(label + "@example.test", password)["session_token"]
    with models.get_connection() as conn:
        pids = []
        for i in range(10):
            pid = conn.execute(
                "INSERT INTO products(name,category,price,available) VALUES(?,?,?,?)",
                ("Fictional pool meal " + label + str(i), "rice", 2000 + i * 100, 1),
            ).lastrowid
            pids.append(pid)
            conn.execute("INSERT INTO inventory(product_id,variant,quantity) VALUES(?,?,?)",
                         (pid, "standard", 50000))
        area = "Fictional Pool Area " + label
        conn.execute("INSERT INTO delivery_zones(area,fee,estimated_min_hours,estimated_max_hours,available) VALUES(?,?,?,?,?)",
                     (area, 1000, 1, 2, 1))
        conn.execute("INSERT INTO conversation_sessions(session_id,customer_id,cart_data) VALUES(?,?,?) ON CONFLICT(session_id) DO NOTHING",
                     (session, customer["id"], '{"items":[]}'))
    details = {
        "customer_name": "Fictional Customer", "phone": "08012345678",
        "delivery_address": "Fictional Benchmark Road", "delivery_area": area,
    }

    def set_cart(n):
        items = [{"product_id": p, "quantity": 1, "variant": "standard", "modifications": []}
                 for p in pids[:n]]
        with mode(False):
            with models.get_connection() as conn:
                conn.execute("UPDATE conversation_sessions SET cart_data=? WHERE session_id=?",
                             (json.dumps({"items": items}), session))
        return items

    def checkout_fixture(i):
        set_cart(5)
        with mode(False):
            prep = CheckoutService(session).prepare_checkout(**details)
        if not prep.get("success"):
            raise AssertionError("Checkout fixture invalid")
        return prep

    def confirmation(prep):
        return CheckoutService(session).confirm_checkout(
            customer_approved=True, reviewed_fingerprint=fingerprint(prep["summary"]),
            checkout_id=prep["checkout_id"],
        ).get("success") is True

    def payment_fixture(i):
        prep = checkout_fixture(i)
        with mode(False):
            confirmed = CheckoutService(session).confirm_checkout(
                customer_approved=True, reviewed_fingerprint=fingerprint(prep["summary"]),
                checkout_id=prep["checkout_id"])
            if not confirmed.get("success"):
                raise AssertionError("Payment fixture failed")
            ref = "nb_pool_mock_" + uuid4().hex
            with models.get_connection() as conn:
                conn.execute("INSERT INTO payments(order_id,amount,payment_reference,status) VALUES(?,?,?,'unpaid')",
                             (confirmed["order_id"], confirmed["total"], ref))
        return ref, confirmed["total"]

    def verify(payload):
        ref, amount = payload
        data = {"reference": ref, "status": "success", "currency": "NGN", "amount": amount * 100}
        with patch.object(paystack_service, "_request", return_value=data):
            result = paystack_service.verify(ref, session)
        return result["payment_status"] == "paid" and result["order_status"] == "confirmed"

    def connect_only(_):
        connection = models.get_connection()
        connection.close()
        return True

    def transaction_commit(_):
        with models.get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM products").fetchone()
        return row["count"] >= 10

    def transaction_rollback(_):
        conn = models.get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE inventory SET quantity = quantity - 1 WHERE product_id=?",
                         (pids[0],))
            conn.rollback()
        finally:
            conn.close()
        return True

    def concurrency(n):
        def work(_):
            return CartService(session).view_cart()["currency"] == "NGN"
        with concurrent.futures.ThreadPoolExecutor(max_workers=n) as executor:
            return all(executor.map(work, range(n)))

    def timed(operation, state):
        begin = time.perf_counter()
        try:
            result = operation(state)
            ok = result is True
        except Exception:
            ok = False
        return (time.perf_counter() - begin) * 1000, ok

    physical = [0]
    original_connect = postgres.psycopg.connect

    def count_connect(*args, **kwargs):
        physical[0] += 1
        return original_connect(*args, **kwargs)

    plan = []
    for n in (1, 5, 10):
        plan.append((f"cart_{n}", lambda _: CartService(session).view_cart()["currency"] == "NGN",
                     lambda i, n=n: set_cart(n)))
        plan.append((f"stock_{n}", lambda items: CartService(session)._check_all_stock(items) is None,
                     lambda i, n=n: set_cart(n)))
    plan += [
        ("auth_login", lambda _: bool(auth_service.login_customer(label + "@example.test", password)["session_token"]), None),
        ("session_verify", lambda _: auth_service.authenticate_token(token)["id"] == customer["id"], None),
        ("connection_cycle", connect_only, None),
        ("transaction_commit", transaction_commit, None),
        ("transaction_rollback", transaction_rollback, None),
        ("checkout_prepare", lambda _: CheckoutService(session).prepare_checkout(**details)["success"],
         lambda i: set_cart(5)),
        ("checkout_confirm", confirmation, checkout_fixture),
        ("payment_verify_mock", verify, payment_fixture),
    ]
    for n in (1, 5, 10):
        plan.append((f"concurrent_cart_{n}", lambda _, n=n: concurrency(n), lambda i: set_cart(5)))

    class QueryCounter(logging.Handler):
        def __init__(self):
            super().__init__()
            self.total = 0
        def emit(self, record):
            if str(record.msg).startswith("db_metric "):
                self.total += 1

    collector = QueryCounter()
    db_logger = logging.getLogger("naija_bites.metrics")
    original_level = db_logger.level
    db_logger.addHandler(collector)
    db_logger.setLevel(logging.INFO)
    pool_experiment.open_pool(min_size=2, max_size=10, timeout=2)
    try:
        with patch.object(postgres.psycopg, "connect", side_effect=count_connect):
            for name, operation, prepare in plan:
                sample = {"fresh": [], "pooled": []}
                total_error = {"fresh": 0, "pooled": 0}
                for repetition in range(repetitions):
                    for iteration in range(warmup + iterations):
                        # Preparation is always fresh, outside measured sections.
                        paired_state = (prepare(iteration) if prepare and name not in
                                        ("checkout_confirm", "payment_verify_mock") else None)
                        order = ("fresh", "pooled") if (repetition + iteration) % 2 == 0 else ("pooled", "fresh")
                        for current in order:
                            # These two workloads mutate state; build a fresh, independently
                            # approvable order/payment for EACH mode, outside timed operation.
                            state = (prepare(iteration) if prepare and name in
                                     ("checkout_confirm", "payment_verify_mock") else paired_state)
                            before_conn = physical[0]
                            before_queries = collector.total
                            before_pool = pool_experiment.snapshot()
                            with mode(current == "pooled"):
                                ms, ok = timed(operation, state)
                            after_pool = pool_experiment.snapshot()
                            if iteration >= warmup:
                                sample[current].append({
                                    "ms": ms,
                                    "queries": collector.total - before_queries,
                                    "new_connections": ((after_pool["new_physical"] - before_pool["new_physical"])
                                                        if current == "pooled" else physical[0] - before_conn),
                                    "pool_wait_ms": ((after_pool["wait_ms"] - before_pool["wait_ms"])
                                                     if current == "pooled" else 0.0),
                                })
                                if not ok:
                                    total_error[current] += 1
                def stat(items, error):
                    values = [x["ms"] for x in items]
                    return {
                        "p50_ms": round(percentile(values, 50), 3),
                        "p95_ms": round(percentile(values, 95), 3),
                        "new_physical_connections": sum(x["new_connections"] for x in items),
                        "db_queries_per_operation": round(statistics.mean(x["queries"] for x in items), 2),
                        "pool_borrow_ms_total": round(sum(x["pool_wait_ms"] for x in items), 3),
                        "throughput_per_s": round(len(values) / (sum(values) / 1000), 2),
                        "errors": error,
                    }
                fresh = stat(sample["fresh"], total_error["fresh"])
                pooled = stat(sample["pooled"], total_error["pooled"])
                print("POOL_AB " + json.dumps({
                    "scenario": name, "iterations_per_repeat": iterations,
                    "repetitions": repetitions, "warmup": warmup,
                    "fresh": fresh, "pooled": pooled,
                    "p50_reduction_percent": round(100 * (fresh["p50_ms"]-pooled["p50_ms"]) / fresh["p50_ms"], 2),
                }, sort_keys=True))
                if fresh["errors"] or pooled["errors"]:
                    raise AssertionError("Pooling A/B correctness failure in " + name)
    finally:
        os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "0"
        pool_experiment.close_pool()
        db_logger.removeHandler(collector)
        db_logger.setLevel(original_level)
    print("POOL_ENV " + json.dumps({
        "python": platform.python_version(), "postgres": 16,
        "pool_min": 2, "pool_max": 10,
        "database": "disposable localhost", "tracing": "off",
        "pool_borrow_ms_definition": "time in getconn including queue wait",
    }, sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    run(args.iterations, args.warmup, args.repetitions)


if __name__ == "__main__":
    main()
