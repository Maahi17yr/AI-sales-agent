"""Bounded PG18 transaction/session integrity probes on the *dedicated* Neon test project.

Tests both Neon direct and PgBouncer pooled endpoints, no customer schema,
no providers, and only uniquely named fictional test rows. A failure is a
validation result, not permission to change any production/staging service.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg

from data.database import models, pool_experiment

def _expect(condition, reason):
    if not condition:
        raise AssertionError(reason)

def _check_mode(mode, url):
    os.environ["DATABASE_URL"] = url
    pool_experiment.guard()  # Exact isolated project/DB and explicit flags.
    outcome = {"endpoint": mode, "tests": {}, "errors": []}
    key = uuid4().hex
    try:
        # Dedicated fictional table, not application schema. No real data.
        with models.get_connection() as c:
            c.execute("CREATE TABLE IF NOT EXISTS pool_hosted_integrity "
                      "(id TEXT PRIMARY KEY, val INT NOT NULL)")
            c.execute("INSERT INTO pool_hosted_integrity (id,val) VALUES (?,0)", (key,))
            c.execute("CREATE TABLE IF NOT EXISTS pool_hosted_deferred "
                      "(k TEXT, v TEXT, CONSTRAINT pool_hosted_deferred_unique "
                      "UNIQUE(v) DEFERRABLE INITIALLY DEFERRED)")

        pool_experiment.open_pool(1, 5, timeout=2.0)
        os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "1"

        def increment(_):
            with models.get_connection() as c:
                c.execute("UPDATE pool_hosted_integrity SET val=val+1 WHERE id=?", (key,))

        with ThreadPoolExecutor(max_workers=5) as executor:
            list(executor.map(increment, range(30)))
        with models.get_connection() as c:
            count = c.execute("SELECT val FROM pool_hosted_integrity WHERE id=?", (key,)).fetchone()["val"]
        _expect(count == 30, "Concurrent atomic increments lost or duplicated")
        outcome["tests"]["concurrent_commit"] = "passed"

        try:
            with models.get_connection() as c:
                c.execute("UPDATE pool_hosted_integrity SET val=val+10 WHERE id=?", (key,))
                raise ValueError("intentional rollback")
        except ValueError:
            pass
        with models.get_connection() as c:
            count = c.execute("SELECT val FROM pool_hosted_integrity WHERE id=?", (key,)).fetchone()["val"]
        _expect(count == 30, "Rollback failed to undo a write")
        outcome["tests"]["rollback"] = "passed"

        try:
            with models.get_connection() as c:
                c.execute("INSERT INTO pool_hosted_deferred (k,v) VALUES (?,?)", (key+"a",key))
                c.execute("INSERT INTO pool_hosted_deferred (k,v) VALUES (?,?)", (key+"b",key))
        except psycopg.errors.UniqueViolation:
            pass
        else:
            raise AssertionError("Deferred commit failure did not raise")
        with models.get_connection() as c:
            count = c.execute("SELECT count(*) AS n FROM pool_hosted_deferred WHERE v=?", (key,)).fetchone()["n"]
        _expect(count == 0, "Failed commit left partial rows")
        outcome["tests"]["failed_commit"] = "passed"

        try:
            with models.get_connection() as c:
                c.execute("SET LOCAL statement_timeout='20ms'")
                c.execute("SELECT pg_sleep(0.15)")
        except psycopg.errors.QueryCanceled:
            pass
        else:
            raise AssertionError("Statement timeout did not cancel query")
        with models.get_connection() as c:
            _expect(c.execute("SELECT 1 AS n").fetchone()["n"] == 1,
                    "Connection failed to recover after cancellation")
        outcome["tests"]["cancel_recovery"] = "passed"

        # Close and reopen a single-connection pool so a subsequent lease
        # necessarily reuses the same CLIENT connection. This is a meaningful
        # check for direct PG; transaction PgBouncer can rotate SERVER sessions.
        os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "0"
        pool_experiment.close_pool()
        pool_experiment.open_pool(1, 1, timeout=2.0)
        os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "1"
        marker = "fictional_" + key[:12]
        with models.get_connection() as c:
            c.execute("SELECT set_config('application_name', ?, false)", (marker,))
            c.execute("CREATE TEMP TABLE pool_hosted_temp(v TEXT)")
            c.execute("INSERT INTO pool_hosted_temp VALUES (?)", (marker,))
        with models.get_connection() as c:
            name = c.execute("SELECT current_setting('application_name') AS v").fetchone()["v"]
            table = c.execute("SELECT to_regclass('pg_temp.pool_hosted_temp') AS v").fetchone()["v"]
        _expect(name != marker and table is None,
                "Connection reuse retained session identity or temporary table")
        outcome["tests"]["session_reset"] = "passed"
    except Exception as exc:
        # Only error class and a fixed category; never log SQL or DB credentials.
        outcome["errors"].append(type(exc).__name__)
    finally:
        os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "0"
        if pool_experiment.pool_is_open():
            pool_experiment.close_pool()
        # Remove ONLY our own fictional rows; retain test table for review.
        try:
            with models.get_connection() as c:
                c.execute("DELETE FROM pool_hosted_integrity WHERE id=?", (key,))
                c.execute("DELETE FROM pool_hosted_deferred WHERE v=?", (key,))
        except Exception as exc:
            outcome["errors"].append("cleanup_" + type(exc).__name__)
    outcome["status"] = "passed" if not outcome["errors"] else "failed"
    return outcome

def run_integrity():
    original = os.environ["DATABASE_URL"]
    if "ep-tiny-violet-bbc1phtz-pooler.c-15.us-east-1.aws.neon.tech" not in original:
        raise RuntimeError("Expected dedicated pooled endpoint")
    direct = original.replace(
        "ep-tiny-violet-bbc1phtz-pooler.c-15.us-east-1.aws.neon.tech",
        "ep-tiny-violet-bbc1phtz.c-15.us-east-1.aws.neon.tech")
    try:
        return [_check_mode("direct", direct), _check_mode("pgbouncer", original)]
    finally:
        os.environ["DATABASE_URL"] = original
        os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "0"
