# Phase 2 — Pool safety, sizing and realistic load validation

**Scope:** This report describes **isolated disposable PostgreSQL only**. Draft PR #47 is stacked on draft PR #46; PRs #42–#46 are unchanged. The pool remains disabled unless both experimental opt-in flags are set, the database URL matches exactly `postgresql://postgres:postgres@127.0.0.1:5432/naija_pool_experiment`, and raw observability tracing is off. This is **not** approval to connect to a staging or production database, merge or deploy.

## Safety review and changes

The base experiment kept PostgreSQL connections in a bounded `psycopg_pool.ConnectionPool`, using `autocommit=True`, explicit transaction `BEGIN/COMMIT/ROLLBACK` in the unchanged database adapter, full `DISCARD ALL` on return, and disabled automatic prepared-statement caching. This project additionally:

- Associates each borrowed connection with the **issuing** pool, not the current global pool, preventing a late return from entering a newer pool after shutdown/restart. Rejects new pool startup while outstanding older leases exist.
- Tracks active and peak borrowed connections and measures reset time/error counts, acquisition time, physical connections, timeouts and pool internals **without writing SQL, session values, or personal data to logs**.
- Increases the **experimental-only** maximum bound from 10 to 15 to test the requested 5/15 configuration. Does not change deployment configuration.
- Serializes initialization to reject concurrent attempts without recreating the deadlock caused by holding a mutex during `pool.wait()`. Shutdown while initialization is underway is rejected.
- Adds real failure-injection tests for deferred commit failure, server-side query cancellation, row-lock timeout, deadlock rollback, terminated backend, shutdown with active lease, and session-state cleanup. The original pool tests and pooled checkout/payment regression suite remain in place.

Checkout confirmation continues to lock the session row and revalidate inventory in its original transaction; payment verification/replay retains its original code path. No production/business-policy or payment logic was changed.

## Setup and methodology

All comparisons use GitHub Actions Python **3.12.16**, disposable localhost **PostgreSQL 16**, fictional accounts, no AWS Bedrock calls, mocked Paystack, and `OBSERVABILITY_MODE=off`.

**Pool sizing and saturation:**
- Pool (min/max) configurations **1/3, 2/5, 2/10, 5/15**.
- **1, 5, 10, 20 and 50** maximum concurrent clients.
- Each load window schedules **60 requests at 300 arrivals/s** using monotonic fixed-interval arrivals; each request executes `SELECT pg_sleep(0.04)` to hold a connection for ~40 ms (deliberately stresses pool capacity).
- Five warmups per window, then **three independently repeated windows** per configuration/concurrency, with pool-profile order counterbalanced each repetition; no fixture setup inside measured requests.
- `actual_completed_rps = successful requests / (time of last completion - start of scheduling)`. Response latency measures time within the worker, including pool acquisition and SQL; **not** whole-batch duration. Errors include pool timeouts; p95/p99 values below include both successful and failed request durations. Per-repetition CPU time and max process RSS are captured, but PostgreSQL/Render resource usage is **not** measured.
- CI local PostgreSQL reported `SHOW max_connections = 100`; this is **not** a verified hosted Neon limit.

