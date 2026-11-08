"""Run existing PostgreSQL checkout/payment tests with experimental pooling ON.

Only the exact disposable CI database is permitted. No live provider calls.
"""
import os
import unittest

from data.database import pool_experiment


def main():
    pool_experiment.guard()
    pool_experiment.open_pool(min_size=2, max_size=5, timeout=2)
    os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "1"
    os.environ["NAIJA_BITES_POSTGRES_CI"] = "1"
    try:
        suite = unittest.defaultTestLoader.loadTestsFromName("scripts.test_checkout_postgres")
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful() or result.testsRun < 2:
            raise SystemExit(1)
    finally:
        os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"] = "0"
        os.environ.pop("NAIJA_BITES_POSTGRES_CI", None)
        pool_experiment.close_pool()


if __name__ == "__main__":
    main()
