"""Bounded, disposable-only concurrency test of the actual Naija Bites backend.

Exactly 10 -> 25 -> 50 distinct signed-in synthetic customers per mode.
Real menu, cart, checkout preparation and limited confirmation. No AI,
Paystack, customer data or production/staging database access.
The two existing lab backends remain unchanged.
"""
import os
import statistics
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

FRESH = "https://naija-bites-real-fresh-temp-20261108.onrender.com"
POOLED = "https://naija-bites-real-pool-temp-20261108.onrender.com"
TARGETS = {"fresh": FRESH, "pooled": POOLED}
PROJECT = "green-hat-88213342"
RUN_ID = "real-backend-10-25-50-20261108-v1"
STAGES = (10, 25, 50)
ROUNDS = 2
MAX_USERS = 50
MAX_CONFIRMS = 10
TIMEOUT = httpx.Timeout(14.0, connect=7.0, read=12.0, pool=7.0)
ACCESS_KEY = os.getenv("NAIJA_AB_LAB_KEY", "")


def guard():
    if (os.getenv("NAIJA_AB_CONCURRENCY") != "1"
            or os.getenv("NAIJA_AB_TARGETS") != FRESH + "," + POOLED
            or os.getenv("NAIJA_AB_CONCURRENCY_RUN_ID") != RUN_ID
            or len(ACCESS_KEY) < 24
            or os.getenv("DATABASE_URL") or os.getenv("AWS_ACCESS_KEY_ID")
            or os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv("PAYSTACK_SECRET_KEY")):
        raise RuntimeError("Concurrency runner refuses non-isolated configuration.")


guard()
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_lock = threading.Lock()
_report = {
    "state": "pending",
    "scope": "actual isolated Naija Bites FastAPI auth, menu, cart and checkout",
    "stages": list(STAGES),
    "rounds_per_stage": ROUNDS,
    "max_confirmed_orders_per_mode": MAX_CONFIRMS,
    "real_ai_or_payments": False,
    "results": [],
}


def publish(**updates):
    with _lock:
        _report.update(updates)


