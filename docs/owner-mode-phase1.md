# Naija Bites Owner's Mode — Phase 1

## Scope
Shared login, server-trusted customer/owner roles, protected owner API,
owner route placeholder, audit log, and secure promotion of an existing account.
The nine management modules and in-app notifications are scheduled for later phases;
external notifications remain deferred. No business-management controls are active yet.

## Promote an account (trusted operator only)
1. Register an ordinary account through the existing login page.
2. Independently verify that the account belongs to the authorised restaurant owner.
3. On a trusted machine with the correct `DATABASE_URL` environment, run:

   `python -m scripts.promote_owner --email owner@example.com --confirm-email owner@example.com --acknowledge-owner-access`

4. The command changes the database role, writes an audit record, and revokes all existing sessions for that account. Sign in again through the normal login page.

Never expose this command as a public API, accept roles from registration payloads, or put database credentials in the frontend. Avoid running it against production until the branch is reviewed and deployed through the normal release process.

## Security
- FastAPI derives roles from database-backed sessions, not client input.
- `GET /owner/me` responds 401 without a session and 403 for a customer.
- Next.js role routing is only navigation; API authorization remains mandatory.
- The existing customer API, payment verification, and chat isolation remain unchanged.
- Schema migration is additive for both SQLite and PostgreSQL.

## Checks
`python -m unittest scripts.test_auth_api scripts.test_owner_auth -v`

`cd frontend && npm ci && npm run build` (set `NEXT_BACKEND_URL` for production builds).
