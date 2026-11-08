"""Bounded, manually started Locust comparison against the *fictional* Naija Bites lab.

Target is a hardcoded temporary Render URL. Every request uses that absolute URL;
neither a Locust UI host override nor an environment change can redirect traffic
to production. The scripted assistant and payments are fake. The database is real,
but is the separate disposable Neon PG18 lab project.
"""
import os
import random
import threading

from locust import HttpUser, LoadTestShape, between, events, task
from locust.exception import StopUser

LAB_URL = "https://naija-bites-live-lab-temp-20261108.onrender.com"
if os.getenv("NAIJA_LOAD_TEST_ISOLATED") != "1":
    raise RuntimeError("Load test is disabled outside the isolated lab.")
if os.getenv("LAB_TARGET_URL") != LAB_URL:
    raise RuntimeError("Target must match the exact disposable lab URL.")
LAB_KEY = os.environ["LAB_ACCESS_CODE"]
if len(LAB_KEY) < 12:
    raise RuntimeError("Lab access code is missing or invalid.")

MAX_SIMULTANEOUS_USERS = 50
MAX_TOTAL_JOURNEYS = 600
MAX_ORDERS_PER_USER = 6
# Exactly three finite stages, with a slow ramp. The UI starts the test manually.
STAGES = ((60, 10, 2), (120, 25, 3), (180, 50, 5))
_journeys = 0
_lock = threading.Lock()


@events.test_start.add_listener
def reset_budget(environment, **kwargs):
    global _journeys
    with _lock:
        _journeys = 0
    print("NAIJA_LAB_LOAD_TEST_STARTED target=isolated_lab max_users=50 max_journeys=600", flush=True)


@events.test_stop.add_listener
def record_finish(environment, **kwargs):
    print("NAIJA_LAB_LOAD_TEST_STOPPED journeys_started=" + str(_journeys), flush=True)


def claim_journey():
    global _journeys
    with _lock:
        if _journeys >= MAX_TOTAL_JOURNEYS:
            return False
        _journeys += 1
        return True


class FictionalCustomer(HttpUser):
    abstract = True
    host = LAB_URL
    wait_time = between(3, 5)
    mode = None

    def _call(self, method, path, step, body=None, required=()):
        # Always use an absolute, immutable URL: no UI-supplied host can redirect.
        headers = {"X-Lab-Key": LAB_KEY, "Origin": LAB_URL}
        options = {"name": self.mode + " | " + step, "headers": headers,
                   "catch_response": True, "allow_redirects": False, "timeout": 18}
        if body is not None:
            options["json"] = body
        with self.client.request(method, LAB_URL + path, **options) as response:
            if response.status_code != 200:
                response.failure("Unexpected HTTP " + str(response.status_code))
                return None
            try:
                data = response.json()
            except (ValueError, TypeError):
                response.failure("Expected JSON response")
                return None
            if not isinstance(data, dict) or any(field not in data for field in required):
                response.failure("Missing expected response fields")
                return None
            response.success()
            return data

    def on_start(self):
        self.orders_done = 0
        if self._call("GET", "/api/state", "start session", required=("products",)) is None:
            raise StopUser()
        if self._call("POST", "/api/mode", "set mode",
                      body={"mode": self.mode}, required=("mode",)) is None:
            raise StopUser()

    @task
    def ordering_journey(self):
        if self.orders_done >= MAX_ORDERS_PER_USER or not claim_journey():
            raise StopUser()
        state = self._call("GET", "/api/state", "view menu", required=("products",))
        if state is None:
            raise StopUser()
        available = [p for p in state["products"]
                     if isinstance(p, dict) and p.get("stock", 0) > 0]
        if not available:
            # Never create more inventory or modify the service under test.
            raise StopUser()
        product = random.choice(available)
        if self._call("POST", "/api/chat", "scripted chat",
                      body={"message": "show me the menu"}, required=("reply",)) is None:
            raise StopUser()
        if self._call("POST", "/api/cart/add", "add meal",
                      body={"product_id": product["id"], "quantity": 1},
                      required=("message",)) is None:
            raise StopUser()
        order = self._call("POST", "/api/checkout", "checkout",
                           body={}, required=("order_id", "total", "status"))
        if order is None:
            raise StopUser()
        payment = self._call("POST", "/api/payment", "mock payment",
                             body={"order_id": order["order_id"]}, required=("message",))
        if payment is None:
            raise StopUser()
        if self._call("GET", "/api/state", "verify order", required=("orders",)) is None:
            raise StopUser()
        self.orders_done += 1


class FreshConnectionCustomer(FictionalCustomer):
    mode = "fresh"
    weight = 1


class PooledConnectionCustomer(FictionalCustomer):
    mode = "pool"
    weight = 1


class CautiousRamp(LoadTestShape):
    """10 -> 25 -> 50 total virtual customers; stop after 3 min or on errors.

    The shape, not an editable UI field, owns the user count and spawn rate.
    No external host, uncapped concurrency, or indefinite test runs.
    """
    def tick(self):
        if _journeys >= MAX_TOTAL_JOURNEYS:
            print("NAIJA_LAB_STOP reason=journey_budget", flush=True)
            return None
        if self.runner is not None:
            stats = self.runner.stats.total
            if stats.num_failures >= 3:
                print("NAIJA_LAB_STOP reason=three_failures", flush=True)
                return None
            if stats.num_requests >= 40 and stats.get_response_time_percentile(0.95) > 6000:
                print("NAIJA_LAB_STOP reason=high_latency", flush=True)
                return None
        elapsed = self.get_run_time()
        for until, users, spawn_rate in STAGES:
            if elapsed < until:
                return min(users, MAX_SIMULTANEOUS_USERS), spawn_rate
        return None
