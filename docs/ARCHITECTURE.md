# Naija Bites — Architecture and repository map

This is a **layered application**, not a set of independent services. Follow a customer request from top to bottom:

```text
PRESENTATION LAYER
frontend/ (Next.js UI)
        |
        v
APPLICATION / API LAYER
application/api/server.py (FastAPI + AG-UI)
        |
        v
INTELLIGENCE LAYER
application/intelligence/agent.py
application/intelligence/system_prompt.py
        |
        v
TOOLS AND BUSINESS RULES
tools/ (agent-facing operations)
services/ (trusted validation, carts, orders, checkout, payments)
        |
        v
PERSISTENCE LAYER
database/ (repository, schema, PostgreSQL adapter)
        |
        +-- DATABASE_URL set: Neon PostgreSQL
        +-- DATABASE_URL absent in local development: SQLite
```

**Supporting capabilities:** `memory/` handles customer session/preference logic; `rag/` indexes and searches fictional policy documents using a separate local SQLite FTS5 knowledge index; `observability/` provides tracing/hooks.

## Where to find things

| Responsibility | Path |
| --- | --- |
| Web presentation | `frontend/` |
| FastAPI endpoints, auth, streaming | `application/api/server.py` |
| Strands agent factory and prompt | `application/intelligence/` |
| Agent-facing tools | `tools/` |
| Deterministic business rules | `services/` |
| Persistence, schema, seed data | `database/` |
| Memory and preferences | `memory/` |
| Knowledge retrieval | `rag/` |
| Instrumentation | `observability/` |
| Local developer CLI | `main.py` |
| Checkout diagnostics | `diagnose_checkout.py` |

## Compatibility entrypoints

- `agui_server.py` remains a tiny import shim so existing Render and local `uvicorn agui_server:app` commands still work.
- `agent.py` remains a tiny import shim so local development scripts importing `make_agent` still work.
- The frontend remains at `frontend/` because the deployed Vercel project may be configured with that directory as its project root. Moving it requires updating the Vercel project configuration, not just Git files.

## Storage safety

In public demo mode, `DATABASE_URL` is mandatory and the API rejects startup without it. **Never fall back silently to SQLite in the hosted demo.** For local development, leaving `DATABASE_URL` unset uses a local SQLite database with fictional data. The RAG SQLite FTS5 index is a different database from customer/order storage and should not be deleted as a "duplicate Neon database".

## Next refactor stages

Once API and frontend smoke tests pass, group `tools/`, `services/`, `memory/`, `rag/`, and `observability/` under the application layer, and group `database/` under a data layer, updating imports and deployment commands together. Avoid moving every package at once without a runnable verification environment.
