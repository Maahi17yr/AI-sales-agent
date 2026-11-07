"""Validate the synthetic baseline is network-free, bounded and non-secret."""
import os
import unittest
from unittest.mock import patch

from scripts import benchmark_phase0


class PhaseZeroBenchmarkTests(unittest.TestCase):
    def test_nearest_rank_percentiles(self):
        self.assertEqual(benchmark_phase0.percentile([10, 20, 30, 40, 50], 50), 30)
        self.assertEqual(benchmark_phase0.percentile([10, 20, 30, 40, 50], 95), 50)

    def test_benchmark_runs_with_fake_provider_and_in_memory_sqlite(self):
        with patch.dict(os.environ, {"DATABASE_URL": ""}):
            result = benchmark_phase0.run(iterations=5, warmup=2)
        self.assertEqual(result["samples"], 5)
        self.assertEqual(result["warmup"], 2)
        self.assertEqual(result["errors"], 0)
        self.assertEqual(result["environment"], "synthetic-local-no-network")
        for key in ("sqlite_p50_ms", "sqlite_p95_ms", "mock_paystack_p50_ms", "mock_paystack_p95_ms"):
            self.assertGreaterEqual(result[key], 0)

    def test_refuses_remote_database_configuration(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://invalid.example/fictional"}):
            with self.assertRaisesRegex(ValueError, "refuses any configured"):
                benchmark_phase0.run(iterations=1, warmup=0)


if __name__ == "__main__":
    unittest.main()
