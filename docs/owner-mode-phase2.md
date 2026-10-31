# Naija Bites Owner's Mode — Phase 2 (stacked on Phase 1)

This branch is based on `feature/owner-mode-phase1-auth`, not `main`.
Do not merge it before Phase 1 has been reviewed and merged.

## Implemented
- Owner overview: verified payment revenue (all-time/today), 7-day sales, order counts, popular paid meals, low-stock alerts.
- Owner orders: latest 50 orders and guarded status progression; verified paid orders can move through fulfilment. Cancellation and stock restoration are deferred to a dedicated safe workflow.
- Menu: view all products, edit price and visibility. Existing cart/checkout business rules remain authoritative.
- Customers: latest 100 customers, order counts and verified spend. Passwords and password hashes are never returned.
- In-app alerts for pending orders, confirmed orders and low stock. External notifications are deferred.
- Backend-only authorization for every owner API, owner audit records for mutations.
- Owner-facing responsive workspace styled to match existing Naija Bites palette.

## Explicitly deferred
Full payment reporting, Owner AI, conversation oversight, promotions and business settings are future phases. Stock quantity editing, pagination, and real-time push notifications are also deferred.

## Validation
`python -m unittest scripts.test_auth_api scripts.test_owner_auth scripts.test_owner_dashboard -v`
`cd frontend && npm ci && npm run build` (configure NEXT_BACKEND_URL).

## Reporting rules
Paid revenue uses only `payments.status = 'paid'` rows and stored paid amounts.
The 7-day trend uses UTC calendar days. The dashboard never estimates or invents revenue.