def stats(values):
    if not values:
        return None
    vals = sorted(values)
    return {
        "count": len(vals),
        "avg_ms": round(statistics.mean(vals), 1),
        "p50_ms": round(vals[(len(vals) - 1) // 2], 1),
        "p95_ms": round(vals[(95 * len(vals) + 99) // 100 - 1], 1),
        "max_ms": round(vals[-1], 1),
    }


def call(client, method, path, label, samples, errors, **kwargs):
    t0 = time.perf_counter()
    try:
        response = client.request(method, path, **kwargs)
        duration = round((time.perf_counter() - t0) * 1000, 1)
        if response.status_code not in (200, 201):
            errors.append({"step": label, "http_status": response.status_code})
            return None
        data = response.json()
        samples[label].append(duration)
        return data
    except Exception as exc:
        errors.append({"step": label, "exception": type(exc).__name__})
        return None


def new_client(mode):
    return httpx.Client(
        base_url=TARGETS[mode],
        headers={"x-naija-ab-lab-key": ACCESS_KEY},
        timeout=TIMEOUT,
        follow_redirects=False,
        limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
    )


def verify_target(mode):
    with new_client(mode) as client:
        r = client.get("/__ab/status")
        if r.status_code != 200:
            raise RuntimeError(f"{mode} isolated target rejected preflight: HTTP {r.status_code}")
        data = r.json()
        if (data.get("project") != PROJECT
                or data.get("mode") != mode
                or data.get("database") != ("naija_real_pool" if mode == "pooled" else "naija_real_fresh")
                or data.get("external_integrations") != "disabled"
                or bool(data.get("pool", {}).get("enabled")) != (mode == "pooled")):
            raise RuntimeError(f"{mode} target is not the expected isolated real backend")
        return data.get("pool", {})


def prepare_user(mode, index):
    client = new_client(mode)
    errors, samples = [], defaultdict(list)
    email = f"nb-ab-load-{RUN_ID}-{mode}-{index:02d}@example.test"
    password = "Only-Fictional-Load-Account-2026"
    try:
        # Deterministic identities prevent duplicate accounts on service restarts.
        registration = client.post("/auth/register", json={
            "name": "Synthetic Load Customer", "email": email, "password": password,
        })
        if registration.status_code not in (201, 400):
            raise RuntimeError(f"register HTTP {registration.status_code}")
        if registration.status_code == 400 and "already registered" not in registration.text.lower():
            raise RuntimeError("register rejected for unexpected reason")
        if call(client, "POST", "/auth/login", "login", samples, errors,
                json={"email": email, "password": password}) is None:
            raise RuntimeError(f"login failed: {errors[-1] if errors else 'unknown'}")
        menu = call(client, "GET", "/menu", "menu", samples, errors)
        if not menu or not isinstance(menu.get("products"), list):
            raise RuntimeError("real menu missing")
        egg = next((p for p in menu["products"] if p.get("name") == "Boiled Egg"), None)
        if not egg:
            raise RuntimeError("expected seeded Boiled Egg not found")
        cart = call(client, "GET", "/cart", "cart", samples, errors)
        if cart is None:
            raise RuntimeError("real cart not accessible")
        if not any(p.get("product_id") == egg["id"] for p in cart.get("items", [])):
            if call(client, "POST", "/__ab/cart/add", "cart_add", samples, errors,
                    json={"product_id": egg["id"], "quantity": 1}) is None:
                raise RuntimeError("real cart service could not add test meal")
        return client, None
    except Exception as exc:
        client.close()
        return None, {"mode": mode, "index": index, "type": type(exc).__name__,
                      "reason": str(exc)[:100]}


def preprovision(mode):
    clients = [None] * MAX_USERS
    errors = []
    # Low, fixed provisioning concurrency prevents auth hashing from swamping Free CPU.
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(prepare_user, mode, i): i for i in range(MAX_USERS)}
        for future in as_completed(futures):
            i = futures[future]
            try:
                client, err = future.result()
            except Exception as exc:
                client, err = None, {"mode": mode, "index": i, "type": type(exc).__name__}
            clients[i] = client
            if err:
                errors.append(err)
    return clients, errors


def one_journey(client, barrier):
    samples = defaultdict(list)
    errors = []
    try:
        barrier.wait(timeout=20)
        menu = call(client, "GET", "/menu", "menu", samples, errors)
        if not menu or not menu.get("products"):
            return samples, errors
        cart = call(client, "GET", "/cart", "cart", samples, errors)
        if cart is None or not cart.get("items"):
            errors.append({"step": "cart", "validation": "missing synthetic item"})
            return samples, errors
        checkout = call(client, "GET", "/checkout", "checkout_read", samples, errors)
        if checkout is None:
            return samples, errors
        prepared = call(client, "POST", "/checkout/prepare", "checkout_prepare",
                        samples, errors, json={
                            "customer_name": "Synthetic Load Customer",
                            "phone": "08012345678",
                            "delivery_address": "Fictional A/B Load Test Street",
                            "delivery_area": "Ikeja",
                        })
        if prepared is None or not prepared.get("checkout_id") or not prepared.get("fingerprint"):
            errors.append({"step": "checkout_prepare", "validation": "missing trusted review"})
            return samples, errors
        check = call(client, "GET", "/checkout", "checkout_recovery", samples, errors)
        if check is None or check.get("checkout_id") != prepared["checkout_id"]:
            errors.append({"step": "checkout_recovery", "validation": "review mismatch"})
    except Exception as exc:
        errors.append({"step": "journey", "exception": type(exc).__name__})
    return samples, errors


def run_stage(mode, clients, count):
    before = verify_target(mode)
    all_samples = defaultdict(list)
    all_errors = []
    started = time.perf_counter()
    for round_no in range(ROUNDS):
        barrier = threading.Barrier(count)
        with ThreadPoolExecutor(max_workers=count) as executor:
            futures = [executor.submit(one_journey, clients[i], barrier) for i in range(count)]
            for future in as_completed(futures):
                try:
                    samples, errors = future.result()
                    for label, values in samples.items():
                        all_samples[label].extend(values)
                    all_errors.extend(errors)
                except Exception as exc:
                    all_errors.append({"step": "worker", "exception": type(exc).__name__})
        total = sum(map(len, all_samples.values())) + len(all_errors)
        if (len(all_errors) >= 3 or (total and len(all_errors) / total > 0.05)
                or any(e.get("http_status", 0) >= 500 for e in all_errors)):
            break
    duration = max(0.001, time.perf_counter() - started)
    after = verify_target(mode)
    successes = sum(map(len, all_samples.values()))
    p95 = stats([v for vals in all_samples.values() for v in vals])
    stop = (len(all_errors) >= 3
            or (successes + len(all_errors) > 0 and
                len(all_errors) / (successes + len(all_errors)) > 0.05)
            or any(e.get("http_status", 0) >= 500 for e in all_errors)
            or (p95 and p95["p95_ms"] > 10000))
    return {
        "mode": mode,
        "concurrent_customers": count,
        "duration_s": round(duration, 2),
        "success_requests": successes,
        "failures": len(all_errors),
        "failure_details": all_errors[:10],
        "throughput_rps": round(successes / duration, 2),
        "overall_latency": p95,
        "by_operation": {name: stats(vals) for name, vals in all_samples.items()},
        "pool_before": before,
        "pool_after": after,
        "safety_stop": bool(stop),
    }


def confirm_one(client, barrier):
    samples, errors = defaultdict(list), []
    try:
        barrier.wait(timeout=20)
        checkout = call(client, "GET", "/checkout", "review_before_confirm", samples, errors)
        if not checkout or not checkout.get("fingerprint") or not checkout.get("checkout_id"):
            errors.append({"step": "review_before_confirm", "validation": "no prepared checkout"})
            return samples, errors, None
        result = call(client, "POST", "/checkout/confirm", "confirm_order", samples, errors,
                      json={"customer_approved": True,
                            "reviewed_fingerprint": checkout["fingerprint"],
                            "checkout_id": checkout["checkout_id"]})
        if not result or not result.get("order_id"):
            errors.append({"step": "confirm_order", "validation": "order not created"})
            return samples, errors, None
        cart = call(client, "GET", "/cart", "cart_after_confirm", samples, errors)
        if cart is None or cart.get("items"):
            errors.append({"step": "cart_after_confirm", "validation": "cart not cleared"})
        return samples, errors, result["order_id"]
    except Exception as exc:
        errors.append({"step": "confirm", "exception": type(exc).__name__})
        return samples, errors, None


def limited_confirm(mode, clients):
    barrier = threading.Barrier(MAX_CONFIRMS)
    all_samples, errors, ids = defaultdict(list), [], []
    with ThreadPoolExecutor(max_workers=MAX_CONFIRMS) as executor:
        futures = [executor.submit(confirm_one, clients[i], barrier)
                   for i in range(MAX_CONFIRMS)]
        for future in as_completed(futures):
            samples, errs, order_id = future.result()
            for label, values in samples.items():
                all_samples[label].extend(values)
            errors.extend(errs)
            if order_id is not None:
                ids.append(str(order_id))
    if len(set(ids)) != len(ids):
        errors.append({"step": "orders", "validation": "duplicate order identifiers"})
    return {"mode": mode, "attempted": MAX_CONFIRMS, "confirmed": len(ids),
            "distinct_order_ids": len(set(ids)), "failures": errors[:20],
            "by_operation": {name: stats(vals) for name, vals in all_samples.items()}}


def run():
    clients = {}
    try:
        publish(state="preflight", started_at=datetime.now(timezone.utc).isoformat())
        for mode in TARGETS:
            verify_target(mode)
        for mode in TARGETS:
            publish(state="provisioning", current_mode=mode)
            clients[mode], failures = preprovision(mode)
            if failures:
                publish(state="stopped_safety", reason="synthetic account provisioning failed",
                        provisioning_failures=failures[:10])
                return
        results = []
        for count in STAGES:
            # Alternate the mode order to reduce systematic time-of-run bias.
            order = ("fresh", "pooled") if count != 25 else ("pooled", "fresh")
            for mode in order:
                publish(state="testing", current_mode=mode, current_concurrency=count,
                        results=results)
                stage = run_stage(mode, clients[mode], count)
                results.append(stage)
                publish(results=results)
                if stage["safety_stop"]:
                    publish(state="stopped_safety", reason="latency/error safety threshold",
                            stopped_at={"mode": mode, "concurrent_customers": count})
                    return
        publish(state="confirming_limited_test_orders")
        confirms = [limited_confirm(mode, clients[mode]) for mode in TARGETS]
        publish(state="finished", results=results, confirmations=confirms,
                finished_at=datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        publish(state="stopped_safety", reason="runner exception",
                error_type=type(exc).__name__, error_message=str(exc)[:160])
    finally:
        for mode_clients in clients.values():
            for client in mode_clients:
                if client is not None:
                    client.close()


@app.on_event("startup")
def start_once():
    threading.Thread(target=run, name="bounded-real-backend-concurrency", daemon=True).start()


@app.get("/")
def report():
    with _lock:
        return JSONResponse(dict(_report))
