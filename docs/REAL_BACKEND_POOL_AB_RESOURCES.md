# Real Naija Bites backend — isolated fresh-vs-pooled comparison (2026-11-08)

## Purpose and boundary
Run the **actual** `core.api.server` FastAPI app, `data/database.postgres`, `core.services.cart`, `core.services.checkout`, `core.services.order_service`, and customer authentication on separate temporary Render services with fresh vs pooled PostgreSQL connections. This is **not** the simplified `scripts/live_pool_lab.py` site.

- Source: `feature/reliable-server-driven-checkout-20261101` (existing staging backend code), with ONLY experimental adapter changes and isolated wrapper.
- Branch: `experiment/real-backend-pooling-ab-20261108`. Do not merge into main or existing staging.
- Neon project: `green-hat-88213342` / `naija-bites-real-backend-ab-temp-20261108`, PostgreSQL 18, direct host `ep-late-mountain-bbo66p9r.c-15.us-east-1.aws.neon.tech`, branch `br-odd-thunder-bbyh73on`.
- Databases: `naija_real_fresh` (fresh) and `naija_real_pool` (pooled). Both start empty and are seeded by the **real app's existing startup seed**. Never copy production data.
- Fresh Render service: `naija-bites-real-fresh-temp-20261108` (ID `srv-dbo4gu67bikc73funs0g`), URL `https://naija-bites-real-fresh-temp-20261108.onrender.com`.
- Pooled Render service: `naija-bites-real-pool-temp-20261108` (ID `srv-dbo4h0m7bikc73fuo2ag`), URL `https://naija-bites-real-pool-temp-20261108.onrender.com`.
- Both are Render Free Virginia, autoDeploy disabled.
- Separate temporary HTTP comparison runner: `scripts/real_backend_ab_runner.py`, **up to 8** sequential fictional customer journeys per mode, no external AI/payment calls; reports aggregate timings. Runner service: `srv-dbo4hogu01pc73e89v40` (`naija-bites-real-ab-runner-temp-20261108`), https://naija-bites-real-ab-runner-temp-20261108.onrender.com, Free Virginia, autoDeploy off. Each uses `python -m uvicorn scripts.real_backend_ab_app:app --host 0.0.0.0 --port $PORT` and `pip install -r requirements.txt && python -m core.rag.ingest`.
- The pool uses psycopg_pool 2–5 physical connections, 3s wait timeout, and DISCARD ALL reset on release. Fresh opens a new direct PostgreSQL connection per DB operation.
- Environment: `NAIJA_AB_LAB=1`, `NAIJA_AB_POOL_MODE=fresh|pooled`, `NAIJA_BITES_ENV=demo`, `OBSERVABILITY_MODE=off`, exact direct Neon DATABASE_URL (secret), `NAIJA_BITES_FRONTEND_ORIGIN` exact service URL, dummy `sk_test_disabled_real_ab_lab` key, matching callback, dummy Bedrock model id, **no AWS credentials**, `NAIJA_AB_LAB_KEY` secret.
- `/__ab/cart/add` is a guarded *test-only* HTTP route calling the **actual CartService.add_item**. It avoids the paid model but uses real application business logic.
- All API routes except `/ping` require the lab access key; `/agent`, `/payments/*`, `/docs` and OpenAPI are disabled. No real AI calls, Paystack calls, payment completion, production traffic, customer data, or delivery fulfillment.
- Existing staging service `srv-dbj2eee7bikc73e7vegg` and production service `srv-dbgc5je7bikc73fm1v40` and Neon `fragrant-cake-44695541` must not be modified.

## Comparison validity
The experiment uses identical real application code and dedicated databases in the same Neon project. Test authentication, menu, cart, checkout prepare/confirm, and pending-payment recovery; do **not** claim agent streaming or Paystack latency improvements. Warm up both Render instances and Neon, use identical synthetic data, capture per-endpoint latency and errors, repeat stages, and investigate any HTTP 500 or connection pool timeout before scaling.

## Cleanup — ask for explicit approval before deletion
- [ ] Stop any load driver.
- [ ] Export aggregate measurements and preserve PR for review.
- [ ] Delete only the two temporary backend Render services and the temporary runner service listed here.
- [ ] Delete only Neon project `green-hat-88213342` (includes both disposable databases).
- [ ] Close the experiment PR and remove the experiment branch if approved.
- [ ] Do not delete other disposable labs without separate approval.
