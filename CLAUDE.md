# CLAUDE.md — Project Context for Claude Code

## What This Project Is

A multi-agent AI travel planning app. The user types a travel request ("Toronto to Montreal, 3 nights, 2 people, train"); a 4-phase pipeline of Claude Haiku agents researches live prices, calculates budgets, and returns a structured JSON dossier displayed in a React UI.

---

## Architecture at a Glance

```
React (Vite) frontend
  └─► FastAPI backend (SSE streaming)
        └─► 4-phase agent pipeline
              ├─ Phase 1 — Planner    (no tools → JSON manifest)
              ├─ Phase 2 — Pricer     (Playwright MCP → 6 Google searches)
              ├─ Phase 3 — Budget     (Calculator MCP → 3-tier budget)
              └─ Phase 4 — Aggregator (no tools → final JSON)
```

**Backend**: `uvicorn app.api:app --reload` on port 8000  
**Frontend**: `npm run dev` (inside `frontend/`) on port 5173  
**Dev proxy**: Vite proxies `/api/*` and `/health` → `localhost:8000`

---

## File Map

| Path | Purpose |
|------|---------|
| `app/api.py` | FastAPI app — endpoints, session manager, SSE streaming |
| `app/db.py` | SQLite observability layer (runs, agent_calls, tool_calls) |
| `app/agent/pipeline.py` | `TravelAgent` — 4-phase orchestrator, MCP lifecycle |
| `app/agent/runner.py` | `AgentRunner` — single-role agentic loop |
| `app/agent/executor.py` | `ToolExecutor` — MCP tool dispatch + DB recording |
| `app/agent/prompts.py` | System prompts for all 4 roles |
| `app/agent/config.py` | Models, token limits, turn limits, MCP server configs |
| `app/agent/mcp_agent.py` | Thin shim: `MCPAgent = TravelAgent` (backward compat) |
| `frontend/src/App.jsx` | Main React component — form, SSE client, state |
| `frontend/src/TripResult.jsx` | Result display — tabs, tables, budget cards, PDF export |
| `frontend/src/styles.css` | All styles — dark mode via `[data-theme="dark"]` |
| `playwright-mcp.config.json` | Chromium launch options for the browser MCP server |
| `pyproject.toml` | Python deps (uv/pip) |
| `frontend/package.json` | JS deps (React 18, Vite 5) |

---

## Pipeline Deep Dive

### Phase 1 — Planner (`claude-haiku-4-5-20251001`, max 2 turns, no tools)
- Input: user message + today's date
- Output: strict JSON manifest with `trip`, `transport_operators`, `booking_urls`
- Failure path: if JSON parse fails or `trip` key missing → `db.fail_run()` + return error JSON

### Phase 2 — Pricer (`claude-haiku-4-5-20251001`, max 14 turns, browser tools)
- Tools: `browser_navigate` + `browser_evaluate` (always called as a pair)
- 6 structured Google search steps (transport out, transport alt, return, hotels, Airbnb, activities)
- MCP server: `npx @playwright/mcp@latest --config playwright-mcp.config.json --isolated`
- Browser tool timeout: 45s; result cap: 5KB

### Phase 3 — Budget (`claude-haiku-4-5-20251001`, max 10 turns, calculator tools)
- MCP server: `uvx calculator-mcp-server`
- All arithmetic must go through tool calls (never mental math)
- Calculator tool timeout: 30s; result cap: 2KB

### Phase 4 — Aggregator (`claude-haiku-4-5-20251001`, max 3 turns, no tools)
- Combines all phase outputs into a single JSON dossier
- Output is validated with `json.loads()` before returning

---

## Key Invariants

**MCP lifecycle (anyio constraint):** `AsyncExitStack` entries must be exited from the same asyncio task they were entered in. Never call `agent.disconnect()` via `asyncio.create_task()` — use `_safe_disconnect()` wrapper in `api.py` which catches the cancel-scope exception.

**Session eviction:** `_evict_stale()` in `api.py` uses `asyncio.create_task(_safe_disconnect(...))`. The disconnect is best-effort; errors are swallowed.

