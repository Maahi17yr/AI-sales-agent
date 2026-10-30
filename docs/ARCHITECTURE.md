# Naija Bites — Layered architecture

Naija Bites is a **layered application**, not a collection of separately deployed microservices. A request passes through these responsibilities:

```text
CUSTOMER
   |
   v
PRESENTATION
frontend/           Next.js / React chat, login, cart, checkout
   |
   v
APPLICATION API
core/api/server.py        FastAPI endpoints, auth, AG-UI events
   |
   v
INTELLIGENCE
core/intelligence/        Strands agent + system prompt
   |
   v
TOOLS
core/tools/               Agent-callable operations
   |
   v
BUSINESS SERVICES
core/services/            Trusted cart, stock, order, checkout, payments
   |
   v
PERSISTENCE
data/database/                   Schema, repository, database connections
   |
   +-- Hosted demo: Neon PostgreSQL (DATABASE_URL required)
   +-- Local development: SQLite (DATABASE_URL unset)
```

## Supporting components

- `core/memory/` — Session and customer preference operations. Strands model-message session files are stored locally and may be ephemeral on hosted free-tier infrastructure.
- `core/rag/` — Fictional policy Markdown documents, FTS5 indexing and retrieval. The RAG index uses a **separate local SQLite file**, even when customer/order records use Neon.
- `core/observability/` — Optional tracing and tool-call instrumentation.
- `scripts/` — Local command-line entrypoint and maintenance/diagnostic utilities. These are not API routes.
- `docs/` — Architecture and setup notes.

## Trust boundary

The AI may interpret a request and call tools. It cannot directly approve a checkout or declare payment success. The API authenticates customers, trusted services calculate totals and validate state, and the customer explicitly confirms the prepared checkout. The database is the source of truth.

## Key code paths

| Purpose | Path |
| --- | --- |
| Customer interface | `frontend/app/page.tsx` |
| HTTP API and streaming | `core/api/server.py` |
| Agent factory | `core/intelligence/agent.py` |
| Agent instructions | `core/intelligence/system_prompt.py` |
| Agent tools | `core/tools/` |
| Business rules | `core/services/` |
| SQL and schema | `data/database/` |
| Config | `core/config.py` |
| RAG indexing | `core/rag/ingest.py` |
| Local CLI | `scripts/cli.py` |
| Checkout diagnostic | `scripts/diagnose_checkout.py` |

## Startup and deployment

Run commands from the repository root:

```powershell
python -m core.rag.ingest
python -m uvicorn core.api.server:app --reload --port 8000
```

For the UI:

```powershell
cd frontend
npm install
npm run dev
```

Render uses `render.yaml`. Vercel must use `frontend` as its project root; changing Git folders does not automatically update Vercel project settings.

## Storage and safety

The hosted demo requires `NAIJA_BITES_ENV=demo`, a valid `DATABASE_URL`, and Paystack TEST credentials. It must fail closed if Neon is unavailable rather than quietly write orders to SQLite. Local development may use SQLite with **fictional data only**. RAG's SQLite index is generated from policy documents and does not replace Neon.

## Refactoring notes

The previous root entrypoints (`agent.py`, `main.py`) and previous package imports have been replaced with fully qualified module paths. A temporary `agui_server.py` forwarding module and `rag/ingest.py` build wrapper remain to keep the existing Render service working until its dashboard build/start commands are updated to `python -m core.rag.ingest` and `python -m uvicorn core.api.server:app --host 0.0.0.0 --port $PORT`. Update local shortcuts and hosting project settings before redeploying. This restructuring does **not** imply that a full end-to-end smoke test has passed.
