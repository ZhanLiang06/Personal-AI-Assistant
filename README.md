# Personal AI Assistant for Productivity

A local-first personal AI assistant built around FastAPI, LangChain/LangGraph-style tool use, an Obsidian knowledge base, local conversation memory, SSE streaming, and Google Calendar tools.

This is for my personal assistance. The project is also a learning project for AI agent fundamentals: tool calling, retrieval, memory, observability, and eventually multi-agent design. Future work includes financial trackers, telegram bot integration, advance RAG pipeline and richer personal productivity workflows.

## Current Capabilities

- Chat with a LangChain `create_agent` assistant.
- Search an Obsidian vault through local retrieval.
- Manage daily todos stored in Obsidian markdown files.
- Create, list, update, and delete Google Calendar events through OAuth.
- Store conversations, tool calls, tool results, errors, and future summaries in local SQLite.
- Rebuild conversation history from SQLite before each agent run.
- Stream agent status events to the web UI with Server-Sent Events.
- Show a run trace that reports what each tool call actually cost in milliseconds.
- Track finances: transactions, categories, budgets, goals, and a monthly dashboard.
- Serve the UI as a separate Vite build ("Kairos") deployed to Cloudflare Pages.

## Architecture

```mermaid
flowchart LR
    User["User"] --> WebUI["Kairos web app<br/>web/ (React + Vite)"]
    WebUI -->|POST /chat/stream<br/>SSE| API["FastAPI<br/>src/api/main.py"]
    API --> Context["Conversation Context Builder<br/>src/llm/conversation_context.py"]
    Context -->|rebuilt message history| Agent["LangChain Agent<br/>src/llm/langchain_agent.py"]
    Agent --> Prompt["System Prompt<br/>src/llm/prompts.py"]
    Agent --> Tools["Tool Registry"]
    Tools --> ObsidianTools["Obsidian Tools<br/>src/tools/obsidian.py"]
    Tools --> CalendarTools["Google Calendar Tools<br/>src/tools/google_calendar.py"]
    Tools --> GeneralTools["General Tools<br/>src/tools/general.py"]
    ObsidianTools --> Vault["Obsidian Vault"]
    CalendarTools --> Google["Google Calendar API"]
    Context <-->|load prior events<br/>return history| SQLite["Conversation SQLite<br/>data/conversations.sqlite3"]
    API -->|store user/assistant/tool events| SQLite
    API --> EventLog["Agent Event Logs<br/>data/event_logs"]
```

## Runtime Flow

```mermaid
sequenceDiagram
    participant U as User
    participant W as Web UI
    participant A as FastAPI
    participant C as Conversation DB
    participant L as LangChain Agent
    participant T as Tools

    U->>W: Send message
    W->>A: POST /chat/stream
    A->>C: Load prior conversation events
    C-->>A: Return rebuilt message history
    A->>C: Store user message
    A->>L: Stream agent with history + runtime context
    L->>T: Call tool when needed
    T-->>L: Tool result
    L-->>A: Status events + final reply
    A->>C: Store tool calls/results/reply
    A-->>W: SSE events
    W-->>U: Reply + expandable process trace
```

## Tool Surface

```mermaid
flowchart TB
    Agent["LangChain Agent"] --> Time["get_current_time"]
    Agent --> Notes["search_notes"]
    Agent --> Todos["Daily Todo Tools"]
    Agent --> Calendar["Google Calendar Tools"]

    Todos --> ListTodos["list_daily_todos"]
    Todos --> AddTodos["add_daily_todos"]
    Todos --> UpdateTodos["update_daily_todos"]
    Todos --> DeleteTodos["delete_daily_todos"]

    Calendar --> CreateCal["create_google_calendar_events"]
    Calendar --> ListCal["list_google_calendar_events"]
    Calendar --> UpdateCal["update_google_calendar_events"]
    Calendar --> DeleteCal["delete_google_calendar_events"]
```

## Google Calendar Behavior

Calendar tools are for important scheduled events, not minor daily todos.

- Date only creates an all-day event.
- Date + start time with no end time defaults to 1 hour.
- Create 1 or 2 events without confirmation.
- Create 3 or more events only after confirmation.
- Update 1 or 2 events without confirmation.
- Update 3 or more events only after confirmation.
- Delete any event only after confirmation.
- Before update/delete, the agent should list matching events and use `event_id + expected_title`.
- Raw Google Calendar event IDs are internal tool identifiers and should not be shown in final user-facing replies.

Category color mapping:

| Category | Color |
| --- | --- |
| `career` | blue |
| `learning` | purple |
| `personal` | cyan |
| `finance` | yellow |
| `health` | green |
| `travel` | red |
| `important` | orange |

## Local Setup

Install dependencies:

```powershell
uv sync
npm install --prefix web
```

The API and the web app are two processes. Run the API:

```powershell
uv run uvicorn src.api.main:app --reload --host 127.0.0.1 --port 8000
```

Run the web app:

```powershell
npm run dev --prefix web
```

Open:

```text
http://localhost:5173
```

FastAPI serves JSON only — opening `:8000` directly shows no UI. Vite proxies
API paths through to it, so there is no CORS in development.

## Environment

Create a local `.env` file. At minimum, configure the model provider and local paths used by your setup.

Google Calendar OAuth uses:

```env
GOOGLE_OAUTH_CLIENT_SECRETS_PATH=data/google_oauth_client_secret.json
GOOGLE_CALENDAR_TOKEN_PATH=data/google_calendar_token.json
GOOGLE_CALENDAR_ID=primary
```

Run OAuth setup once:

```powershell
uv run python scripts/google_calendar_oauth.py
```

This opens a browser for Google consent and saves a local token file.

## Important Files

| Path | Purpose |
| --- | --- |
| `src/api/main.py` | FastAPI app, SSE chat endpoint, static web serving |
| `src/llm/langchain_agent.py` | Agent construction, tool registry, event streaming |
| `src/llm/prompts.py` | System prompt and tool-use policy |
| `src/llm/conversation_context.py` | Rebuilds conversation history for the agent |
| `src/db/conver_sqlite.py` | Local SQLite conversation store |
| `src/tools/obsidian.py` | Obsidian search and daily todo tools |
| `src/tools/google_calendar.py` | Google Calendar create/list/update/delete tools |
| `src/api/calendar.py` | Read-only `GET /api/calendar/today` for the web UI |
| `src/api/finance.py` | Finance JSON API under `/api/finance` |
| `scripts/google_calendar_oauth.py` | Google OAuth token setup |
| `web/src/styles/base.css` | The design token system — every colour and font resolves here |
| `web/src/lib/api.js` | The single place that knows where the backend is |
| `web/src/components/RunTrace.jsx` | The run trace: timing tower / daemon stack |

## Deployment Shape

The current deployment direction is local-first:

```mermaid
flowchart LR
    Browser["Browser"] --> Pages["Cloudflare Pages<br/>Vite build of web/"]
    Pages --> Access["Cloudflare Access"]
    Access --> Tunnel["Cloudflare Tunnel"]
    Tunnel --> FastAPI["Local FastAPI<br/>localhost:8000"]
    FastAPI --> Agent["Local Agent Runtime"]
```

See `DEPLOYMENT.md` for the Cloudflare Pages, Tunnel, Access, and CORS setup.
