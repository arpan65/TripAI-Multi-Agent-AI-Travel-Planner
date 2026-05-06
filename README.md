# TripAI — Multi-Agent AI Travel Planner

An agentic travel planning application powered by Claude. Type a travel request ("Toronto to Montreal, 3 nights, 2 people, train") and a 4-phase pipeline of Claude Haiku agents researches live prices, calculates budgets, and returns a structured trip dossier in a React UI.

---

## Screenshots

![Search UI](Screenshots/front.png)
*Landing page — enter your route, dates, travellers, and transport preference*

![Results UI](Screenshots/front1.png)
*Results view — 3-tier budget breakdown (Economy / Mid-Range / Comfort) with transport, accommodation, meals, and activities*

---

## Architecture

```
React (Vite) frontend
  └─► FastAPI backend  (SSE streaming)
        └─► 4-phase agent pipeline
              ├─ Phase 1 — Planner     no tools  →  JSON manifest
              ├─ Phase 2 — Pricer      Playwright MCP  →  live prices (6 Google searches)
              ├─ Phase 3 — Budget      Calculator MCP  →  3-tier budget
              └─ Phase 4 — Aggregator  no tools  →  final JSON dossier
```

**Model:** `claude-haiku-4-5-20251001` for all phases  
**MCP servers:** `@playwright/mcp` (browser) + `calculator-mcp-server` (arithmetic)  
**Persistence:** SQLite locally, DynamoDB in production  
**Deployment:** Docker on EC2 behind CloudFront (AWS CDK)

---

## Project Structure

```
├── app/
│   ├── api.py                  FastAPI app — endpoints, session manager, SSE streaming
│   ├── db.py                   SQLite / DynamoDB persistence layer
│   └── agent/
│       ├── pipeline.py         TravelAgent — 4-phase orchestrator, MCP lifecycle
│       ├── runner.py           AgentRunner — single-role agentic loop
│       ├── executor.py         ToolExecutor — MCP tool dispatch + DB recording
│       ├── prompts.py          System prompts for all 4 roles
│       ├── config.py           Models, token limits, turn limits, MCP server configs
│       └── mcp_agent.py        Shim: MCPAgent = TravelAgent (backward compat)
├── frontend/
│   └── src/
│       ├── App.jsx             Main React component — form, SSE client, state
│       ├── TripResult.jsx      Result display — tabs, tables, budget cards, PDF export
│       └── styles.css          All styles (dark mode via [data-theme="dark"])
├── infra/
│   └── lib/infra-stack.ts      AWS CDK stack — EC2, CloudFront, DynamoDB, IAM
├── Dockerfile                  Multi-stage: Node frontend builder → Python backend
├── docker-compose.yml          Local dev with volume-persisted browser
├── docker-entrypoint.sh        Installs Chromium into volume on first run, starts uvicorn
├── playwright-mcp.config.json  Chromium launch options (anti-bot headers, viewport)
├── deploy.sh                   Build → push to ECR → SSM restart on EC2
└── pyproject.toml              Python deps (uv)
```

---

## Pipeline Details

### Phase 1 — Planner
- **Model:** claude-haiku-4-5-20251001 · **Max turns:** 2 · **No tools**
- Parses the user's natural language request and today's date
- Outputs a strict JSON manifest: `trip`, `transport_operators`, `booking_urls`

### Phase 2 — Pricer
- **Model:** claude-haiku-4-5-20251001 · **Max turns:** 14 · **Tool:** browser (Playwright MCP)
- Executes 6 structured Google searches: transport out, transport alt, return trip, hotels, Airbnb, activities
- Each search uses `browser_navigate` + `browser_evaluate` called as a pair in the same turn
- Browser tool timeout: 45s · Result cap: 5 KB per call

### Phase 3 — Budget
- **Model:** claude-haiku-4-5-20251001 · **Max turns:** 10 · **Tool:** calculator (calculator-mcp-server)
- All arithmetic routed through tool calls — no mental math
- Outputs 3 budget tiers: economy / mid-range / comfort
- Calculator tool timeout: 30s · Result cap: 2 KB per call

### Phase 4 — Aggregator
- **Model:** claude-haiku-4-5-20251001 · **Max turns:** 3 · **No tools**
- Combines all phase outputs into a single validated JSON dossier
- Output validated with `json.loads()` before returning to the client

---

## Local Development

### Prerequisites

