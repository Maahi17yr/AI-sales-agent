"""Disposable PostgreSQL 16 baseline; all fixtures are fictional.

Only run in CI's isolated naija_bench database. Refuses other configurations.
No Bedrock, no Paystack requests, and no prompts or customer data in output.
"""
import argparse
import asyncio
import json
import logging
import os
import platform
import re
import statistics
import time
from unittest.mock import patch
from uuid import uuid4

import httpx

from core.api.server import app, AUTH_COOKIE_NAME
from core.services import auth_service, paystack_service
from core.services.cart import CartService
from core.services.checkout import CheckoutService
from core.observability import metrics
from data.database import models, postgres


def percentile(samples, fraction):
    values = sorted(samples)
    return values[max(0, (len(values) * fraction + 99) // 100 - 1)]


class CountQueries(logging.Handler):
    def __init__(self):
        super().__init__()
        self.total = 0

    def emit(self, record):
        if record.msg.startswith("db_metric "):
            self.total += 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=5)
    args = parser.parse_args()
    if args.iterations < 30 or args.warmup < 0:
        raise ValueError("At least 30 measured iterations and nonnegative warmup required.")
    url = os.getenv("DATABASE_URL", "")
    if (os.getenv("NAIJA_BITES_POSTGRES_BENCH") != "1"
        or not re.fullmatch(
            r"postgresql://postgres:postgres@127\\.0\\.0\\.1:5432/naija_bench", url
        )):
        raise RuntimeError("Refusing non-disposable PostgreSQL configuration.")
    if os.getenv("OBSERVABILITY_MODE", "off") != "off":
        raise RuntimeError("Raw observability must remain off.")
    models.create_tables()
    counter = [0]
    original_connect = postgres.psycopg.connect

    def tracked_connect(*a, **kw):
        counter[0] += 1
        return original_connect(*a, **kw)

    logger = logging.getLogger("naija_bites.metrics")
    oldlevel = logger.level
    collector = CountQueries()
    logger.setLevel(logging.INFO)
    logger.addHandler(collector)
    prefix = "benchmark-" + uuid4().hex[:10]
    password = "Fictional-Benchmark-Password-12345"
    email = prefix + "@example.test"
    area = "Fictional Benchmark Area " + prefix
    details = {
        "customer_name": "Fictional Customer",
        "phone": "08012345678",
        "delivery_address": "Fictional Benchmark Road",
        "delivery_area": area,
    }

    with patch.object(postgres.psycopg, "connect", side_effect=tracked_connect):
        try:
            owner = auth_service.register_customer("Fictional Customer", email, password)
            session = "customer-" + str(owner["id"])
            login = auth_service.login_customer(email, password)
            token = login["session_token"]
            with models.get_connection() as conn:
                product_ids = []
                for idx in range(10):
                    pid = conn.execute(
                        "INSERT INTO products(name,category,price,available) VALUES(?,?,?,?)",
                        (f"Fictional item {prefix} {idx}", "rice", 4500, 1),
                    ).lastrowid
                    product_ids.append(pid)
                    conn.execute(
                        "INSERT INTO inventory(product_id,variant,quantity) VALUES(?,?,?)",
                        (pid, "standard", 1500),
                    )
                conn.execute(
                    "INSERT INTO delivery_zones(area,fee,estimated_min_hours,estimated_max_hours,available) "
                    "VALUES(?,?,?,?,?)", (area, 1000, 1, 3, 1),
                )
                conn.execute(
                    "INSERT INTO conversation_sessions(session_id,customer_id,cart_data) "
                    "VALUES(?,?,?) ON CONFLICT(session_id) DO NOTHING",
                    (session, owner["id"], '{"items":[]}'),
                )

            def set_cart(size):
                items = [{
                    "product_id": pid, "quantity": 1, "variant": "standard",
                    "modifications": [],
                } for pid in product_ids[:size]]
                with models.get_connection() as conn:
                    conn.execute("UPDATE conversation_sessions SET cart_data=? WHERE session_id=?",
                                 (json.dumps({"items": items}), session))
                return items

            def measured(name, prepare, operation, requests=0):
                durations, queries, connections, failures = [], [], [], 0
                for i in range(args.warmup + args.iterations):
                    state = prepare(i) if prepare is not None else None
                    token_db = metrics.start_request_db_metrics()
                    before_conn, before_q = counter[0], collector.total
                    started = time.perf_counter()
                    try:
                        ok = operation(state)
                        if ok is False:
                            failures += 1
                    except Exception:
                        # No exception text; it could contain credentials or customer details.
                        failures += 1
                    elapsed = (time.perf_counter() - started) * 1000
                    sample_q = collector.total - before_q
                    sample_conn = counter[0] - before_conn
                    metrics.finish_request_db_metrics(token_db)
                    if i >= args.warmup:
                        durations.append(elapsed)
                        queries.append(sample_q)
                        connections.append(sample_conn)
                summary = {
                    "scenario": name, "samples": args.iterations, "warmup": args.warmup,
                    "p50_ms": round(percentile(durations, 50), 3),
                    "p95_ms": round(percentile(durations, 95), 3),
                    "queries_total": sum(queries),
                    "queries_per_call": round(statistics.mean(queries), 2),
                    "connections_total": sum(connections),
                    "connections_per_call": round(statistics.mean(connections), 2),
                    "requests_total": requests * args.iterations,
                    "errors": failures,
                }
                if failures:
                    raise AssertionError(name + " benchmark had failures")
                print("BENCHMARK_RESULT " + json.dumps(summary, sort_keys=True))
                return summary

            measured("auth_login", None,
                     lambda _: bool(auth_service.login_customer(email, password)["session_token"]))
            measured("auth_session_check", None,
                     lambda _: auth_service.authenticate_token(token)["id"] == owner["id"])
            for size in (1, 5, 10):
                measured(f"cart_view_{size}", lambda i, n=size: set_cart(n),
                         lambda items, n=size: len(CartService(session).view_cart()["items"]) == n)
                measured(f"inventory_validate_{size}", lambda i, n=size: set_cart(n),
                         lambda items: CartService(session)._check_all_stock(items) is None)
            measured("checkout_prepare_5", lambda i: set_cart(5),
                     lambda _: CheckoutService(session).prepare_checkout(**details)["success"])

            def prepared_review(i):
                set_cart(5)
                prepared = CheckoutService(session).prepare_checkout(**details)
                if not prepared["success"]:
                    raise AssertionError("Fixture preparation failed")
                return prepared

            def confirm(review):
                result = CheckoutService(session).confirm_checkout(
                    customer_approved=True,
                    reviewed_fingerprint=review["summary"] and
                      __import__("core.services.order_service", fromlist=["fingerprint"]).fingerprint(review["summary"]),
                    checkout_id=review["checkout_id"],
                )
                return result.get("success") is True

            measured("checkout_confirm_5", prepared_review, confirm)

            def prepare_payment(i):
                review = prepared_review(i)
                result = CheckoutService(session).confirm_checkout(
                    customer_approved=True,
                    reviewed_fingerprint=__import__(
                        "core.services.order_service", fromlist=["fingerprint"]
                    ).fingerprint(review["summary"]),
                    checkout_id=review["checkout_id"],
                )
                if not result.get("success"):
                    raise AssertionError("Fixture checkout failed")
                reference = "nb_test_bench_" + uuid4().hex
                with models.get_connection() as conn:
                    conn.execute(
                        "INSERT INTO payments(order_id,amount,payment_reference,status) "
                        "VALUES(?,?,?,'unpaid')",
                        (result["order_id"], result["total"], reference),
                    )
                return reference, result["total"]

            def verify_payment(payload):
                reference, total = payload
                data = {"reference": reference, "status": "success",
                        "amount": total * 100, "currency": "NGN"}
                with patch.object(paystack_service, "_request", return_value=data):
                    result = paystack_service.verify(reference, session)
                return result["payment_status"] == "paid" and result["order_status"] == "confirmed"

            measured("payment_verify_mocked_5", prepare_payment, verify_payment)

            # Five distinct authenticated customers, each with their own session/cookie.
            burst_tokens = [token]
            for n in range(4):
                other_email = f"{prefix}-concurrent-{n}@example.test"
                other = auth_service.register_customer("Fictional Customer", other_email, password)
                burst_tokens.append(auth_service.login_customer(other_email, password)["session_token"])

            async def request_burst():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 54321)),
                    base_url="http://127.0.0.1:8000",
                ) as client:
                    responses = await asyncio.gather(*[
                        client.get("/checkout", headers={"Cookie": AUTH_COOKIE_NAME + "=" + item})
                        for item in burst_tokens
                    ])
                return all(r.status_code == 200 for r in responses)

            measured("concurrent_checkout_5_customers", None,
                     lambda _: asyncio.run(request_burst()), requests=5)
        finally:
            logger.removeHandler(collector)
            logger.setLevel(oldlevel)
    print("BENCHMARK_ENV " + json.dumps({
        "python": platform.python_version(),
        "database": "isolated GitHub Actions PostgreSQL 16 / naija_bench",
        "calls": "local only; payment response mocked; no Bedrock",
        "iterations": args.iterations, "warmup": args.warmup,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
