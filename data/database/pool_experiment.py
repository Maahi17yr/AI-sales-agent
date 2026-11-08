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
_starting = False
_leases = {}  # id(connection) -> originating pool (safe across close/open)
_stats = {"new_physical": 0, "borrows": 0, "wait_ms": 0.0, "timeouts": 0,
          "active": 0, "peak_active": 0, "reset_count": 0,
          "reset_ms": 0.0, "reset_errors": 0}


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
    """Keep full session reset; measure its cost, including any rollback."""
    started = time.perf_counter()
    try:
        if conn.info.transaction_status.name != "IDLE":
            conn.rollback()
        # DISCARD ALL requires autocommit and invalidates prepared statements.
        # prepare_threshold=None in open_pool keeps client cache consistent.
        conn.execute("DISCARD ALL")
    except Exception:
        with _lock:
            _stats["reset_errors"] += 1
        raise  # psycopg_pool must evict a connection it cannot safely reset
    finally:
        with _lock:
            _stats["reset_count"] += 1
            _stats["reset_ms"] += (time.perf_counter() - started) * 1000


def open_pool(min_size=1, max_size=5, timeout=0.5):
    guard()
    if type(min_size) is not int or type(max_size) is not int or min_size < 1 or max_size < min_size or max_size > 15:
        raise ValueError("Experiment pool sizes must satisfy 1 <= min <= max <= 15")
    if not 0 < timeout <= 10:
        raise ValueError("Experiment checkout timeout must be in (0,10] seconds.")
    global _pool, _starting
    # Claim exclusive startup while holding the lock, but release it before
    # pool.wait: background connection workers invoke the same stats mutex.
    with _lock:
        if _pool is not None or _starting:
            raise RuntimeError("Experiment pool is already open or starting.")
        if _leases:
            raise RuntimeError("Previous pool has outstanding borrowed connections.")
        _starting = True
        for key in _stats:
            _stats[key] = 0
    def created(_conn):
        with _lock:
            _stats["new_physical"] += 1
    try:
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
            _pool = pool
    finally:
        with _lock:
            _starting = False


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
        _leases[id(conn)] = pool
        _stats["borrows"] += 1
        _stats["wait_ms"] += elapsed_ms
        _stats["active"] += 1
        _stats["peak_active"] = max(_stats["peak_active"], _stats["active"])
    # no hidden SQL or logging. Return requires transaction cleanup.
    return conn


def release(conn):
    # Return to the pool that issued the lease, never a newly reopened pool.
    with _lock:
        origin = _leases.pop(id(conn), None)
        if origin is not None:
            _stats["active"] -= 1
    if origin is None:
        # The caller never borrowed through this experimental pool.
        # Do not grant it to any customer.
        conn.close()
        return
    if origin.closed:
        conn.close()
        return
    origin.putconn(conn)


def reset_peak_active():
    """Start a new observation window only after previous borrow leases finish."""
    with _lock:
        if _stats["active"] != 0:
            raise RuntimeError("Cannot reset peak while pool connections are borrowed")
        _stats["peak_active"] = 0


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
        if _starting:
            raise RuntimeError("Cannot close the experimental pool during startup.")
        pool, _pool = _pool, None
    if pool is not None:
        pool.close()
