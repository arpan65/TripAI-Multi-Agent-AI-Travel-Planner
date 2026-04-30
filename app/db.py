import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "travel_runs.db"
logger = logging.getLogger("db")

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS runs (
    id                TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    input_message     TEXT NOT NULL,
    session_id        TEXT,
    status            TEXT NOT NULL DEFAULT 'running',
    total_duration_ms INTEGER,
    result_json       TEXT
);

CREATE TABLE IF NOT EXISTS agent_calls (
    id                 TEXT PRIMARY KEY,
    run_id             TEXT NOT NULL REFERENCES runs(id),
    phase              TEXT NOT NULL,
    model              TEXT NOT NULL,
    started_at         TEXT NOT NULL,
    duration_ms        INTEGER,
    input_tokens       INTEGER DEFAULT 0,
    output_tokens      INTEGER DEFAULT 0,
    cache_read_tokens  INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    tool_calls_count   INTEGER DEFAULT 0,
    output_text        TEXT,
    status             TEXT NOT NULL DEFAULT 'running',
    error              TEXT
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id            TEXT PRIMARY KEY,
    agent_call_id TEXT NOT NULL REFERENCES agent_calls(id),
    run_id        TEXT NOT NULL,
    tool_name     TEXT NOT NULL,
    input_json    TEXT,
    output_text   TEXT,
    duration_ms   INTEGER,
    success       INTEGER NOT NULL DEFAULT 1
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    try:
        with _connect() as conn:
            conn.executescript(_SCHEMA)
        logger.info("DB initialised at %s", DB_PATH)
    except Exception:
        logger.warning("DB init failed — tracing disabled", exc_info=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Run lifecycle ──────────────────────────────────────────────────────────────

def create_run(run_id: str, input_message: str, session_id: Optional[str]) -> None:
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO runs (id, created_at, input_message, session_id) VALUES (?,?,?,?)",
                (run_id, _now(), input_message, session_id),
            )
    except Exception:
        logger.warning("create_run failed", exc_info=True)


def complete_run(run_id: str, result_json: str, duration_ms: int) -> None:
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE runs SET status='complete', result_json=?, total_duration_ms=? WHERE id=?",
                (result_json, duration_ms, run_id),
            )
    except Exception:
        logger.warning("complete_run failed", exc_info=True)


def fail_run(run_id: str, error: str, duration_ms: int) -> None:
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE runs SET status='error', result_json=?, total_duration_ms=? WHERE id=?",
                (json.dumps({"error": error}), duration_ms, run_id),
            )
    except Exception:
        logger.warning("fail_run failed", exc_info=True)


def get_latest_run() -> Optional[dict]:
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT input_message, result_json FROM runs "
                "WHERE status='complete' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if row:
            return {"input_message": row["input_message"], "result_json": row["result_json"]}
    except Exception:
        logger.warning("get_latest_run failed", exc_info=True)
    return None


# ── Agent call lifecycle ───────────────────────────────────────────────────────

def create_agent_call(agent_call_id: str, run_id: str, phase: str, model: str) -> None:
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO agent_calls (id, run_id, phase, model, started_at) VALUES (?,?,?,?,?)",
                (agent_call_id, run_id, phase, model, _now()),
            )
    except Exception:
        logger.warning("create_agent_call failed", exc_info=True)


def complete_agent_call(
    agent_call_id: str,
    duration_ms: int,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    tool_calls_count: int,
    output_text: str,
) -> None:
    try:
        with _connect() as conn:
            conn.execute(
                """UPDATE agent_calls SET
                    status='complete', duration_ms=?,
                    input_tokens=?, output_tokens=?,
                    cache_read_tokens=?, cache_write_tokens=?,
                    tool_calls_count=?, output_text=?
                   WHERE id=?""",
                (
                    duration_ms, input_tokens, output_tokens,
                    cache_read_tokens, cache_write_tokens,
                    tool_calls_count, output_text[:10_000],
                    agent_call_id,
                ),
            )
    except Exception:
        logger.warning("complete_agent_call failed", exc_info=True)


def fail_agent_call(agent_call_id: str, error: str, duration_ms: int) -> None:
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE agent_calls SET status='error', error=?, duration_ms=? WHERE id=?",
                (error, duration_ms, agent_call_id),
            )
    except Exception:
        logger.warning("fail_agent_call failed", exc_info=True)


# ── Tool call recording ────────────────────────────────────────────────────────

def record_tool_call(
    agent_call_id: str,
    run_id: str,
    tool_name: str,
    input_json: str,
    output_text: str,
    duration_ms: int,
    success: bool,
) -> None:
    try:
        with _connect() as conn:
            conn.execute(
                """INSERT INTO tool_calls
                   (id, agent_call_id, run_id, tool_name, input_json, output_text, duration_ms, success)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()), agent_call_id, run_id,
                    tool_name, input_json, output_text[:5_000],
                    duration_ms, 1 if success else 0,
                ),
            )
    except Exception:
        logger.warning("record_tool_call failed", exc_info=True)
