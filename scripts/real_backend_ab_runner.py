"""Bounded HTTP benchmark of actual isolated Naija Bites backend workflows.

Run automatically once on a dedicated disposable Render runner. No real AI,
Paystack, production, or external customer data. Publishes aggregate results only.
"""
import os
import uuid
import statistics
import threading
import time
from collections import defaultdict

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

FRESH = "https://naija-bites-real-fresh-temp-20261108.onrender.com"
POOLED = "https://naija-bites-real-pool-temp-20261108.onrender.com"
HOSTS = {"fresh": FRESH, "pooled": POOLED}
EXPECTED_KEY = "NaijaRealAB-8Nov26-Km9Qp7zT4rL2x"
if (os.getenv("NAIJA_AB_RUNNER") != "1"
        or os.getenv("NAIJA_AB_LAB_KEY") != EXPECTED_KEY
        or os.getenv("NAIJA_AB_TARGETS") != FRESH + "," + POOLED):
    raise RuntimeError("Refusing to run outside the two exact isolated real-backend hosts.")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_lock = threading.Lock()
_result = {"state": "pending", "scope": "actual Naija Bites FastAPI services; no AI or Paystack"}
_MAX_REPEATS = 8
_TIMEOUT = httpx.Timeout(20.0, connect=12.0)


def _request(client, method, path, step, times, failures, **kwargs):
    started = time.perf_counter()
    try:
        response = client.request(method, path, **kwargs)
        elapsed = (time.perf_counter() - started) * 1000
        if response.status_code not in (200, 201):
            failures.append({"step": step, "status": response.status_code, "message": response.text[:140]})
            return None
        times[step].append(round(elapsed, 1))
        return response.json()
    except Exception as exc:
        failures.append({"step": step, "error": type(exc).__name__})
        return None


def _one(mode, times, failures, index):
    base = HOSTS[mode]
    headers = {"x-naija-ab-lab-key": EXPECTED_KEY}
    with httpx.Client(base_url=base, headers=headers, timeout=_TIMEOUT, follow_redirects=False) as client:
        status = _request(client, "GET", "/__ab/status", "status", times, failures)
        if not status or status.get("mode") != mode or status.get("project") != "green-hat-88213342":
            failures.append({"step": "guard", "error": "backend mismatch"})
            return
        email = f"real-ab-{mode}-{index}-{uuid.uuid4().hex[:12]}@example.test"
        password = "Fictional-AB-Password-12345"
        registered = _request(client, "POST", "/auth/register", "register", times, failures,
                              json={"name": "Real AB Test", "email": email, "password": password})
        if not registered:
            return
        login = _request(client, "POST", "/auth/login", "login", times, failures,
                         json={"email": email, "password": password})
        if not login:
            return
        menu = _request(client, "GET", "/menu", "menu", times, failures)
        if not menu or not menu.get("products"):
            failures.append({"step": "menu", "error": "missing products"})
            return
        product = next((p for p in menu["products"] if p.get("name") == "Party Jollof Rice"), menu["products"][0])
        _request(client, "GET", "/cart", "cart_read", times, failures)
        added = _request(client, "POST", "/__ab/cart/add", "cart_add", times, failures,
                         json={"product_id": product["id"], "quantity": 1})
        if not added:
            return
        _request(client, "GET", "/cart", "cart_after_add", times, failures)
        checkout = _request(client, "GET", "/checkout", "checkout_read", times, failures)
        if checkout is None:
            return
        prepared = _request(client, "POST", "/checkout/prepare", "checkout_prepare", times, failures,
                            json={"customer_name": "Real AB Test", "phone": "08012345678",
                                  "delivery_address": "Fictional AB Street",
                                  "delivery_area": "Ikeja"})
        if not prepared or not prepared.get("checkout_id"):
            return
        approval = {"customer_approved": True,
                    "reviewed_fingerprint": prepared.get("fingerprint"),
                    "checkout_id": prepared["checkout_id"]}
        if not approval["reviewed_fingerprint"]:
            failures.append({"step": "checkout_prepare", "error": "missing fingerprint"})
            return
        confirmed = _request(client, "POST", "/checkout/confirm", "checkout_confirm", times, failures,
                             json=approval)
        if not confirmed or not confirmed.get("order_id"):
            return
        _request(client, "GET", "/checkout", "pending_payment_recovery", times, failures)


def _summarize(samples):
    if not samples:
        return None
    vals = sorted(samples)
    return {"count": len(vals), "avg_ms": round(statistics.mean(vals), 1),
            "p50_ms": vals[(len(vals)-1)//2],
            "p95_ms": vals[max(0, (95*len(vals)+99)//100-1)]}


def run():
    global _result
    with _lock:
        _result = {"state": "running"}
    stats = {}
    # Interleave modes to reduce warmup/time-of-day bias.
    times = {mode: defaultdict(list) for mode in HOSTS}
    failures = {mode: [] for mode in HOSTS}
    for index in range(_MAX_REPEATS):
        for mode in ("fresh", "pooled") if index % 2 == 0 else ("pooled", "fresh"):
            _one(mode, times[mode], failures[mode], index)
            time.sleep(1)
    for mode in HOSTS:
        stats[mode] = {
            "requests_success": sum(map(len, times[mode].values())),
            "failures": failures[mode],
            "latency": {name: _summarize(samples) for name, samples in times[mode].items()},
        }
    with _lock:
        _result = {"state": "finished", "repeat_count_per_mode": _MAX_REPEATS,
                   "scope": "real FastAPI auth/menu/cart/checkout/pending payment, isolated DBs",
                   "external_calls": "none", "modes": stats}


@app.on_event("startup")
def start_once():
    threading.Thread(target=run, daemon=True).start()


@app.get("/")
def report():
    with _lock:
        return JSONResponse(_result)
