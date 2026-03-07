# SupportPortal Local POC

This repository contains a local Proof of Concept for an AI ticketing system.
The project currently focuses on local validation only.

## Project Structure

```
backend/       # FastAPI backend and API/WebSocket endpoints
client_ui/     # Client-facing UI project (independent folder)
engineer_ui/   # Engineer workbench UI project (independent folder)
dashboard/     # Admin dashboard UI project (independent folder)
docs/          # Plan, progress tracking, and UI standards
```

Each UI is developed in a separate folder as requested.

## Quick Start

1. Create and activate a virtual environment:

```bash
python -m venv venv
source venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run the backend server:

```bash
uvicorn backend.main:app --reload --port 8000
```

4. Open the three pages:

- Client UI: [http://localhost:8000/client](http://localhost:8000/client)
- Engineer UI: [http://localhost:8000/engineer](http://localhost:8000/engineer)
- Admin Dashboard: [http://localhost:8000/dashboard](http://localhost:8000/dashboard)

## Current API Endpoints

- `GET /health`
- `POST /api/tickets/query`
- `GET /api/engineer/tickets`
- `POST /api/tickets/{ticket_id}/action`
- `GET /api/dashboard/metrics`
- `GET /api/dashboard/events`
- `WS /ws/engineer`
- `WS /ws/dashboard`

## RAG Integration (PostgreSQL + LangChain)

Customer questions now try a RAG path before FAQ fallback:

1. Retrieve top-k chunks from PostgreSQL pgvector table.
2. Use LangChain (`ChatPromptTemplate` + `ChatOpenAI`) to answer with retrieved context.
3. If RAG is unavailable/fails, fallback to local FAQ rules.

Create a project-root `.env` file (auto-loaded on startup) with:

```bash
OPENAI_API_KEY=your-openai-key
PGVECTOR_DSN=postgresql://user:password@host:5432/dbname
PGVECTOR_TABLE=docagent_chunks
OPENAI_CHAT_MODEL=gpt-4.1
OPENAI_EMBEDDING_MODEL=text-embedding-3-large
RAG_TOP_K=5
```

Then start normally:

```bash
uvicorn backend.main:app --reload --port 8000
```

If `OPENAI_API_KEY` or `PGVECTOR_DSN`/`DATABASE_URL` is missing, the system uses FAQ fallback only.

## Ticket Persistence (AWS PostgreSQL)

Ticket storage is now database-backed and auto-initialized on startup.

Environment variables:

```bash
# Dedicated ticket DB DSN (recommended)
TICKET_DB_DSN=postgresql://user:password@host:5432/dbname?sslmode=require

# Optional: schema name for ticket tables (default: public)
TICKET_DB_SCHEMA=public

# Optional: DB connect timeout in seconds (default: 5)
TICKET_DB_CONNECT_TIMEOUT=5
```

Behavior:

- If `TICKET_DB_DSN` is set, backend writes tickets/messages/events to PostgreSQL.
- If `TICKET_DB_DSN` is not set, backend falls back to in-memory ticket storage.
- `GET /health` now includes `ticket_storage` to show current mode (`postgres` or `memory`).

Tables created automatically:

- `support_tickets`
- `support_ticket_messages`
- `support_ticket_events`

Schema reference: `backend/sql/ticket_storage.sql`.

## Notes

- Ticket data uses PostgreSQL when `TICKET_DB_DSN` is configured; otherwise falls back to in-memory mode.
- The sentiment logic is a lightweight local heuristic placeholder.
- Phase-gate governance is defined in `docs/poc_plan.md` and `docs/poc_progress.md`.
