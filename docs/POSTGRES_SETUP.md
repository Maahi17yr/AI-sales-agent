# Naija Bites — hosted PostgreSQL staging guide

This work is on `feature/postgres-persistence`, NOT `main`.
Your live Vercel and Render deployment should remain unchanged until staging passes.

## Create a database and connect it

1. Sign up at https://neon.tech and create a new PostgreSQL project/database.
2. In the Neon dashboard, open **Connect** and copy the **PostgreSQL connection string**.
   Select the provider's TLS connection URL (include `sslmode=require`).
3. In Render, deploy **a separate staging web service** from
   `feature/postgres-persistence`. Use the same build/start commands as
   `render.yaml` and set the same demo environment variables, but use a
   separate test-only database and credentials.
4. In the staging service's **Environment** tab, add a **secret**:
   `DATABASE_URL` = the full Neon PostgreSQL URI. Never share it in chat,
   commit it, or put it in any `NEXT_PUBLIC_` variable.
5. Configure `NAIJA_BITES_FRONTEND_ORIGIN` and
   `PAYSTACK_CALLBACK_URL` to match your staging frontend origin.
   Do not change the live Vercel proxy until testing is complete.
6. The backend creates its schema and demo menu automatically on startup.
   It refuses to start in demo mode if `DATABASE_URL` is missing.
7. Verify signup, login, logout, order creation, Paystack TEST verification,
   and **restart the staging backend**. Verify the same account still signs in.
8. Once verified, merge the branch to `main`, add `DATABASE_URL` to the
   existing Render service, and deploy. Only then should the live backend use
   PostgreSQL. Keep `NEXT_BACKEND_URL` pointing at the existing service.

## Persistence scope and limits

- PostgreSQL stores customers, password hashes, login sessions, menu,
  inventory, orders, payments, and application cart/session records.
- Existing SQLite accounts **are not automatically migrated**. Lost ephemeral
  accounts cannot be recovered. If a copy remains, migrate separately.
- Strands FileSessionManager's model messages still live in Render's
  temporary `NAIJA_BITES_DATA_DIR` and may disappear on restart.
  Moving model session files to durable storage is a separate improvement.
- The public app is still a demo: fictional customer data and Paystack
  TEST credentials only. Free-tier limits may change, and AWS usage may cost.
- Backups, abuse prevention, monitoring and a full security review are needed
  before handling real customers or payments.
