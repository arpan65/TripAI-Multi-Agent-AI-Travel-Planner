# /test-local — E2E Test Against Local Backend

Start the backend and frontend locally, run a full pipeline test, then stop everything.

## Steps

### 1. Kill any existing processes on ports 8000 and 5173
```bash
lsof -ti:8000 | xargs kill -9 2>/dev/null; lsof -ti:5173 | xargs kill -9 2>/dev/null; echo "ports cleared"
```

### 2. Start backend
```bash
cd /Users/arpan/Downloads/Projects/Claude-Agentic-Workflow
uv run uvicorn app.api:app --port 8000 --log-level info > /tmp/tripai-backend.log 2>&1 &
echo "backend PID: $!"
sleep 3
curl -s http://localhost:8000/health
```
Must return `{"status":"ok"}` before continuing.

### 3. Start frontend
```bash
cd /Users/arpan/Downloads/Projects/Claude-Agentic-Workflow/frontend
npm run dev > /tmp/tripai-frontend.log 2>&1 &
echo "frontend PID: $!"
sleep 3
curl -s http://localhost:5173/health | head -1
```

### 4. Run the pipeline test
```bash
curl -s -X POST http://localhost:8000/api/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"message":"Toronto to Montreal, 3 nights, 2 people, train, May 15-18 2026","test_mode":false}' \
  --no-buffer --max-time 300
```

### 5. Evaluate result
Parse the SSE stream and report:
- Did all 4 phase events arrive?
- Did a `{"type":"result",...}` event arrive with valid JSON?
- Is `data_notes.fetch_failed` empty `[]`? (confirms local browser MCP worked)
- Transport prices found?
- Budget tier totals (economy/mid_range/comfort)?

Report PASS or FAIL with details.

### 6. Show log tail on failure
```bash
tail -50 /tmp/tripai-backend.log
```

### 7. Stop servers
```bash
lsof -ti:8000 | xargs kill -9 2>/dev/null
lsof -ti:5173 | xargs kill -9 2>/dev/null
echo "servers stopped"
```
