# Naija Bites — Measurement accuracy and Phase 2 readiness

**Status:** Draft PR #43, based on `optimise/phase-0-1`, preserving draft PR #42. **No database performance optimization has been implemented.**

## Safety and method

All measurements use fictional customers and disposable PostgreSQL 16 on GitHub Actions with Python **3.12.16**. The benchmark checks for exactly `DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/naija_bench` and `NAIJA_BITES_POSTGRES_BENCH=1`; it refuses other databases. OpenTelemetry is **off**, Paystack's provider request is mocked, and no AWS Bedrock calls occur. No live customer information, model prompts, tool arguments or payment references are output.

Run in a dedicated disposable PostgreSQL 16 environment with exactly the CI database/flag above (never production or staging):

```bash
python -m scripts.benchmark_postgres_phase2 --iterations 30 --warmup 5
```

GitHub Actions job `postgres-performance-baseline` provisions `naija_bench` automatically. **Never run this command with production credentials or shared database.** The script validates the database URL *before* creating any tables. The output contains only aggregate `BENCHMARK_RESULT` and `BENCHMARK_ENV` JSON records. Each scenario uses 5 warmups and 30 measured iterations, nearest-rank p50/p95. Fixture setup and database seeding are outside the measured operation. Measurements include local service execution and connecting to the same-runner PostgreSQL container; they do **not** represent Neon or production network latency. Each runner/CI run can vary; compare only matching environments.

Authentication, cart operations, inventory checks, checkout preparation, checkout confirmation, and payment verification use the existing application services. The concurrent test uses FastAPI ASGI transport with five separately authenticated fictional customers. In the latter, duration is **per five-request batch**, not per individual response. The service benchmarks are **not HTTP requests**; `requests_total=0` is correct and must not be interpreted as zero database work. Query counts come from the instrumented database adapter; connection counts come from wrapping `psycopg.connect` inside this disposable benchmark, without logging URLs.

## Verified baseline — 2026-11-07

