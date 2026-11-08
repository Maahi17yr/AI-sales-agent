"""Disposable-only psycopg pool for A/B testing the REAL Naija Bites backend.

A fixed Neon hostname and two isolated databases are allowed. Never import
this module into production service configuration.
"""
import os
import threading
from urllib.parse import urlsplit, parse_qs

from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

HOST = "ep-late-mountain-bbo66p9r.c-15.us-east-1.aws.neon.tech"
DATABASES = {"fresh": "naija_real_fresh", "pooled": "naija_real_pool"}
SERVICE_URLS = {
    "fresh": "https://naija-bites-real-fresh-temp-20261108.onrender.com",
    "pooled": "https://naija-bites-real-pool-temp-20261108.onrender.com",
}
_pool = None
_lock = threading.Lock()


def guard():
    mode = os.getenv("NAIJA_AB_POOL_MODE")
    if (os.getenv("NAIJA_AB_LAB") != "1"
            or os.getenv("NAIJA_BITES_ENV") != "demo"
            or os.getenv("OBSERVABILITY_MODE", "off") != "off"
            or mode not in DATABASES):
        raise RuntimeError("Real-backend A/B is disabled outside the dedicated lab.")
    parsed = urlsplit(os.getenv("DATABASE_URL", ""))
    if (parsed.scheme not in ("postgres", "postgresql")
            or parsed.hostname != HOST
            or parsed.path != "/" + DATABASES[mode]
            or not parsed.username or not parsed.password
            or parse_qs(parsed.query).get("sslmode") != ["require"]):
        raise RuntimeError("Refusing a database outside the exact isolated A/B Neon project.")
    if (os.getenv("NAIJA_BITES_FRONTEND_ORIGIN") != SERVICE_URLS[mode]
            or os.getenv("PAYSTACK_CALLBACK_URL") != SERVICE_URLS[mode] + "/payments/callback"
            or os.getenv("PAYSTACK_SECRET_KEY") != "sk_test_disabled_real_ab_lab"
            or os.getenv("BEDROCK_MODEL_ID") != "disabled-for-real-ab-lab"):
        raise RuntimeError("Real-backend A/B requires fixed fake external-service configuration.")
    if any(os.getenv(key) for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_PROFILE")):
        raise RuntimeError("Real-backend A/B must not have AWS credentials.")
    if len(os.getenv("NAIJA_AB_LAB_KEY", "")) < 24:
        raise RuntimeError("Real-backend A/B requires a strong request access key.")
    return mode


def start():
    global _pool
    if guard() != "pooled":
        return
    with _lock:
        if _pool is not None:
            raise RuntimeError("Pool already started")
        pool = ConnectionPool(
            conninfo=os.environ["DATABASE_URL"],
            min_size=2, max_size=5, timeout=3.0, open=False,
            kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None},
            reset=_reset,
        )
        pool.open()
        try:
            pool.wait(timeout=15)
        except Exception:
            pool.close()
            raise
        _pool = pool


def _reset(conn):
    if not conn.closed:
        if conn.info.transaction_status.name != "IDLE":
            conn.rollback()
        conn.execute("DISCARD ALL")


def borrow(url):
    guard()
    if url != os.environ["DATABASE_URL"] or _pool is None:
        raise RuntimeError("Pool unavailable or database URL mismatch")
    return _pool.getconn()


def release(conn):
    if _pool is None:
        conn.close()
        return
    _pool.putconn(conn)


def snapshot():
    if _pool is None:
        return {"enabled": False}
    stats = _pool.get_stats()
    return {"enabled": True, **{k: stats.get(k, 0) for k in (
        "pool_size", "pool_available", "requests_waiting", "requests_queued",
        "requests_errors", "connections_errors", "connections_num",
    )}}


def stop():
    global _pool
    with _lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.close()
