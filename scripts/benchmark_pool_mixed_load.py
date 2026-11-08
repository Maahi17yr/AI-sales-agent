"""Open-loop fixed-arrival mixed workload, disposable PostgreSQL and fake providers.

Measures *true* wall-clock completed throughput, scheduled-arrival response
latency and per-call service latency. Every state-changing operation gets its
own prepared fictional session/order; assertions check inventory/payment state.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import random
import time
from unittest.mock import patch
from uuid import uuid4

from core.services import auth_service, paystack_service
from core.services.cart import CartService
from core.services.checkout import CheckoutService
from core.services.order_service import fingerprint, OrderService
from data.database import models, pool_experiment, repository

from scripts.benchmark_pool_sizing import percentile


def run_arrivals(workload, workers, arrival_rps):
    start = time.perf_counter()
    def execute(entry, scheduled):
        started = time.perf_counter()
        try:
            good = entry["call"]() is True
        except Exception:
            good = False
        end = time.perf_counter()
        return {"kind": entry["kind"], "ok": good,
                "response_ms": (end - scheduled) * 1000,
                "service_ms": (end - started) * 1000}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        jobs = []
        for index, entry in enumerate(workload):
            scheduled = start + index / arrival_rps
            delay = scheduled - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            jobs.append(executor.submit(execute, entry, scheduled))
        responses = [job.result() for job in jobs]
    elapsed = time.perf_counter() - start
    durations = [r["response_ms"] for r in responses]
    services = [r["service_ms"] for r in responses]
    successes = sum(r["ok"] for r in responses)
    by_kind = {}
    for kind in sorted(set(r["kind"] for r in responses)):
        subset = [r for r in responses if r["kind"] == kind]
        by_kind[kind] = {"successes": sum(r["ok"] for r in subset),
                         "attempted": len(subset),
                         "p95_response_ms": percentile([r["response_ms"] for r in subset], 95)}
    return {
        "requests": len(responses), "successes": successes, "errors": len(responses) - successes,
        "actual_completed_rps": round(successes / elapsed, 2),
        "elapsed_s": round(elapsed, 3),
        "p50_response_ms": percentile(durations, 50),
        "p95_response_ms": percentile(durations, 95),
        "p99_response_ms": percentile(durations, 99),
        "p50_service_ms": percentile(services, 50),
        "p95_service_ms": percentile(services, 95),
        "types": by_kind,
    }


def main():
    arg = argparse.ArgumentParser()
    arg.add_argument("--arrival-rps", type=float, default=75.0)
    arg.add_argument("--per-kind", type=int, default=12)
    arg.add_argument("--cases", default="", help="Optional comma-separated case names; default all")
    args = arg.parse_args()
    pool_experiment.guard()
    if not 20 <= args.arrival_rps <= 200 or not 10 <= args.per_kind <= 40:
        raise ValueError("Load test requires 20..200 arrivals/s and 10..40 per workload.")
    os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "0"
    models.create_tables()

    prefix = "fictional-mix-" + uuid4().hex[:12]
    password = "Fictional-Mix-Passphrase-12345"
    accounts = []
    for n in range(5):
        email = f"{prefix}-{n}@example.test"
        created = auth_service.register_customer("Fictional Mix Customer", email, password)
        token = auth_service.login_customer(email, password)["session_token"]
        accounts.append((created["id"], email, token))

    with models.get_connection() as conn:
        pid = conn.execute(
            "INSERT INTO products(name,category,price,available) VALUES (?,?,?,?)",
            ("Fictional Mix Meal " + prefix, "rice", 2400, 1),
        ).lastrowid
        conn.execute("INSERT INTO inventory(product_id,variant,quantity) VALUES(?,?,?)",
                     (pid, "standard", 10000))
        area = "Fictional Mix Area " + prefix
        conn.execute(
            "INSERT INTO delivery_zones(area,fee,estimated_min_hours,estimated_max_hours,available) "
            "VALUES(?,?,?,?,?)", (area, 500, 1, 2, 1))
    details = {"customer_name": "Fictional Test",
               "phone": "08012345678",
               "delivery_address": "Fictional Delivery Street",
               "delivery_area": area}

    def prepare_workload(suffix):
        # Every checkout state-changing call belongs to a distinct session
        # and is provisioned outside the timed load window.
        entries = []
        payment_payloads = {}
        confirmed_ids = []
        paid_ids = []
        expected_stock_deduction = 0

        def new_session(i):
            account_id = accounts[i % len(accounts)][0]
            session = f"pool-load-{suffix}-{uuid4().hex}"
            cart = {"items": [{"product_id": pid, "quantity": 1,
                               "variant": "standard", "modifications": []}]}
            with models.get_connection() as conn:
                conn.execute("INSERT INTO conversation_sessions(session_id,customer_id,cart_data) "
                             "VALUES(?,?,?)", (session, account_id, json.dumps(cart)))
            return session

        n = args.per_kind
        for index in range(n):
            cid, email, token = accounts[index % len(accounts)]
            # Chat-adjacent persistence read, not an LLM call.
            session = new_session(index)
            entries.append({"kind": "chat_db",
                            "call": lambda s=session: repository.get_session(s) is not None})
            entries.append({"kind": "session",
                            "call": lambda c=cid, t=token:
                                    (auth_service.authenticate_token(t) or {}).get("id") == c})
            entries.append({"kind": "login",
                            "call": lambda e=email:
                                    bool(auth_service.login_customer(e, password).get("session_token"))})
            entries.append({"kind": "cart",
                            "call": lambda s=session:
                                    len(CartService(s).view_cart()["items"]) == 1})

            prep_session = new_session(index)
            entries.append({"kind": "checkout_prepare",
                            "call": lambda s=prep_session:
                                    CheckoutService(s).prepare_checkout(**details).get("success") is True})
            confirm_session = new_session(index)
            review = CheckoutService(confirm_session).prepare_checkout(**details)
            if not review.get("success"):
                raise AssertionError("Fictional checkout confirmation fixture failed")
            entries.append({
                "kind": "checkout_confirm",
                "call": lambda s=confirm_session, p=review:
                        CheckoutService(s).confirm_checkout(
                            customer_approved=True,
                            reviewed_fingerprint=fingerprint(p["summary"]),
                            checkout_id=p["checkout_id"]).get("success") is True,
            })
            confirmed_ids.append((confirm_session, cid))

            pay_session = new_session(index)
            pay_review = CheckoutService(pay_session).prepare_checkout(**details)
            if not pay_review.get("success"):
                raise AssertionError("Fictional payment fixture review failed")
            paid_order = CheckoutService(pay_session).confirm_checkout(
                customer_approved=True,
                reviewed_fingerprint=fingerprint(pay_review["summary"]),
                checkout_id=pay_review["checkout_id"])
            if not paid_order.get("success"):
                raise AssertionError("Fictional payment fixture confirmation failed")
            ref = "nb_test_pool_load_" + uuid4().hex
            with models.get_connection() as conn:
                conn.execute("INSERT INTO payments(order_id,amount,payment_reference,status) "
                             "VALUES(?,?,?,'unpaid')",
                             (paid_order["order_id"], paid_order["total"], ref))
            payment_payloads[ref] = {
                "reference": ref, "status": "success",
                "currency": "NGN", "amount": paid_order["total"] * 100,
            }
            entries.append({"kind": "mock_payment",
                            "call": lambda s=pay_session, r=ref:
                                    paystack_service.verify(r, s)["payment_status"] == "paid"})
            paid_ids.append((paid_order["order_id"], cid))
            expected_stock_deduction += 1

        random.Random(20261108).shuffle(entries)
        return entries, payment_payloads, confirmed_ids, paid_ids, expected_stock_deduction

    def verify_state(previous_stock, paid_ids, confirmed_sessions, count):
        # All of these reads run OUTSIDE the timed load. Verify customer ownership,
        # session isolation, exact order prices, payment status, and inventory.
        expected_discount = OrderService._calculate_discount(2400)
        expected_total = 2400 + 500 - expected_discount
        with models.get_connection() as conn:
            now = conn.execute(
                "SELECT quantity FROM inventory WHERE product_id=?", (pid,)
            ).fetchone()["quantity"]
            if now != previous_stock - count:
                order_count = 0
                item_count = 0
                for session_id, _customer_id in confirmed_sessions:
                    saved = conn.execute("SELECT cart_data FROM conversation_sessions WHERE session_id=?", (session_id,)).fetchone()
                    if saved:
                        last = json.loads(saved["cart_data"]).get("last_checkout", {})
                        if last.get("order_id"):
                            order_count += 1
                            item = conn.execute("SELECT quantity FROM order_items WHERE order_id=? AND product_id=?", (last["order_id"], pid)).fetchone()
                            item_count += item["quantity"] if item else 0
                raise AssertionError(f"Stock deduction invariant failed: before={previous_stock} after={now} expected={previous_stock-count} confirmed_orders={order_count} confirmed_items={item_count}")
            for session, expected_customer in confirmed_sessions:
                saved = conn.execute(
                    "SELECT customer_id, cart_data FROM conversation_sessions WHERE session_id=?",
                    (session,),
                ).fetchone()
                if not saved or saved["customer_id"] != expected_customer:
                    raise AssertionError("Checkout session/customer isolation failed")
                last = json.loads(saved["cart_data"]).get("last_checkout", {})
                order_id = last.get("order_id")
                if order_id is None:
                    raise AssertionError("Confirmed checkout order missing")
                order = conn.execute(
                    "SELECT customer_id,subtotal,discount,delivery_fee,total,status "
                    "FROM orders WHERE id=?", (order_id,)
                ).fetchone()
                if (not order or order["customer_id"] != expected_customer or
                        order["subtotal"] != 2400 or
                        order["discount"] != expected_discount or
                        order["delivery_fee"] != 500 or order["total"] != expected_total or
                        order["status"] != "pending_payment"):
                    raise AssertionError("Checkout ownership/pricing invariant failed")
                item = conn.execute(
                    "SELECT quantity,unit_price FROM order_items WHERE order_id=?",
                    (order_id,),
                ).fetchone()
                if not item or item["quantity"] != 1 or item["unit_price"] != 2400:
                    raise AssertionError("Order line item mismatch")
            for order_id, expected_customer in paid_ids:
                row = conn.execute(
                    "SELECT p.status AS payment, o.status AS order_status, "
                    "o.customer_id,o.total,o.discount,o.subtotal "
                    "FROM orders o JOIN payments p ON p.order_id=o.id WHERE o.id=?",
                    (order_id,),
                ).fetchone()
                if (not row or row["customer_id"] != expected_customer or
                        row["payment"] != "paid" or row["order_status"] != "confirmed" or
                        row["subtotal"] != 2400 or row["discount"] != expected_discount or
                        row["total"] != expected_total):
                    raise AssertionError("Payment/order/account invariant failed")
        for cid, _email, token in accounts:
            who = auth_service.authenticate_token(token)
            if not who or who["id"] != cid or who["role"] != "customer":
                raise AssertionError("Customer token isolation/role invariant failed")


    cases = (("fresh", None, None, 10),
             ("pool_2_5", 2, 5, 10),
             ("pool_5_15", 5, 15, 10),
             ("fresh_25", None, None, 25),
             ("pool_2_5_25", 2, 5, 25),
             ("fresh_50", None, None, 50),
             ("pool_2_5_50", 2, 5, 50),
             ("pool_5_15_50", 5, 15, 50))
    if args.cases:
        selected = {item.strip() for item in args.cases.split(",")}
        known = {item[0] for item in cases}
        if not selected or not selected.issubset(known) or "" in selected:
            raise ValueError("Unknown workload case selection")
        cases = tuple(item for item in cases if item[0] in selected)

    for name, minimum, maximum, workers in cases:
        if minimum is not None:
            pool_experiment.open_pool(minimum, maximum, timeout=0.8)
            os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "1"
        try:
            # Fixtures are created in fresh mode to keep setup outside measured
            # operation; reconnect to pool afterwards for the actual load.
            os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "0"
            entries, fake, confirmed, paid, preconfirmed = prepare_workload(name)
            with models.get_connection() as conn:
                stock_before = conn.execute(
                    "SELECT quantity FROM inventory WHERE product_id=?", (pid,)
                ).fetchone()["quantity"]
            os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = ("1" if minimum is not None else "0")
            # warmup outside measured section
            for _ in range(5):
                with models.get_connection() as conn:
                    conn.execute("SELECT 1")
            before = pool_experiment.snapshot()
            def fake_request(method, path):
                return fake[path.rsplit("/", 1)[-1]]
            with patch.object(paystack_service, "_request", side_effect=fake_request):
                result = run_arrivals(entries, workers, args.arrival_rps)
            after = pool_experiment.snapshot()
            os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "0"
            # Count successful order confirmations; any failure blocks load pass.
            verify_state(stock_before, paid, confirmed, len(confirmed))
            result.update({
                "case": name, "workers": workers,
                "arrival_rps": args.arrival_rps,
                "pool_min": minimum, "pool_max": maximum,
                "borrow_ms_total": round(after["wait_ms"] - before["wait_ms"], 3),
                "pool_timeout_count": after["timeouts"] - before["timeouts"],
                "physical_connections_new": after["new_physical"] - before["new_physical"],
                "pool_reset_ms": round(after["reset_ms"] - before["reset_ms"], 3),
                "pool_peak_active": after["peak_active"] if minimum is not None else None,
                "payment_records_verified": len(paid),
                "checkout_confirmation_records": len(confirmed),
                "stock_deductions_after_fixture": len(confirmed),
            })
            print("MIX_LOAD " + json.dumps(result, sort_keys=True), flush=True)
            if result["errors"]:
                raise AssertionError("Realistic mixed workload returned failures")
        finally:
            os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "0"
            if pool_experiment.pool_is_open():
                pool_experiment.close_pool()

    print("MIX_ENV " + json.dumps({
        "database": "disposable PG16", "fixture": "fictional",
        "providers": "Paystack mocked / no Bedrock", "raw_tracing": "off",
        "throughput": "successful completions divided by real elapsed wall time",
        "latency": "actual completion minus scheduled arrival; includes queueing",
        "fixture_setup": "outside timed operation",
    }, sort_keys=True))


if __name__ == "__main__":
    main()
