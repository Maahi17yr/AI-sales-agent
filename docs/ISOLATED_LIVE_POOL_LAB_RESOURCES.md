# Isolated interactive live pooling lab — temporary resources (2026-11-08)

**Scope:** A standalone fictional Naija Bites ordering lab, not the production or existing owner staging app. No AWS Bedrock, Paystack, real payments, real customers, or delivery fulfillment.

- Git branch: `experiment/isolated-live-pool-lab-20261108`
- Git entrypoint: `scripts/live_pool_lab.py`
- Neon project: `curly-night-52323584` — `naija-bites-live-lab-temp-20261108`, PG18, `naija_live_lab`, Virginia/us-east-1, separate newly created project; **not cloned from production**.
- Render service: `srv-dbo4103l550s738m0vc0` — `naija-bites-live-lab-temp-20261108`, Free Virginia, autoDeploy off.
- Render URL: https://naija-bites-live-lab-temp-20261108.onrender.com
- Environment variables on this **isolated service only**: `DATABASE_URL` (secret), `NAIJA_LAB_ISOLATED=1`, `LAB_PUBLIC_ORIGIN`, `LAB_ACCESS_CODE` (secret), `OBSERVABILITY_MODE=off`.
- No Vercel project created; lab serves its own UI directly from Render.
- Pool modes: per-session `fresh` and `pool` (min 2/max 5), same PostgreSQL database and real cart/order writes; pool reset uses `DISCARD ALL`.
- Test data: only fictional menu and sessions, orders, payments; max 8 orders/session, max 5 per item.
- Performance measurements: last 200 samples per action/mode, p50/p95 in milliseconds, including connection acquisition. Browser also shows end-to-end request time. Do not interpret as production capacity.
- Safety: exact Neon project database name, PostgreSQL 18, HTTPS Render origin, access code, origin validation on writes, cookie isolation. No external provider calls.
- **Cleanup** (after user approval): delete the temporary Render service, delete the temporary Neon project, close draft PR/branch. Do not delete the original pooling validation service `srv-dbo3d43m8hqs73fesub0` or project `red-boat-54971627` without separate approval.
- **Production unchanged**. No merge into main.

Note: The access code and database URI must never be committed to GitHub or included in this document.