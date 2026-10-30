# Naija Bites — low-cost public demo deployment

This is **demo-only**. It is not a production release. **Paystack test mode is mandatory.** Never enter real payment details or actual delivery addresses. No real food orders will be fulfilled. AWS Bedrock usage may incur charges even when hosting is free.

## Architecture

- Next.js frontend on Vercel (root directory: `frontend`)
- FastAPI backend on Render (Blueprint: `render.yaml`)
- First-party browser API proxy: `/api/backend/*` rewritten by Next.js to Render
- HttpOnly, Secure, SameSite=Lax authentication cookie on the **Vercel domain**
- SQLite + Strands session files in `/tmp/naija-bites` on the free Render instance (**ephemeral**)

**Critical limitation:** Render free-instance local data can be erased on restart, sleep, or redeploy. All accounts, orders, inventory changes, payment records and conversations may disappear. A user can be logged out and an old Paystack callback may not be verifiable if data disappears. Use fictitious test data only. Do not use this architecture for real customers.

## 1. Prepare repository branch

Work from `deployment-preparation`. Keep `main` unchanged. Do not commit `.env`, `.env.local`, AWS keys or Paystack keys.

## 2. Deploy backend on Render

1. In Render, create a Blueprint from this repository and select the `deployment-preparation` branch (check branch selection before deploying).
2. Render uses `render.yaml`. Confirm the free plan is available for your account; pricing and availability may vary.
3. Set the secret environment variables:
   - `NAIJA_BITES_FRONTEND_ORIGIN` = `https://YOUR-VERCEL-DOMAIN.vercel.app` (no trailing slash)
   - `PAYSTACK_CALLBACK_URL` = `https://YOUR-VERCEL-DOMAIN.vercel.app/?payment_return=1`
   - `PAYSTACK_SECRET_KEY` = your **sk_test_...** key only
   - `BEDROCK_MODEL_ID` = a model available to your AWS account
   - `AWS_REGION` = the model's AWS region
   - `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` = credentials with least-privilege Bedrock model invocation access (use a role instead if your host supports it)
4. If you don't yet know your Vercel domain, create the Vercel project first, then return to configure these variables.
5. The demo initializes tables and the sample menu automatically on startup; the RAG index builds during deployment. Use a single backend instance only.

## 3. Deploy frontend on Vercel

1. Import this GitHub repository and select branch `deployment-preparation`.
2. Set **Root Directory** to `frontend` and framework to Next.js.
3. Set server-side `NEXT_BACKEND_URL` to `https://YOUR-RENDER-SERVICE.onrender.com` (no trailing slash).
4. Set `NEXT_PUBLIC_DEMO_MODE=true` so the site visibly warns visitors this is not a real food service.
5. **Do not set** `NEXT_PUBLIC_AGUI_URL` in Vercel. The browser must call relative `/api/backend/*` URLs through the Next.js rewrite; direct cross-site cookies are unreliable.
6. Deploy, and copy the actual Vercel HTTPS domain to `NAIJA_BITES_FRONTEND_ORIGIN` and `PAYSTACK_CALLBACK_URL` on Render. Redeploy/restart Render after setting them.

## 4. Payment webhook (test mode)

In the Paystack **test** dashboard, set the webhook URL to:
`https://YOUR-RENDER-SERVICE.onrender.com/payments/webhook`

The webhook is validated using the Paystack test secret. The browser callback returns to the Vercel frontend. Keep both in test mode. Render's free service may be asleep when webhooks arrive; do not rely on webhook delivery in this demo.

## 5. Smoke test (use fake information)

- Visit `https://YOUR-VERCEL-DOMAIN.vercel.app` in a fresh browser.
- Sign up with a **fictional demo identity**, then sign in and refresh to confirm the session cookie works.
- Ask the AI for the menu; verify Bedrock replies.
- Create a fake cart and prepared checkout, then explicitly confirm it.
- Complete Paystack's **test** payment and check for the backend-verified success confirmation.
- Log out, log back in and verify isolation with a second fictional account.
- Check Render logs if `/api/backend/ping` returns an error.

## Security / operational caveats

- **Never set `NAIJA_BITES_ENV=production`**. Only `development` and explicitly configured `demo` are allowed.
- The demo exposes customer signup to the public. It does **not** have production-grade abuse prevention, monitoring, backups, or persistent data.
- Authenticated state is enforced on the FastAPI server; the frontend login gate alone is not a security boundary.
- `NAIJA_BITES_FRONTEND_ORIGIN` must exactly match the browser's origin. If you later add a custom domain, update this setting and redeploy the backend.
- Hosted AI may cost money and the free backend may cold-start, time out, or lose data. Use budgets/usage limits at AWS.
- Production requires a durable database (prefer PostgreSQL), persistent session storage, a rate-limiting strategy, stronger security review, and real payment/fulfilment processes.
