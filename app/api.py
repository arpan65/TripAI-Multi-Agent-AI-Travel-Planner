import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import db as _db
from app.agent.mcp_agent import MCPAgent

load_dotenv()

_api_key = os.getenv("ANTHROPIC_API_KEY")
if not _api_key:
    raise ValueError("Missing ANTHROPIC_API_KEY in environment")

MAX_SESSIONS = 10        # max concurrent searches (each spawns a browser process)
SESSION_TTL  = 7_200    # seconds — evict idle sessions after 2 hours


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=500)
    session_id: str | None = None
    test_mode: bool = False
    transport_mode: str = "any"


class ChatResponse(BaseModel):
    session_id: str
    reply: str


class ResetRequest(BaseModel):
    session_id: str


@dataclass
class AgentState:
    agent: MCPAgent
    last_used: float = field(default_factory=time.time)


_agents: dict[str, AgentState] = {}


def _evict_stale() -> None:
    cutoff = time.time() - SESSION_TTL
    stale = [sid for sid, s in _agents.items() if s.last_used < cutoff]
    for sid in stale:
        state = _agents.pop(sid)
        asyncio.create_task(state.agent.disconnect())


def _get_or_create_state(session_id: str | None) -> tuple[str, AgentState]:
    _evict_stale()

    if session_id and session_id in _agents:
        _agents[session_id].last_used = time.time()
        return session_id, _agents[session_id]

    if len(_agents) >= MAX_SESSIONS:
        raise HTTPException(
            status_code=503,
            detail="Server busy — max concurrent searches reached. Try again shortly.",
        )

    new_session_id = session_id or str(uuid.uuid4())
    state = AgentState(agent=MCPAgent(api_key=_api_key))
    _agents[new_session_id] = state
    return new_session_id, state


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _db.init_db()
    yield
    for state in list(_agents.values()):
        await state.agent.disconnect()
    _agents.clear()


app = FastAPI(title="Claude MCP Backend", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/latest-run")
async def latest_run() -> dict:
    """Return the most recently completed run for test mode pre-population."""
    row = _db.get_latest_run()
    if not row:
        raise HTTPException(status_code=404, detail="No completed runs found")
    return row


@app.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    session_id, state = _get_or_create_state(payload.session_id)
    reply = await state.agent.run_agent(
        message,
        session_id=session_id,
        transport_mode=payload.transport_mode,
    )
    return ChatResponse(session_id=session_id, reply=reply)


async def _stream_test_mode(payload: ChatRequest) -> StreamingResponse:
    """Return stored result as SSE without calling the agent."""
    row = _db.get_latest_run()

    async def generate():
        sid = payload.session_id or str(uuid.uuid4())
        yield f"data: {json.dumps({'type': 'session', 'session_id': sid})}\n\n"
        if not row:
            yield f"data: {json.dumps({'type': 'error', 'message': 'No stored run found. Complete a real search first.'})}\n\n"
            return
        fake_phases = [
            "🗺️ Planning your route...",
            "🔍 Scouting prices and travel options...",
            "📊 Calculating budget tiers...",
            "📋 Finalising your travel dossier...",
        ]
        for phase_msg in fake_phases:
            yield f"data: {json.dumps({'type': 'phase', 'phase': phase_msg})}\n\n"
            await asyncio.sleep(0.4)
        yield f"data: {json.dumps({'type': 'result', 'reply': row['result_json']})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.post("/api/chat/stream")
async def chat_stream(payload: ChatRequest) -> StreamingResponse:
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    if payload.test_mode:
        return await _stream_test_mode(payload)

    session_id, state = _get_or_create_state(payload.session_id)
    queue: asyncio.Queue = asyncio.Queue()

    async def progress_cb(msg: str) -> None:
        await queue.put({"type": "phase", "phase": msg})

    async def agent_task() -> None:
        try:
            result = await state.agent.run_agent(
                message,
                progress_callback=progress_cb,
                session_id=session_id,
                transport_mode=payload.transport_mode,
            )
            await queue.put({"type": "result", "reply": result})
        except Exception as exc:
            await queue.put({"type": "error", "message": str(exc)})
        finally:
            await queue.put(None)  # sentinel — signals end of stream

    async def generate():
        yield f"data: {json.dumps({'type': 'session', 'session_id': session_id})}\n\n"
        task = asyncio.create_task(agent_task())
        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"
        # Re-raise any task exception so FastAPI can log it
        if not task.cancelled():
            task.result()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/reset")
async def reset(payload: ResetRequest) -> dict[str, str]:
    state = _agents.pop(payload.session_id, None)
    if state:
        await state.agent.disconnect()
    return {"status": "cleared"}