- Python 3.11+
- Node.js 20+
- [uv](https://github.com/astral-sh/uv) — `brew install uv` or `pip install uv`
- `ANTHROPIC_API_KEY` in `.env`

### Setup

```bash
# Clone and enter the project
git clone <repo-url>
cd Claude-Agentic-Workflow

# Create .env
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# Install Python deps
uv sync

# Install Chromium for local browser MCP
uv run playwright install chromium

# Install frontend deps
cd frontend && npm install && cd ..
```

### Run

```bash
# Terminal 1 — backend
uv run uvicorn app.api:app --reload --log-level info

# Terminal 2 — frontend
cd frontend && npm run dev
```

- Frontend: http://localhost:5173
- Backend: http://localhost:8000
- The Vite dev server proxies `/api/*` and `/health` → port 8000

### Run with Docker (local)

```bash
docker compose up --build
```

App available at http://localhost:8000 (frontend + API on the same origin).

---

## Environment Variables

| Variable | Required | Notes |
|----------|----------|-------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key — raises `ValueError` on startup if missing |
| `USE_DYNAMODB` | No | Set to `true` to use DynamoDB instead of SQLite |
| `VITE_API_URL` | No | Frontend only — defaults to `""` (relative URLs via Vite proxy) |

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check — returns `{"status": "ok"}` |
| `GET` | `/api/latest-run` | Most recent completed run (for test mode replay) |
| `POST` | `/api/chat` | Synchronous chat — returns full reply when done |
| `POST` | `/api/chat/stream` | SSE streaming — events arrive as phases complete |
| `POST` | `/api/reset` | Clear a session and disconnect its agent |

### POST /api/chat/stream

**Request body:**
```json
{
  "message": "Toronto to Montreal, 3 nights, 2 people, train",
  "session_id": null,
  "test_mode": false,
  "transport_mode": "any"
}
```

`transport_mode` accepts: `any` | `train` | `bus` | `flight` | `ferry`  
`test_mode: true` replays the latest stored run from DB without calling agents.

**SSE event stream:**
```
data: {"type": "session",  "session_id": "..."}
data: {"type": "phase",    "phase": "Planning your route..."}
data: {"type": "phase",    "phase": "Scouting live prices..."}
data: {"type": "phase",    "phase": "Calculating budget tiers..."}
data: {"type": "phase",    "phase": "Finalising your travel dossier..."}
data: {"type": "result",   "reply": "{...full JSON dossier...}"}
data: {"type": "error",    "message": "..."}   ← only on failure
```

---

## Database Schema

SQLite (local) or DynamoDB (production). All writes are observability-only — wrapped in `try/except`, failures are logged and ignored.

```sql
runs          — id, input_message, session_id, status, result_json, duration_ms, created_at
agent_calls   — id, run_id, phase, model, input_tokens, output_tokens, cache_tokens,
                tool_calls_count, duration_ms, status, started_at
tool_calls    — id, run_id, agent_call_id, tool_name, input_json, output_text,
                duration_ms, success, started_at
```

SQLite path: `travel_runs.db` at repo root. WAL mode is enabled — `.db-shm` and `.db-wal` sidecar files are normal.

---

## AWS Deployment

Infrastructure is defined in `infra/lib/infra-stack.ts` (AWS CDK, TypeScript).

### Resources provisioned

| Resource | Details |
|----------|---------|
| EC2 `t2.micro` | Runs the Docker container, Amazon Linux 2023 |
| ECR repository | `tripai` — stores the Docker image |
| CloudFront distribution | HTTPS frontend, 60s read timeout, no caching |
| DynamoDB tables | `tripai-runs`, `tripai-agent-calls`, `tripai-tool-calls` (PAY_PER_REQUEST, RETAIN on destroy) |
| IAM role | EC2 instance profile with SSM + ECR read + DynamoDB read/write |
| Security group | Inbound TCP 22 (SSH) + 8000 (FastAPI) |

### Deploy

```bash
# First-time infra deploy (creates EC2, CloudFront, DynamoDB)
./deploy.sh --infra

# After infra is up, SSH/SSM into the instance and create:
# /home/ec2-user/app/.env  with ANTHROPIC_API_KEY and USE_DYNAMODB=true
# Then start the service:
sudo systemctl start tripai

# Subsequent code deploys (build image → push to ECR → restart container)
./deploy.sh
```

### Destroy

```bash
# Tear down the CDK stack (EC2 + CloudFront — DynamoDB tables are retained)
cd infra && cdk destroy TripAIStack --force

# To also delete DynamoDB tables:
aws dynamodb delete-table --table-name tripai-runs --region us-east-1
aws dynamodb delete-table --table-name tripai-agent-calls --region us-east-1
aws dynamodb delete-table --table-name tripai-tool-calls --region us-east-1
```

### Claude Code slash commands

| Command | Action |
|---------|--------|
| `/deploy` | Build image → push to ECR → restart container on EC2 |
| `/destroy` | Tear down the CDK stack (with confirmation prompt) |
| `/test` | E2E test against the live cloud backend |
| `/test-local` | E2E test against a locally running backend |

---

## Session Management

- Max 10 concurrent sessions (each spawns a browser process)
- Idle sessions evicted after 2 hours (`SESSION_TTL = 7200`)
- Eviction disconnects the MCP server best-effort (errors swallowed — anyio cancel-scope constraint)

---

## Key Design Decisions

**Browser volume:** Chromium (~2 GB) is stored in a Docker volume (`ms-playwright`) that persists across image updates. The entrypoint installs it on first run only, keeping image size small.

**Prompt caching:** All system prompts use `"cache_control": {"type": "ephemeral"}` in the Anthropic API — significantly reduces token costs on repeat pipeline runs.

**SSE keepalive:** The stream generator emits `: keepalive\n\n` every 25s to keep CloudFront (60s read timeout) and proxies from closing the connection during long Playwright searches.

**MCP lifecycle:** `AsyncExitStack` entries must be exited from the same asyncio task they were entered in (anyio constraint). `_safe_disconnect()` in `api.py` wraps disconnects that happen outside the original task and swallows the cancel-scope exception.

**Tool pairing:** The pricer prompt requires `browser_navigate` and `browser_evaluate` to always be called together in the same response turn — navigate sets up the page, evaluate extracts the data.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| AI | Anthropic Claude (`claude-haiku-4-5-20251001`) |
| Agent framework | Anthropic MCP SDK + custom pipeline |
| Backend | Python 3.11, FastAPI, uvicorn |
| Frontend | React 18, Vite 5 |
| Browser automation | Playwright MCP (`@playwright/mcp@0.0.73`) |
| Calculator | `calculator-mcp-server` (via `uvx`) |
| Package management | uv (Python), npm (Node) |
| Database | SQLite (local), DynamoDB (production) |
| Containerization | Docker (multi-stage build), Docker Compose |
| Infrastructure | AWS CDK (TypeScript), EC2, CloudFront, ECR |
