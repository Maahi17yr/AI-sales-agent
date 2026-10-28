# Naija Bites — AI Food Sales Assistant

**Computer Science project demonstration · Fictional Nigerian food vendor · Paystack TEST payments only**

Naija Bites is a conversational ordering demo. Customers can explore a 30-item Nigerian menu, ask questions, add meals to a cart, review delivery fees and order totals, and explicitly approve checkout. The assistant helps with conversation and tool selection; **Python services and the database** enforce prices, stock, authorization, and order confirmation.

## Live demo

- Frontend: https://ai-sales-agent-uerh.vercel.app/
- Backend API: https://naija-bites-demo-api.onrender.com/

> **Demo only:** No real food is sold or delivered. Use fictional customer details and test-mode payments. AWS Bedrock usage may incur costs.

## Architecture

| Layer | Technology | Responsibility |
| --- | --- | --- |
| Web UI | Next.js 15, React 19, TypeScript | Chat, login, cart, checkout review |
| API | FastAPI, AG-UI | Authenticated requests and agent streaming |
| Agent | Strands Agents, AWS Bedrock | Conversational decisions and registered tool calls |
| Business logic | Python services | Product lookup, cart, inventory, checkout, discounts |
| Database | PostgreSQL (Neon in hosted demo), SQLite locally | Customers, carts, orders, inventory |
| Knowledge retrieval | SQLite FTS5 over Markdown policies | Business FAQ and policy lookup |
| Payment | Paystack TEST | Simulated/test payment workflow |
| Hosting | Vercel and Render | Frontend and backend |

**Important trust boundary:** A model response does not create an order. Checkout is prepared first; the customer reviews and confirms the server-calculated summary through the application.

## Run locally

Requires Python 3.12+, Node.js and npm, and AWS Bedrock access for live agent responses.

From the repository root, install Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

Configure local environment variables (see `config.py`, `DEPLOY_DEMO.md`, and `POSTGRES_SETUP.md`). Never commit credentials or local `.env` files.

Start the backend:

```powershell
python -m uvicorn agui_server:app --reload --port 8000
```

In another terminal, start the frontend:

```powershell
cd frontend
npm install
npm run dev
```

For a local frontend connection, set `NEXT_PUBLIC_AGUI_URL=http://localhost:8000` in `frontend/.env.local`, then restart Next.js.

## Testing

Run the dependency-free exact product matching regression tests:

```powershell
python -m unittest discover -s tests -p "test_exact_cart_matching.py" -v
```

Additional tests are under `tests/` and `rag/tests/`. Some integration tests require installed dependencies, a configured database, or a running backend. Do not interpret a successful unit test as proof of end-to-end agent reliability.

**Known limitation:** The AI may occasionally require a retry. Cart contents and totals must be verified against application state, not inferred from the assistant's message.

## Repository guide

- `agent.py` and `system_prompt.py` — agent registration and behavior
- `agui_server.py` — API endpoints and streaming
- `tools/` — agent-facing operations and exact product-name matching
- `services/` — trusted cart, order and checkout logic
- `database/` — persistence, schema and sample catalogue
- `rag/` — knowledge-base ingestion and retrieval
- `memory/` — session management (generated session files are ignored)
- `frontend/` — customer-facing website
- `tests/` — test and regression cases

See `Use_case.md` for the assignment brief, `DEPLOY_DEMO.md` for deployment notes, and `POSTGRES_SETUP.md` for database configuration.
