# Controlled PostgreSQL A/B — Phase 2 cart batching validation

## Verified environment and results

- Branch: `optimise/phase2-controlled-ab-postgres-20261107` based on PR #44. Previous PRs #42/#43/#44 untouched.
- [Successful CI run 42414011465](https://github.com/Maahi17yr/AI-sales-agent/actions/runs/42414011465).
- CI: Ubuntu GitHub Actions runner, Python **3.12.16**, isolated localhost **PostgreSQL 16** (`naija_ab`), no paid model or provider calls; raw tracing off.
- Executed **three repetitions of five warmups + 30 measured operations**, for **90 measured samples per variant per workload**; the original and batched implementations execute alternately against the same fixtures and CI runner. Fixture setup outside the timed operation. Nearest-rank p50/p95; query count from DB adapter metrics; new connection count from instrumented `psycopg.connect`.
- There were **zero errors** in the A/B benchmark. Tests compare full returned values and stock validation outcomes per operation. They are not real user network latencies.

| Scenario | Original p50 / p95 (ms) | Batched p50 / p95 (ms) | p50 difference | Original queries/connections | Batched queries/connections |
|---|---|---|---:|---|---|
| Cart view, 1 item | 14.269 / 16.548 | 14.291 / 17.769 | **0.15% slower** | 2/2 | 2/2 |
| Stock check, 1 item | 7.126 / 8.248 | 7.044 / 8.287 | **1.15% faster** | 1/1 | 1/1 |
| Cart view, 5 items | 42.959 / 49.490 | 14.552 / 16.725 | **66.13% faster** | 6/6 | 2/2 |
| Stock check, 5 items | 36.941 / 43.076 | 7.601 / 8.996 | **79.42% faster** | 5/5 | 1/1 |
| Cart view, 10 items | 74.285 / 90.838 | 13.796 / 15.998 | **81.43% faster** | 11/11 | 2/2 |
| Stock check, 10 items | 67.436 / 71.882 | 7.001 / 7.821 | **89.62% faster** | 10/10 | 1/1 |

**Interpretation:** Same-runner alternating results strongly support substantial latency improvement for 5/10-item carts and stock checks, with verified reduction in database queries and connections. The 1-item cart measurement differs by only **0.022 ms** (14.269 to 14.291), so the previous cross-run apparent 38.6% regression **did not reproduce**. No special-case path is justified. We have not computed confidence intervals or paired per-repetition significance, so the results are strong evidence rather than formal statistical proof. Runs were compared in alternating order but use aggregate p50, not a statistical hypothesis test. There is still hosted database/network variance in production.

## PostgreSQL equivalence

On the **same disposable PostgreSQL CI database**, `python -m unittest scripts.test_cart_batching_postgres -v` passed **5 tests** for empty / 1 / 5 / 10 item carts, duplicate ordering, variants and quantities, missing/unavailable and out-of-stock products, priced modifications, ingredient shortfall, customer session separation, unchanged discount calculation, and cart lists over the 400-key chunk boundary. The original cart and stock functions were copied for test-only use and compared directly with optimized implementations. No checkout, price policy or stock-deduction code was changed.

The minimum PostgreSQL test schema contains fictional ingredient rows, not real customers. An invalid extra on a nonstandard variant still fails. The core checkout confirmation path performs **independent stock checking and order creation under the existing explicit transaction lock**. A cart view or read-only stock check is never a reservation.

## CI verification

[Run 42414011465](https://github.com/Maahi17yr/AI-sales-agent/actions/runs/42414011465) completed successfully:
- Existing configured backend suite: **65 passed**.
- Existing disposable PostgreSQL integration suite: **4 passed**.
- Additional disposable PostgreSQL original-vs-batched equivalence: **5 passed**.
- Frontend production build **passed**.
- Both local customer/owner and checkout/payment/memory smoke tests **passed**.
- Prior 12-scenario disposable PostgreSQL performance suite **passed**; controlled six-scenario A/B suite **passed**.

## Connection-pooling readiness — not implemented

The current `data/database/postgres.py` adapter opens a fresh synchronous `psycopg.connect(..., autocommit=True)` connection for each `PostgresConnection`. Entering a context manager calls explicit `BEGIN`; on successful exit it commits, and on errors it rolls back. `close()` also rolls back an open transaction before closing. These semantics are important for authenticated customer isolation and checkout/payment idempotency. No shared connections, pool, transaction or retry paths have been modified.

The A/B benchmark proves **reducing unnecessary connections** is worthwhile for multi-item reads (11 to 2 for 10-item cart, 10 to 1 for 10-item stock check). It does **not** isolate the cost of opening a connection from query latency or prove a further pooling benefit. The remaining baseline still opens 2 connections for a 10-item cart view (session fetch + product batch), and 1 for stock validation. Before pooling, test:
- Borrow/return under concurrency, pool saturation and timeouts, minimum/maximum pool size.
- Guaranteed rollback and reset on exceptions and cancellations, failed commit handling.
- Prevention of leaking transaction/session state between customer and owner requests.
- Checkout row locks, double-confirmation, webhook replay, payment idempotency.
- Shutdown/restart and broken-connection handling; PostgreSQL hosted connection limits.
- Matched p50/p95 with throughput and error rates at 1/5/10+ simultaneous requests.

**Recommendation:** Do not implement pooling in this PR. Consider a **separate, approved pool experiment** only after connection-open cost is isolated in a disposable test, under the same transaction and isolation safeguards. Further optimization of in-transaction checkout queries is a separate higher-risk area and should not be bundled into pooling.

## Reproduce

The isolated job `postgres-controlled-cart-ab` provisions `postgres:16`, Python 3.12, and refuses anything other than `DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/naija_ab`, `NAIJA_BITES_POSTGRES_AB=1` and `OBSERVABILITY_MODE=off`.

```bash
python -m unittest scripts.test_cart_batching_postgres -v
python -m scripts.benchmark_cart_ab_postgres --iterations 30 --warmup 5 --repetitions 3
```

These are **not** safe to aim at shared or production databases; use only disposable infrastructure. The Python benchmark validates its database target before creating schema/fixture rows. No production/staging access, no live AWS Bedrock/Paystack, no deployments or merges.