Source: [CI run 42408893569](https://github.com/Maahi17yr/AI-sales-agent/actions/runs/42408893569), disposable Postgres 16, Python 3.12.16, 30 measured iterations after 5 warmups.

| Workload | p50 ms | p95 ms | Queries / operation | Connections / operation | HTTP requests (measured) | Errors |
|---|---:|---:|---:|---:|---:|---:|
| Authentication: login | 96.444 | 129.142 | 2 | 1 | 0 (service call) | 0 |
| Authentication: verify session | 5.780 | 6.512 | 1 | 1 | 0 (service call) | 0 |
| View cart: 1 item | 11.718 | 13.343 | 2 | 2 | 0 (service call) | 0 |
| Validate inventory: 1 item | 6.090 | 6.865 | 1 | 1 | 0 (service call) | 0 |
| View cart: 5 items | 34.119 | 35.623 | 6 | 6 | 0 (service call) | 0 |
| Validate inventory: 5 items | 28.251 | 29.635 | 5 | 5 | 0 (service call) | 0 |
| View cart: 10 items | 63.343 | 69.358 | 11 | 11 | 0 (service call) | 0 |
| Validate inventory: 10 items | 57.681 | 66.628 | 10 | 10 | 0 (service call) | 0 |
| Checkout preparation: 5 items | 10.070 | 13.522 | 15 | 1 | 0 (service call) | 0 |
| Checkout confirmation: 5 items | 13.248 | 15.286 | 26 | 1 | 0 (service call) | 0 |
| Mocked Paystack verification: 5-item order | 15.686 | 17.457 | 7 | 2 | 0 (service call) | 0 |
| Five concurrent checkout reads (batch) | 98.859 | 104.920 | 35 / batch | 15 / batch | **150** (5 x 30) | 0 |

A second CI run with the same benchmark after non-performance instrumentation changes also passed ([run 42409237801](https://github.com/Maahi17yr/AI-sales-agent/actions/runs/42409237801)). Differences between these run timings are **runner variance**, not established optimizations.

## Measurement correctness: before versus after

| Concern | Prior implementation | New verification |
|---|---|---|
| Streaming total | Middleware `call_next` returned after headers, before stream completion | Agent stream `finally` records full request duration and query summary; regression confirms DB query made during streaming is counted, with one complete `request_metric` |
| First visible text | First nonempty text delta, including hidden thought/whitespace | Stateful bounded filter skips whitespace and `<thinking>` content; if text exceeds 16 KiB before visibility, first-visible timestamp remains unavailable |
| Stream cancellation/error | Cancellation could be misclassified as completed, and synthetic HTTP 500 was logged after HTTP 200 headers | Explicit `completed/error/cancelled` outcome. The HTTP status reflects headers sent; stream outcome is separate. Cancellation regression runs direct generator closure |
| Token usage | Latest invocation was prioritized, missing earlier model cycles | Aggregate SDK usage now prioritized. Regression fixture validates aggregate input 1,900 vs latest 1,200, **not real token usage**. Cache read/write values tracked only when provider reports them |
| Model and tool calls | Tools recorded durations; model-call count was missing | Strands before/after hooks count model/tool invocations and failures, per request; no payloads retained |
| Overlapping frontend journeys | One global journey was cleared whenever another started | Independent in-memory IDs; regression checks overlapping chat and checkout, background failures and streaming errors |
| Background HTTP requests | Only requests during active journey were counted | Unattributed `apiFetch` requests, direct auth requests and health checks counted separately |

**Caveats:** Strands conversation-manager summarization may invoke a model outside the agent model hooks; its **call count/usage are unavailable**, never zero. Raw Bedrock token counts, prompt costs, real first-visible text latency and browser journey p50/p95 are **unavailable**, because paid model calls and browser traffic were not exercised. The token usage scope is labeled `aggregate` or `latest_only`. Per-query duration excludes subsequent cursor iteration/fetching in some paths. Frontend counters are per page runtime, not persistent across payment redirects; background snapshots may outlive individual journeys.

## Ranked Phase 2 candidates — NOT IMPLEMENTED

1. **Batch cart/product and stock lookups** (high confidence, first task). `CartService.view_cart` calls `repository.get_product` for every item and `CartService._check_all_stock` calls `repository.get_stock` for each variant. **Measured:** cart view scales from 2 queries/connections (1 item) to 11 (10 items); validation scales from 1 to 10. **Potential benefit:** fewer round trips, lower cart latency and connection pressure; magnitude unverified. **Risk:** preserved quantity, pricing, variant, inventory, customization and customer isolation semantics. **Regression:** 1/5/10 item totals, variant combinations, unavailable/changed stock, discount policy, concurrent confirmation, owner/customer isolation, both databases. **Criteria:** repeat this exact benchmark and show fewer queries without changed outcomes.
2. **PostgreSQL connection pooling** (high confidence for connection overhead, greater integration risk). Existing `data/database/postgres.py` constructs `psycopg.connect` for each `get_connection`; benchmark recorded 11 separate connections for 10-item cart view. **Potential benefit:** reduce connection handshakes; magnitude unverified. **Risk:** transaction isolation, pending transactions, connection leakage, checkout idempotency, concurrency and service limits. **Regression:** concurrent requests, rollback/commit, owner access, auth, payment idempotency, transaction lifetime, pool exhaustion. **Criteria:** per-request creation/borrow counts, connection contention and p50/p95 with the same environment.
3. **Avoid blocking database calls in asynchronous routes** (hypothesis; needs profiling). `/checkout` and `/checkout/prepare` use synchronous DB work in `async def` routes; the five-customer checkout-read batch took 98.859 ms p50. This does **not** isolate event-loop blocking from repeated connections, authentication checks or ASGI test overhead. **Potential benefit:** better throughput at concurrency, unverified. **Risk:** transaction/lock safety, cancellation behavior and consistency. **Regression:** parallel customers, same-customer locks, cancellation, payment/checkout integrity, no race conditions. **Criteria:** event-loop delay and throughput at matched levels (1,5,10+ concurrency).

**Do not weaken password hashing to optimize login.** Argon2 computation is deliberately expensive for security. Authentication and session checks are included to quantify costs, not justify removal of safeguards.

## Required sign-off before implementing Phase 2

- Confirm representative fixtures and expected outcomes against application rules.
- Run all backend/checkout/auth/payment regressions and disposable PostgreSQL integration tests.
- Run frontend production build and both local smoke tests.
- Compare matched p50/p95, query and connection counts, errors, stock/account correctness.
- Never merge or deploy without separate explicit approval. Keep OpenTelemetry off and use only isolated resources.

**Recommended first Phase 2 task:** Add read-only batched product/stock retrieval underneath the cart service and verify existing cart view and stock validation behavior remains identical. Implement in a **new** task commit only after approval; do not change payment, checkout, model selection or business policy.