[Three-repetition sizing run #42462091335](https://github.com/Maahi17yr/AI-sales-agent/actions/runs/42462091335) **passed**. The numbers below are the **medians of the three individually reported 50-client results**, each of which contains 60 scheduled requests. Failed-request latency is bounded by the 200 ms pool acquisition timeout, so smaller-pool p95s are not direct apples-to-apples **successful-response** latency comparisons.

| Pool min/max | 50-client completed req/s | p95 (ms) | p99 (ms) | Failed / 60 | Peak active |
|---|---:|---:|---:|---:|---:|
| 1 / 3 | 72.40 | 239.05 | 239.99 | **30** | 3 |
| 2 / 5 | 118.84 | 235.97 | 240.24 | **10** | 5 |
| 2 / 10 | 218.83 | 76.75 | 76.99 | **0** | 10 |
| 5 / 15 | 252.27 | 40.51 | 40.55 | **0** | 13 |

At **20 clients**, 1/3 failed **9 of 60**; 2/5, 2/10 and 5/15 had zero failures. At 1/5/10 clients, all profiles completed without errors in all three repetitions. At the stress load, **1/3 and 2/5 are insufficient**; 2/10 and 5/15 complete the given synthetic workload. The ~252 req/s near 5/15 reflects the approximately 13 concurrently needed connections at 300 arrivals/s × ~40 ms SQL hold; it is **not a prediction for Naija Bites application throughput**.

### Session reset and rollback

[Reset benchmark run #42461666985](https://github.com/Maahi17yr/AI-sales-agent/actions/runs/42461666985): three alternating repetitions of 30 measured reset operations per variant after five warmups per repetition. Measured on a standalone disposable PostgreSQL connection, not in a running app pool:

| Operation | p50 ms | p95 ms | p99 ms |
|---|---:|---:|---:|
| `DISCARD ALL` | 0.114 | 0.159 | 0.258 |
| `RESET ALL` | 0.109 | 0.134 | 0.192 |
| Explicit rollback | 0.095 | 0.127 | 0.142 |

A security demonstration proved that **`RESET ALL` leaves a temporary table and its data accessible**. The same table becomes inaccessible after `DISCARD ALL`. Thus `RESET ALL` is **not a safe replacement**. Psycopg pool resets happen asynchronously after connection return and may extend beyond an operation's measured latency; reset totals are diagnostic rather than exact per-request amortized cost. Never remove `DISCARD ALL` to pursue a negligible benchmark difference.

## Realistic mixed workload (not an LLM benchmark)

The open-loop script constructs **84 requests per case** (12 each: chat-related session DB read, authentication session validation, login, cart viewing, checkout preparation, checkout confirmation, and mocked Paystack verification). Each mutating checkout/payment request has a distinct preprovisioned fictional session and payment/order; fixture creation is excluded from the timed load. The target arrival rate is **75 requests/s**, and concurrency caps are **10** or **50** client threads. No Bedrock inference or live HTTP traffic is included.

**Response latency** is the interval from **scheduled arrival to completion**, including any queueing before thread execution. Actual throughput is the number of successful completions divided by the true elapsed wall-clock test time. This intentionally differs from the earlier PR #46 inverse-duration throughput proxy. After each workload, assertions verify order line prices, discounts and totals, order and payment ownership and status, authenticated customer tokens/roles, and exact inventory deductions.

[Verified 84-request mixed-load run #42461948362](https://github.com/Maahi17yr/AI-sales-agent/actions/runs/42461948362): zero failures in all six scenarios, with all post-run invariants passing.

| Mode | Max clients | Actual success/s | p50 response ms | p95 response ms |
|---|---:|---:|---:|---:|
| Fresh connections | 10 | 39.26 | 627.47 | 1246.98 |
| Pool 2 / 5 | 10 | 57.97 | 286.85 | 644.05 |
| Pool 5 / 15 | 10 | 56.22 | 61.37 | 904.18 |
| Fresh connections | 50 | 38.50 | 472.60 | 1774.78 |
| Pool 2 / 5 | 50 | 56.00 | 358.26 | 703.19 |
| Pool 5 / 15 | 50 | 54.37 | 73.80 | 952.32 |

**Interpretation:** local pooled runs are faster than fresh connections on these representative operations, but the 75/s scheduled target was **not met**. High p95 persists even with 15 connections, plausibly due to login/password-hashing CPU and scheduling contention (not isolated). A larger pool does not guarantee higher throughput or lower tail latency. The result is *not* evidence for a specific production pool size. Authentication is especially CPU-bound; no real model/network measurements were made.

## Injected failures and data integrity

On disposable PostgreSQL, the added regression suite verifies:

1. Deferred unique constraint **fails at COMMIT** and both inserts roll back; connection is subsequently usable.
2. **Server-side query cancellation** via `pg_cancel_backend` aborts the operation, and a later borrower can query safely.
3. Concurrent `UPDATE` with a short PostgreSQL **lock timeout** loses no partial updates.
4. A genuine two-row **deadlock** rolls back the victim; winner's transaction commits exactly once.
5. Shutdown with an **active borrowed lease** cannot return that lease to a newly opened pool.
6. Terminating a PostgreSQL backend forces the bad connection to be evicted; another connection works.
7. `DISCARD ALL` clears session settings and a temporary table before reuse.
8. Simultaneous initialization and shutdown are rejected while startup is in progress, without locking the pool's workers.

These extend PR #46's existing eight pool tests (owner promotion and session revocation, customer isolation, exhaustion/recovery, rollback, reset, shutdown) and three pooled PostgreSQL checkout/payment tests (double confirmation, mocked payment idempotency, signed webhook replay). They do **not** simulate a restart of the entire PostgreSQL service, HTTP/client disconnect at the ASGI layer, forced app process death, or multi-instance deployment. Those remain unverified and block production readiness.

## Read-only hosted architecture

The following are **metadata/configuration observations only**; no production/staging database connections, secrets, service logs, env values or deployments were accessed.

- Repository `render.yaml` specifies `uvicorn core.api.server:app` without `--workers`, a single Uvicorn process in the declared configuration. The Render account metadata shows `naija-bites-demo-api` on **Free/Oregon**, one instance, branch `main`; and `naija-bites-owner-staging` on **Free/Oregon**, one instance, branch `feature/reliable-server-driven-checkout-20261101`. Runtime worker/instance counts can change, so verify before selecting a pool size. These instances have **not** received this experimental branch.
- Neon project **naija-bites** reports PostgreSQL **18**, region **AWS us-east-1**, compute size range **0.25–2 CU**; the experiment used local PostgreSQL **16**. Neon database connections, actual `max_connections`, current connection-string type, running process counts and hosted RTT were **not** inspected. PostgreSQL 18 compatibility needs independent verification.
- Render Oregon and Neon us-east-1 are in **different regions**, adding potential round-trip latency versus same-region deployments. We have **not** measured their network latency.
- Budget **maximum direct physical DB connections = SUM over services/instances (processes × pool maximum per process)**. Hypothetically, one process with max15 consumes up to 15, but other services/worker processes and deploy overlap may multiply this; never size against the disposable local limit of 100.
- Neon supplies a built-in **PgBouncer** transaction-pooling endpoint (often indicated by a `-pooler` hostname). Application pooling above PgBouncer may not provide equivalent benefit, and transaction/session reset semantics require dedicated compatibility review. The live `DATABASE_URL` and whether it points to a direct or pooled endpoint were **not** read.
- Render Free services may idle-spin-down/restart, and Render sends **SIGTERM** during deployment with a default ~30s graceful shutdown window. Application lifespan integration and draining active pooled transactions are **not implemented**. Larger pool floors may increase open connections and influence database compute-suspend and resource costs.
- Official background references: https://render.com/docs/free , https://render.com/docs/deploys , https://render.com/docs/outbound-connection-resets and https://neon.com/blog/pgbouncer-the-one-with-prepared-statements .

## Decision and residual risks

**REVISE — not ready for staging integration yet.** The experiment strongly supports local connection-reuse benefits and demonstrates safety of tested transaction paths. However:

- Small pools time out under the controlled burst. A 5/15 pool completes this *specific* synthetic benchmark but cannot be recommended on an unknown live connection budget.
- Mixed-load throughput misses target and has high tail latency even with large pools; investigate CPU hashing, async blocking, and realistic arrival/service profiles.
- Full PostgreSQL restart, ASGI cancellation, app SIGTERM drain, multiple workers, direct-versus-Neon-PgBouncer endpoint, PG18 compatibility, network latency/cost and hosted resource budgets remain untested.
- The experimental pool is **strictly localhost/CI guarded**, disabled by default; its reset and transaction semantics should be security-reviewed before a **separately approved** staging-equivalent test. No policy, payment behavior or infrastructure is changed here.

**Next recommended task:** a separately approved **staging-readiness plan and isolated PG18/PgBouncer compatibility and shutdown test**, not an actual staging connection or rollout. Preserve `DISCARD ALL`, monitor wait time and timeouts, and allocate conservative per-process connection budgets.

## Reproduction

CI job `postgres-pool-safety-load` provisions its own fresh PostgreSQL 16 service and installs `requirements.txt` plus `requirements-pool-experiment.txt` only in that job:

```bash
python -m unittest scripts.test_pool_safety_validation -v
python -m scripts.benchmark_pool_reset
python -m scripts.benchmark_pool_sizing --requests 60 --arrival-rps 300 --repetitions 3
python -m scripts.benchmark_pool_mixed_load --arrival-rps 75 --per-kind 12
```

Only run against the exact disposable localhost database URI named above; scripts refuse other environments. Checkout/stock fixture setup is excluded from measured arrivals, and all output is aggregate and privacy-safe.
