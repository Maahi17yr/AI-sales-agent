# Phase 2 — isolated PostgreSQL connection-pooling experiment

**Status:** Experimental only. **Do not merge, enable by default, or deploy.** Stacked draft PR #46 on PR #45. PRs #42–#45 remain unchanged.

## Design and scope

The existing `data/database/postgres.py` adapter opens a new synchronous `psycopg.connect(..., autocommit=True)` for each `get_connection()`. Context-manager entry opens `BEGIN`; success commits; exceptions roll back; explicit `close()` cleans up. Checkout locks the conversation-session row for confirmation and rechecks stock in its own transaction. Payment verification remains authoritative and webhook replay is idempotent.

The experiment adds `data/database/pool_experiment.py` using **psycopg_pool**, with a bounded pool and explicit cleanup. It is **disabled unless BOTH** `NAIJA_BITES_POOL_EXPERIMENT=1` and `NAIJA_BITES_EXPERIMENT_POOL_ENABLED=1`, with the **exact** localhost database `postgresql://postgres:postgres@127.0.0.1:5432/naija_pool_experiment` and `OBSERVABILITY_MODE=off`. Its dependency lives only in `requirements-pool-experiment.txt`; normal requirements and runtime settings remain unchanged. The pool's `min_size`, `max_size`, and timeout are checked before opening; CI A/B uses 2–10 connections and 2s acquisition timeout. Each return uses `DISCARD ALL` to reset session state. Automatic Psycopg prepared statements are disabled for these pooled connections to avoid stale client-side caches after `DISCARD ALL`. Dead connections are returned for eviction/replacement, not borrowed again.

**Known discovery/fixes during experiment:** Initial initialization deadlocked because a mutex was held during worker initialization; fixed. Reset initially invalidated cached Psycopg prepared statements; fixed by disabling automatic preparation for pooled connections. A fictional payment reference initially failed the application's `nb_test_` validation; corrected. Broken-connection cleanup initially attempted rollback on a closed socket; fixed and regression-tested. Failed intermediate CI runs must **not** be treated as evidence of pooling readiness.

## Reproducible methodology

