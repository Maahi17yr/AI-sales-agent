# Phase 2 — Cart and inventory batching, verified results

## Scope and safety
This change is confined to **read-only** cart product lookup and variant-stock validation. No PostgreSQL pool, production or staging access, migrations, order-confirmation transaction changes, payment processing changes, or policy changes. All tests use fictional test data; Paystack is mocked and Bedrock is not called. PR #44 is stacked on PR #43; both earlier draft PRs are unchanged.

## Files and changes
- `data/database/repository.py`: `get_products_by_ids` and `get_stocks_by_variants`, parameterized, no database connection for empty input, one connection for normal carts, safe 400-entry chunks for larger sets.
- `core/services/cart.py`: replace per-line `get_product` and per-variant `get_stock` calls with batch reads. Preserve cart order, duplicate lines, missing product handling (skip only on display), missing stock default zero, original price/customization logic and error messages.
- `scripts/test_cart_batching.py`: compare outputs against copied pre-change logic for empty, 1/5/10 items, duplicate items and variants, missing/unavailable products, out-of-stock variants, custom add/remove pricing and ingredient checks. Verify oversized chunks reuse one connection and invalid variants/customization policy remain rejected.
- `.github/workflows/chat-safety.yml`: add cart tests and permit CI on the stacked PR base without altering earlier branches.

**Important:** Checkout/OrderService still independently revalidates prices, stock and permitted modifications inside its existing transaction. Display batching is not a stock reservation or a new authorization policy.

## Reproducible benchmarks

The unmodified `scripts.benchmark_postgres_phase2` benchmark runs on an **ephemeral** PostgreSQL 16 CI service under Python **3.12.16**, with 5 warmups then 30 measured calls per workload. Each reported latency is local service time to a database on the same CI runner, not production or hosted Neon latency. GitHub Actions runner performance varies, so cross-run latency differences are descriptive, **not a controlled paired causal measurement**.

- **Before (original implementation):** [run 42408893569](https://github.com/Maahi17yr/AI-sales-agent/actions/runs/42408893569).
- **After (final batched implementation):** [run 42413116035](https://github.com/Maahi17yr/AI-sales-agent/actions/runs/42413116035). All 12 scenarios passed with zero benchmark errors.

| Workload | Before p50/p95 ms | After p50/p95 ms | Before queries/connections | After queries/connections |
|---|---|---|---|---|
| Cart view: 1 item | 11.718 / 13.343 | 16.241 / 17.325 | 2 / 2 | 2 / 2 |
| Cart view: 5 items | 34.119 / 35.623 | 16.152 / 17.057 | 6 / 6 | 2 / 2 |
| Cart view: 10 items | 63.343 / 69.358 | 15.647 / 19.131 | 11 / 11 | 2 / 2 |
| Inventory validation: 1 item | 6.090 / 6.865 | 7.924 / 8.746 | 1 / 1 | 1 / 1 |
| Inventory validation: 5 items | 28.251 / 29.635 | 7.797 / 8.402 | 5 / 5 | 1 / 1 |
| Inventory validation: 10 items | 57.681 / 66.628 | 7.687 / 8.171 | 10 / 10 | 1 / 1 |

**Query and connection reduction** (verified, independent of runner timing):
- Five-item cart: **6 to 2**, 66.7% fewer.
- Ten-item cart: **11 to 2**, 81.8% fewer.
- Five-item inventory validation: **5 to 1**, 80% fewer.
- Ten-item inventory validation: **10 to 1**, 90% fewer.
- One-item workloads: no query/connection reduction.

**Cross-run p50 observations**, not controlled same-runner optimization estimates:
- Cart view, 5 items: (34.119 - 16.152) / 34.119 = **52.7% lower** measured median.
- Cart view, 10 items: (63.343 - 15.647) / 63.343 = **75.3% lower** measured median.
- Inventory validation, 5 items: (28.251 - 7.797) / 28.251 = **72.4% lower** measured median.
- Inventory validation, 10 items: (57.681 - 7.687) / 57.681 = **86.7% lower** measured median.
- One-item cart view: **38.6% higher** cross-run median (11.718 to 16.241 ms); other unrelated benchmark workflows also vary noticeably across runs. Do not attribute it solely to batching.

Checkout confirmation still executes 26 queries / 1 connection for a 5-item transaction; no changes were made there. Five-customer concurrent checkout and mocked payment verification retain their original code paths.

## Verification

Final CI [run 42413116035](https://github.com/Maahi17yr/AI-sales-agent/actions/runs/42413116035):
- **65 backend tests passed**, including 7 new cart-equivalence and large-chunk tests.
- **4 disposable PostgreSQL regression tests passed**.
- Frontend production build and TypeScript counter test **passed**.
- Both local customer/owner and checkout/payment/memory smoke tests **passed**.
- Disposable PostgreSQL benchmark: 12 scenarios, 30 measured iterations after 5 warmups, **zero errors**.

Prior CI failed once due to a missing optional ingredient fixture in the new test. The fixture was corrected without changing business code; subsequent full CI passed.

## Remaining risks and next decision

1. Long carts are chunked into 400 unique identifiers per SQL statement; additional chunks increase query counts above typical-cart baselines, but the connection stays shared.
2. Product reads and stock reads are snapshots, not transactionally tied. This was already the case before batching. Checkout must continue authoritative stock validation and deduction.
3. Not all possible customization combinations or real-provider behavior have been tested; this PR preserves existing mutation and checkout code.
4. The measured reductions in queries and connections are reliable. Timing improvements in GitHub Actions are **promising but not proven on identical hardware**. For causal before/after latency estimates, use the same-runner comparison or repeated matched runs.
5. Current functional test coverage uses SQLite for direct old-vs-new cart equivalence; existing PostgreSQL integration/benchmark tests verify new read paths on PostgreSQL. No direct old-vs-new PostgreSQL equivalence assertion yet.

**Recommendation:** Review and complete same-runner before/after benchmarking before considering PostgreSQL connection pooling. Pooling could further reduce connection setup, but it is not justified for implementation under this PR and introduces transaction/cancellation risks. Seek separate approval first.

**No merge, no deployments, no infrastructure changes.** PR #44 is draft, stacked on #43.
