# Naija Bites — Demo deployment guide

**Fictional demo only. Paystack TEST mode is required. Do not enter real customer/payment information.** AWS Bedrock and other hosting services may incur costs.

## Services and configuration

- **Vercel:** Next.js frontend; set **Root Directory** to `presentation/frontend`.
- **Render:** FastAPI backend; the root `render.yaml` installs dependencies, builds the RAG FTS5 index and launches `application.api.server:app`.
- **Neon:** PostgreSQL for customer, cart, inventory and order data; configure `DATABASE_URL` as a secret on Render.
- **RAG:** A generated SQLite FTS5 index of fictional Markdown policies. This is separate from the Neon business database.

## Before deployment

1. Check out a verified branch and review `README.md` and `docs/ARCHITECTURE.md`.
2. Ensure no `.env` files, private keys, credentials or local databases are tracked.
3. In Render, set `NAIJA_BITES_ENV=demo` and `DATABASE_URL` to the **Neon PostgreSQL** URI. Demo mode refuses to start without `DATABASE_URL`.
4. Set `NAIJA_BITES_FRONTEND_ORIGIN` to the deployed Vercel HTTPS origin, and `PAYSTACK_CALLBACK_URL` to that origin plus the application's payment-return path.
5. Set `PAYSTACK_SECRET_KEY` to a **sk_test_** key only. Configure `BEDROCK_MODEL_ID`, `AWS_REGION` and appropriately scoped AWS credentials.
6. In Vercel, set `NEXT_BACKEND_URL` to the Render HTTPS service URL. Do not expose `DATABASE_URL` or secrets through `NEXT_PUBLIC_` variables.
7. Confirm that the Vercel Root Directory is **`presentation/frontend`**, not the old `frontend`.

## Smoke check

- Open the frontend and check signup/login/logout.
- Browse menu, add items to cart and verify quantities/prices against the UI.
- Prepare checkout, review totals, and confirm only through the application-controlled flow.
- Use **Paystack TEST** payment flows only.
- Restart the backend and confirm customer/order data survives in Neon.
- Verify the backend `/ping` endpoint and check logs for startup errors.

## Limitations

- Render free-tier file storage is ephemeral: local Strands session files and the generated RAG index can be lost across restarts, but the RAG index is rebuilt during deployment. **Neon** is the persistent business-data store.
- This is not a production payment or food-ordering service.
- If Neon is unavailable, demo mode should fail instead of silently switching to SQLite.

## Local development

Run from the repository root:

```powershell
python -m pip install -r requirements.txt
python -m application.rag.ingest
python -m uvicorn application.api.server:app --reload --port 8000
```

In a second terminal:

```powershell
cd presentation/frontend
npm install
npm run dev
```