- GitHub Actions, Python 3.12.16, disposable PostgreSQL 16 (localhost `naija_pool_experiment`), synthetic customer identities and inventory, mocked Paystack verification, no Bedrock.
- Runs alternate **fresh** and **pooled** operations on the same runner with 3 repetitions; each has **5 warmups plus 30 measured iterations** per implementation/scenario (90 samples per implementation). Fixture setup is outside measured sections. For checkout confirmation and payment verification, a separate fresh fictional order/reference is prepared for each implementation to avoid comparing an initial action with an idempotent replay.
- Collect nearest-rank p50 and p95, DB-adapter query counts, new physical connections during measured calls, time spent in pool `getconn()` (includes queueing), failure counts and throughput proxy (`sample_count / sum(operation durations)`). **The throughput proxy is not measured end-to-end requests per wall-clock second.**
- **Pool initialization/new minimum connections happen before measured operations**. A measured pooled count of 0 new connections does not mean no physical pool connections exist. Returning a connection triggers asynchronous reset; some reset work lies outside the timed operation.
- Evidence: [successful experimental A/B run #42459924957](https://github.com/Maahi17yr/AI-sales-agent/actions/runs/42459924957) and [subsequent success #42460054136](https://github.com/Maahi17yr/AI-sales-agent/actions/runs/42460054136), including pooled PostgreSQL checkout regressions.

## Measured same-runner A/B examples (run 42459924957)

| Workload | Fresh p50/p95 ms | Pooled p50/p95 ms | Observed p50 reduction | DB queries fresh/pooled |
|---|---|---|---|---|
| Open and close a connection, no SQL | 4.467 / 6.811 | 0.031 / 0.071 | 99.31% | 0 / 0 |
| Cart 1 item | 10.341 / 11.901 | 1.540 / 2.221 | 85.11% | 2 / 2 |
| Cart 5 items | 10.565 / 13.683 | 1.660 / 2.616 | 84.29% | 2 / 2 |
| Cart 10 items | 10.626 / 12.366 | 1.936 / 2.505 | 81.78% | 2 / 2 |
| Stock 10 items | 5.280 / 6.629 | 0.874 / 1.839 | 83.45% | 1 / 1 |
| Login | 89.690 / 96.781 | 85.105 / 94.780 | 5.11% | 2 / 2 |
| Session verification | 5.501 / 8.206 | 0.785 / 1.875 | 85.73% | 1 / 1 |
| Transaction rollback | 5.324 / 6.357 | 0.890 / 1.910 | 83.28% | 2 / 2 |
| Checkout preparation | 8.644 / 9.753 | 4.240 / 4.657 | 50.95% | 15 / 15 |
| Checkout confirmation | 12.314 / 13.268 | 7.228 / 7.602 | 41.30% | 26 / 26 |
| Mocked payment verification | 13.187 / 14.376 | 3.258 / 3.902 | 75.29% | 7 / 7 |
| Concurrent 5 cart reads, per batch | 42.384 / 44.508 | 5.820 / 6.696 | 86.27% | 10 / 10 |
| Concurrent 10 cart reads, per batch | 82.101 / 92.745 | 11.347 / 13.398 | 86.18% | 20 / 20 |

**All 17 benchmark scenarios completed with zero errors in this run.** The A/B eliminated *new physical connections per measured pooled operation* once the pool was warmed; for instance 10-item cart opened 180 new fresh connections across 90 measurements, versus 0 new pooled physical connections during the measured window. A 10-concurrent-cart batch recorded approximately **913 ms total getconn time across 90 measured pooled batches** in the first run—evidence of queue/borrow contention requiring further sizing tests. Actual physical pool size starts at 2 and may grow to 10. This is localhost-only proof, not hosted-Neon speed/cost evidence; no statistical confidence intervals were computed.

## Correctness and regressions

The first successful extended CI run #42460054136 passed the standard 65 backend tests, normal PostgreSQL tests, frontend build, both smoke tests, existing cart A/B, the new **six** pooling regression tests and **two existing checkout/verified-payment PostgreSQL tests re-executed with pooling explicitly enabled**. Later branch tests add owner promotion/session revocation, recovery from a deliberately closed connection, and signed webhook replay against PostgreSQL in both pooled and fresh test jobs. **Only count these additional checks once a corresponding final CI run succeeds.**

Transaction checks: rollback on exception, explicit rollback without context, reset of session-level `application_name` across borrowers, pool saturation/timeouts and recovery, customer authentication separation, and double checkout confirmation with idempotent mocked Paystack verification. No provider or customer secrets are emitted by benchmark scripts.

## Remaining uncertainties and pool sizing

- Localhost benchmark is not representative of hosted Neon connection latency, TLS, network jitter, cold starts, or external concurrency. Production and staging were intentionally never queried.
- Pool size and server resource limits need validation per backend process/worker and against hosted Postgres maximum connections; 2–10 is only experimental, **not** a production recommendation.
- `DISCARD ALL` adds reset work and requires disabling automatic prepared statements; evaluate that tradeoff on target infrastructure.
- A maxed-out pool can queue or time out and is more dangerous under higher concurrency. Validate borrow/return across cancellation, connection breakage, app restarts and shutdown, partial commits, deadlocks, owner sessions, row locks and webhook replay.
- The benchmark's throughput numbers are throughput proxies, not true full-service arrival-rate measurements. No confidence interval/significance estimates; pairwise measurements are suggestive, not production guarantees.
- Session-level reset is stronger than the old adapter's behavior, but needs explicit security review before general reuse. A leaked or forgotten `close()` can exhaust the pool.
- The experiment does not use production/staging credentials, migrations, live Paystack or Bedrock, and raw OpenTelemetry tracing remains disabled.

## Verdict: **REVISE before adoption**

The isolated A/B **supports a meaningful performance benefit** beyond cart batching, but production rollout remains **unapproved**. First complete the final branch CI and a security/transaction lifecycle review, then (only after separate approval) perform a dedicated hosted-equivalent *staging* throughput/cost test with safe credentials, closed-loop concurrency and realistic pool sizing. Do not merge or enable by default at this stage.

## Reproduce safely

The dedicated `postgres-experimental-pool` GitHub Actions job starts its own ephemeral PostgreSQL 16 container; only that exact URI is accepted. It installs normal dependencies plus `requirements-pool-experiment.txt` in that isolated job, then executes:

```bash
python -m scripts.benchmark_pool_experiment --iterations 30 --warmup 5 --repetitions 3
python -m unittest scripts.test_postgres_pool_experiment -v
python -m scripts.run_pooled_postgres_regressions
```

No architecture, checkout policies, payment rules, production configuration or shared database migration was changed.
