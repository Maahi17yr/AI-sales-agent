# Naija Bites — temporary Locust load-test dashboard and cleanup

**Created:** 2026-11-08. **Status:** SETUP IN PROGRESS; test is NOT auto-started.

## Scope and resources
- New GitHub branch: `experiment/isolated-locust-load-lab-20261108`, based on `experiment/isolated-live-pool-lab-20261108`. Do not merge into main.
- Test files: `scripts/locust_live_lab.py`, `scripts/run_locust_live_lab.py`, `requirements-locust-lab.txt`, `.github/workflows/isolated-locust-lab.yml`, this document.
- Dedicated load-driver Render Free web service: **PENDING CREATION**. Region: Virginia. AutoDeploy disabled.
- Dedicated dashboard URL: **PENDING CREATION**.
- **Only permitted target:** `https://naija-bites-live-lab-temp-20261108.onrender.com` (existing isolated fictional lab, Render `srv-dbo4103l550s738m0vc0`, separate Neon project `curly-night-52323584`).
- No new database required. Existing isolated lab's fictional data is used. No real customers, production/owner staging, Bedrock, Paystack, or actual payments.
- Dashboard HTTP Basic Auth username and password, plus lab access code, are **Render environment secrets**, never committed to the repository.

## Test behavior and stop conditions
- Locust web UI opens idle. **The user must explicitly press Start.**
- Fixed, safe ramp of **10 users (first 60s), 25 users (next 60s), 50 users (final 60s)**, with 2/3/5 users per second ramp rate; max 180s.
- Roughly half the simulated customers use fresh connections; half use pooled connections (randomly distributed, weighted equally). Locust reports per-mode operation names separately.
- Each fictional customer uses a cookie-isolated session, asks for menu, adds one meal, checks out, simulates a payment, and verifies the order. Max 6 orders per virtual session.
- Max 600 total attempted journeys; automatically stop on **3 request failures** or **p95 response time above 6 seconds** after 40 requests.
- The load-test code sends requests only to the exact hardcoded disposable lab URL, ignoring a host override from the dashboard. Redirects are disabled.
- The Locust dashboard is password protected, and the test runner does not auto-start.
- Pooled connection count on the target remains 2–5; the load driver is a separate Render service.
- The existing fictional menu inventory can deplete across runs. Do not silently reset it or delete lab orders; request permission for any test-data reset.

## How to use
1. Open the dedicated dashboard URL after deployment and enter the provided temporary username and password.
2. Click **Start**. The safe 10 → 25 → 50 ramp is automatic, and it stops after three minutes or earlier if failures/latency are high.
3. Watch **Statistics** (compare `fresh |` vs `pool |`), **Charts** (users, response times, RPS), and **Failures**.
4. Click **Stop** at any time. Export CSV if you want a permanent copy; share results for analysis.

These results measure a scripted, simulated ordering experience against a separate lab database, not the live production app's total response time.

## Cleanup checklist (explicit user approval required)
- [ ] Stop any running load test and export useful aggregate results.
- [ ] Confirm the dedicated load-driver Render service ID and delete **only** that temporary service after approval.
- [ ] Revoke/rotate dashboard credentials and the shared fictional lab access code when finished.
- [ ] Close draft PR and remove temporary branch **only** after checking dependencies and approval.
- [ ] Later, separately clean up the isolated lab Render service and Neon project listed in `docs/ISOLATED_LIVE_POOL_LAB_RESOURCES.md`, after explicit approval.
- [ ] Leave production and existing owner staging resources unchanged.