**Tool pairing:** The pricer prompt requires `browser_navigate` and `browser_evaluate` to always be called together in the same response turn.

**Price ranges:** Budget prompt explicitly forbids using the floor of a range (e.g., `$10–110`) as the economy price. Use ~25th percentile realistic value instead.

**Prompt caching:** All system prompts use `"cache_control": {"type": "ephemeral"}` in the messages API. Don't remove this — it significantly reduces token costs on repeat calls.

---

## Database Schema (SQLite WAL mode)

```sql
runs           — one row per pipeline run (id, input_message, session_id, status, result_json, duration_ms)
agent_calls    — one row per phase per run (phase, model, tokens, cache_tokens, tool_calls_count)
tool_calls     — one row per MCP tool call (tool_name, input_json, output_text, duration_ms, success)
```

DB path: `travel_runs.db` at repo root (next to `pyproject.toml`).  
The DB is observability-only. All writes are wrapped in try/except — failures are logged and silently ignored.

---

## SSE Streaming Protocol

Frontend connects to `POST /api/chat/stream`. Events (newline-delimited SSE):

```
data: {"type": "session",  "session_id": "..."}
data: {"type": "phase",    "phase": "Planning your route..."}
data: {"type": "phase",    "phase": "Scouting live prices..."}
data: {"type": "phase",    "phase": "Calculating budget tiers..."}
data: {"type": "phase",    "phase": "Finalising your travel dossier..."}
data: {"type": "result",   "reply": "{...full JSON dossier...}"}
data: {"type": "error",    "message": "..."}   ← only on failure
```

Test mode (`test_mode: true`) replays the latest completed run from SQLite without calling agents.

---

## Environment Variables

| Variable | Where used | Notes |
|----------|-----------|-------|
| `ANTHROPIC_API_KEY` | `app/api.py` on startup | Required; raises ValueError if missing |
| `VITE_API_URL` | `frontend/src/App.jsx` | Optional; defaults to `""` (relative URLs via Vite proxy) |

Load via `.env` in repo root (python-dotenv).

---

## How to Run Locally

```bash
# Backend
uv run uvicorn app.api:app --reload --log-level info

# Frontend (separate terminal)
cd frontend && npm run dev
```

The frontend dev server (port 5173) proxies `/api/*` → port 8000.

---

## Coding Conventions

- **Python**: 3.11+, type hints everywhere, `async/await` throughout, `logging` module (never `print`)
- **Logging**: Use `_banner()`, `_phase()`, `_ok()`, `_warn()`, `_err()` helpers in `pipeline.py` for structured phase output. Per-turn logs in `runner.py` include role, turn#, tool names, elapsed time.
- **No comments by default** — code should be self-explanatory. Add a comment only when the WHY is non-obvious (e.g., the anyio cancel-scope constraint above).
- **React**: Functional components, hooks only. `useCallback` for stable callbacks passed to child components.
- **CSS**: Dark mode via `[data-theme="dark"]` attribute on `<html>`. CSS custom properties (`--surface`, `--border`, etc.) for theming. Never hardcode colours outside of the `[data-theme]` blocks.
- **No new dependencies** without good reason — the stack (fastapi, anthropic, mcp, playwright) is intentionally lean.

---

## Common Gotchas

1. **Playwright not installed**: Run `uv run playwright install chromium` before first run.
2. **`uvx` not found**: Install with `pip install uv` or `brew install uv`. Calculator MCP needs it.
3. **`npx` slow first run**: `@playwright/mcp@latest` downloads on first use — subsequent runs are fast.
4. **Backend logging**: Pass `--log-level info` to uvicorn; the pipeline banners only appear at INFO level.
5. **Test mode pre-population**: `/api/latest-run` returns 404 until at least one real search completes and is stored in SQLite.
6. **SQLite WAL files**: `travel_runs.db-shm` and `travel_runs.db-wal` are normal WAL mode artifacts — don't delete them while the server is running.

---

## Planned / In Progress

- AWS deployment (CDK TypeScript) — not yet started
- Target: App Runner (backend) + S3 + CloudFront (frontend) + DynamoDB (replace SQLite)
