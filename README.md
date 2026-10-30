# Naija Bites — AI Food Sales Assistant

**Computer Science demonstration · Fictional Nigerian food vendor · Paystack TEST mode only**

Naija Bites is a conversational food-ordering demonstration. Customers can browse a Nigerian menu, chat with the AI assistant, manage a cart, review server-calculated delivery fees and totals, and explicitly approve checkout. The AI selects tools, but **trusted Python services and the database enforce the rules**.

## Architecture

| Layer | Directory | Responsibility |
| --- | --- | --- |
| Frontend | `frontend/` | Next.js 15 / React 19 customer interface |
| Core API | `core/api/` | FastAPI endpoints, authentication, AG-UI streaming |
| Intelligence | `core/intelligence/` | Strands agent and system instructions |
| Tools | `core/tools/` | Agent-callable operations |
| Business logic | `core/services/` | Cart, checkout, orders, payment and validation |
| Supporting application | `core/memory/`, `core/rag/`, `core/observability/` | Sessions, knowledge retrieval, tracing |
| Persistence | `data/database/` | Models, repository, seeds and PostgreSQL adapter |
| Developer utilities | `scripts/` | Local CLI, diagnostics, reset utility |
| Documentation | `docs/` | Architecture, setup and deployment |

See **[Architecture and request flow](docs/ARCHITECTURE.md)** for a readable walkthrough.

## Database choice

- **Hosted demo:** Set `DATABASE_URL` to a Neon PostgreSQL connection string. In `NAIJA_BITES_ENV=demo`, startup fails if this variable is missing; the server must never silently fall back to SQLite.
- **Local development:** Leave `DATABASE_URL` unset to use a local SQLite database for fictional data.
- **RAG knowledge search:** `core/rag/` builds a **separate** SQLite FTS5 index from Markdown business-policy documents. It is not the customer/order database.

Never commit secrets or use actual customer/payment data. AWS Bedrock usage may incur costs.

## Run locally

Requires Python 3.12+, Node.js/npm and AWS Bedrock access for AI responses.

From the **repository root**:

```powershell
python -m pip install -r requirements.txt
python -m core.rag.ingest
python -m uvicorn core.api.server:app --reload --port 8000
```

In a **second terminal**:

```powershell
cd frontend
npm install
npm run dev
```

For a local frontend connection, put `NEXT_PUBLIC_AGUI_URL=http://localhost:8000` in `frontend/.env.local` and restart Next.js. Set the necessary environment variables in your root `.env` or shell; see `core/config.py` and the guides in `docs/`.

To use the local command-line demo:

```powershell
python -m scripts.cli
```

## Deployment

- **Vercel:** Set **Root Directory** to `frontend` (formerly `presentation/frontend`).
- **Render:** Use `render.yaml`; it now builds the RAG index via `core.rag.ingest` and runs `core.api.server:app`.
- **Neon:** Keep `DATABASE_URL` as a secret on Render; do not expose it to the browser.
- **Paystack:** Use TEST credentials only.

The older deployment documents in `docs/` may describe previous branch names or storage behavior; the current `render.yaml`, this README and `docs/ARCHITECTURE.md` take precedence.

## Limitations

The assistant can occasionally require a retry. Always verify cart contents and totals against application state, not the model's message. This is a demo, not a production food service.
