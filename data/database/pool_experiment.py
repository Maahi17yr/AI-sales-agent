"""Disposable-only PostgreSQL pool experiment. Never enabled on normal deployments.

Requires exact local database URL, experiment marker and raw tracing disabled.
Uses psycopg_pool.ConnectionPool, not custom connection pooling.
"""
import os
import threading
import time

from psycopg_pool import ConnectionPool, PoolTimeout
from psycopg.rows import dict_row

_ALLOWED_URL = "postgresql://postgres:postgres@127.0.0.1:5432/naija_pool_experiment"
_lock = threading.RLock()
_pool = None
_stats = {"new_physical": 0, "borrows": 0, "wait_ms": 0.0, "timeouts": 0}


def guard():
    if (os.getenv("NAIJA_BITES_POOL_EXPERIMENT") != "1" or
            os.getenv("DATABASE_URL") != _ALLOWED_URL or
            os.getenv("OBSERVABILITY_MODE", "off") != "off"):
        raise RuntimeError("Pooling experiment requires the isolated CI database.")


def _configure(conn):
    # Every borrowed connection must start from autocommit mode, as in the
    # current adapter; explicit BEGIN continues to delimit transactions.
    conn.autocommit = True


def _reset(conn):
    # PostgreSQL session-level settings (SET / set_config) survive COMMIT.
    # Reset all session state before another customer borrows this connection.
    # DISCARD ALL requires autocommit and no active transaction. Automatic
    # prepared statements are disabled on pooled clients: DISCARD ALL clears
    # server-side statements, which would otherwise desynchronize client cache.
    if conn.info.transaction_status.name != "IDLE":
        conn.rollback()
    conn.execute("DISCARD ALL")


def open_pool(min_size=1, max_size=5, timeout=0.5):
    guard()
    if type(min_size) is not int or type(max_size) is not int or min_size < 1 or max_size < min_size or max_size > 10:
        raise ValueError("Experiment pool sizes must satisfy 1 <= min <= max <= 10")
    if not 0 < timeout <= 10:
        raise ValueError("Experiment checkout timeout must be in (0,10] seconds.")
    global _pool
    # Never hold the stats mutex while pool.wait() runs: background workers
    # invoke the configure callback and need the same mutex.
    with _lock:
        if _pool is not None:
            raise RuntimeError("Experiment pool is already open.")
        for key in _stats:
            _stats[key] = 0
    def created(_conn):
        with _lock:
            _stats["new_physical"] += 1
    pool = ConnectionPool(
        conninfo=_ALLOWED_URL, min_size=min_size, max_size=max_size,
        timeout=timeout, open=False,
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None},
        configure=created, reset=_reset,
    )
    pool.open()
    try:
        pool.wait(timeout=10.0)
    except Exception:
        pool.close()
        raise
    with _lock:
        if _pool is not None:
            pool.close()
            raise RuntimeError("Experiment pool is already open.")
        _pool = pool


def pool_is_open():
    with _lock:
        return _pool is not None


def borrow():
    guard()
    with _lock:
        pool = _pool
    if pool is None:
        raise RuntimeError("Experiment pool not initialized.")
    started = time.perf_counter()
    try:
        conn = pool.getconn()
    except PoolTimeout:
        with _lock:
            _stats["timeouts"] += 1
        raise
    elapsed_ms = (time.perf_counter() - started) * 1000
    with _lock:
        _stats["borrows"] += 1
        _stats["wait_ms"] += elapsed_ms
    # no hidden SQL or logging. Return requires transaction cleanup.
    return conn


def release(conn):
    with _lock:
        pool = _pool
    if pool is None:
        # Existing borrowers may finish during shutdown. Close rather than
        # accidentally returning the connection into another pool.
        conn.close()
        return
    pool.putconn(conn)


def snapshot():
    with _lock:
        data = dict(_stats)
        pool = _pool
    if pool is not None:
        data["pool"] = {key: val for key, val in pool.get_stats().items()
                        if key in ("pool_size", "pool_available", "requests_waiting", "requests_errors",
                                   "requests_queued", "connections_num", "connections_errors")}
    return data


def close_pool():
    global _pool
    with _lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.close()
