# Naija Bites — Repeatable performance baseline (Phase 0)

**Safety:** Run only with disposable local SQLite and mocked Paystack/Bedrock. Do not use production, staging infrastructure, live provider calls, actual customer data, or real API keys. No migrations or owner promotion are required. Keep OpenTelemetry tracing `OBSERVABILITY_MODE=off`.

## What is actually measured

- Backend: `request_metric` per chat/checkout/payment request (duration, status), `db_metric` per adapter execution, `db_request_summary` counts, `operation_metric` for RAG and Paystack, and `agent_stream_latency` first-event / first-text / total duration.
- Tools: `tool_events.jsonl` has tool name, duration, outcome and generated request/trace IDs.
- Tokens: `agent_token_usage` reads *provider-reported* counts when the Strands agent supplies them. A mocked agent normally has no real token usage. Do not infer token cost from a fictional response.
- Frontend: developer-console `[Naija Bites latency]` and `[Naija Bites requests]` for chat turns, checkout confirmation and payment initialization/verification, with stage counts including `/auth/me` preflights. These are client-side browser observations, not CI measurements.
- **Important limitations:** backend first text is a streaming text-delta event, not a direct model API token clock; request-level DB summary can exclude DB work after the streaming response starts; per-query duration includes adapter work but not necessarily later cursor fetching; frontend counters record one active journey at a time and should not be used for overlapping concurrent journeys or complete page-load counts.

## Controlled procedure

1. Create a clean Python 3.12 environment and install `requirements.txt`. Use `DATABASE_URL=""`, `NAIJA_BITES_ENV=development`, `OBSERVABILITY_MODE=off`, a new temporary `NAIJA_BITES_DATA_DIR`, and `MONITOR_LOG_PATH` in that temporary directory. Never reuse a local database containing real customer data.
2. Run tests with **no real paid services**:
   ```bash
   python -m unittest scripts.test_auth_api scripts.test_owner_auth scripts.test_owner_dashboard scripts.test_checkout_reliability scripts.test_checkout_logging scripts.test_owner_cli_config scripts.test_request_correlation scripts.test_tool_metrics scripts.test_performance_metrics scripts.test_context_memory -v
   ```
   Use the existing GitHub Actions `owner-postgres-migration` job for isolated PostgreSQL regression coverage. It runs `scripts.test_owner_postgres` and `scripts.test_checkout_postgres` on its ephemeral PostgreSQL service. Do not point these tests at Neon or another shared DB.
3. For **repeatable synthetic timing measurements** (no network), run:
   ```bash
   python -m scripts.benchmark_phase0 --iterations 30 --warmup 5
   ```
   The script uses an in-memory SQLite connection and mocked HTTP provider response; the resulting p50/p95 numbers measure **local synthetic overhead**, not real Bedrock/Paystack latency or deployed concurrency. Keep machine, Python version, warmup, input fixture and iteration count constant when comparing branches.
4. To measure browser request counts, run a **local** Next.js + FastAPI frontend/backend from the repo (with disposable SQLite), sign in with fictional details, then perform the same set of actions per run: (A) one normal chat request using a mocked agent or a local stub; (B) prepare/review and confirm a fictional checkout; (C) initialize/verify payment using a *mocked provider*. Do not follow a real payment authorization link. Record `[Naija Bites requests]` from the browser console and backend counters for each trace. A normal unmocked chat request requires Bedrock and **must not be run without separate approval**.
5. For an actual Bedrock baseline later (separate approval), report environment/model ID, warmup, 30+ measured chat turns, provider-reported input/output/cache tokens, tool-call counts, and first-text/total durations. Avoid collecting prompts, tool arguments, customer identifiers or payment references.
6. For each comparable scenario calculate **p50** and **p95** from individual measured samples, plus `failed / total` request error rate and per-stage request/tool counts. Use the same workload and environment before and after every proposed optimization. Do **not** claim performance improvements on this Phase 0 instrumentation branch without such matched data.

## Practical review template

| Scenario | Environment | N | p50 ms | p95 ms | Errors/total | Model calls | Tool calls | Input/output tokens | Requests |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Chat turn | local mocked agent | — | — | — | — | simulated | simulated | unavailable | — |
| Checkout confirmation | local disposable SQLite | — | — | — | n/a | 0 | 0 | n/a | — |
| Payment provider call | local mocked HTTP | — | — | — | n/a | 0 | 0 | n/a | — |

Leave missing values **unavailable** rather than inventing data. Never report synthetic numbers as production improvements.
