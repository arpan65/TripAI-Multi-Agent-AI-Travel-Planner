## Claude MCP - React + Python Architecture

This project is now split into:

- `app/api.py` -> FastAPI backend that wraps the existing `MCPAgent` logic
- `frontend/` -> modern React (Vite) chat interface

The backend preserves the same operation as the previous Streamlit app:
- keeps conversation context per session
- calls `MCPAgent.run_agent()` for every prompt
- supports clearing chat history

## 1) Backend setup

1. Ensure your environment has `ANTHROPIC_API_KEY`.
2. Install Python dependencies (if not already installed):

```bash
uv sync
```

3. Start the API:

```bash
uv run uvicorn app.api:app --reload --port 8000
```

Backend routes:
- `GET /health`
- `POST /api/chat`
- `POST /api/reset`

## 2) Frontend setup

From `frontend/`:

```bash
npm install
npm run dev
```

The frontend runs on `http://localhost:5173` and proxies API calls to `http://127.0.0.1:8000`.

If you want to use a direct API URL instead of proxy, create:

`frontend/.env`:

```bash
VITE_API_URL=http://127.0.0.1:8000
```
