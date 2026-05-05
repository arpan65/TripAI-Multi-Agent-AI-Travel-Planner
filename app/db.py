import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

logger = logging.getLogger("db")

_dynamodb = None

RUNS_TABLE        = "tripai-runs"
AGENT_CALLS_TABLE = "tripai-agent-calls"
TOOL_CALLS_TABLE  = "tripai-tool-calls"


def _ddb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    return _dynamodb


def init_db() -> None:
    try:
        _ddb().Table(RUNS_TABLE).load()
        logger.info("DynamoDB tables verified: %s, %s, %s", RUNS_TABLE, AGENT_CALLS_TABLE, TOOL_CALLS_TABLE)
    except ClientError:
        logger.warning("DynamoDB table check failed — tracing may be disabled", exc_info=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Run lifecycle ──────────────────────────────────────────────────────────────

def create_run(run_id: str, input_message: str, session_id: Optional[str]) -> None:
    try:
        _ddb().Table(RUNS_TABLE).put_item(Item={
            "id": run_id,
            "created_at": _now(),
            "input_message": input_message,
            "session_id": session_id or "",
            "status": "running",
        })
    except Exception:
        logger.warning("create_run failed", exc_info=True)


def complete_run(run_id: str, result_json: str, duration_ms: int) -> None:
    try:
        _ddb().Table(RUNS_TABLE).update_item(
            Key={"id": run_id},
            UpdateExpression="SET #s = :s, result_json = :r, total_duration_ms = :d",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "complete", ":r": result_json, ":d": duration_ms},
        )
    except Exception:
        logger.warning("complete_run failed", exc_info=True)


def fail_run(run_id: str, error: str, duration_ms: int) -> None:
    try:
        _ddb().Table(RUNS_TABLE).update_item(
            Key={"id": run_id},
            UpdateExpression="SET #s = :s, result_json = :r, total_duration_ms = :d",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "error",
                ":r": json.dumps({"error": error}),
                ":d": duration_ms,
            },
        )
    except Exception:
        logger.warning("fail_run failed", exc_info=True)


def get_latest_run() -> Optional[dict]:
    try:
        resp = _ddb().Table(RUNS_TABLE).query(
            IndexName="status-created-index",
            KeyConditionExpression=Key("status").eq("complete"),
            ScanIndexForward=False,
            Limit=1,
        )
        items = resp.get("Items", [])
        if items:
            return {"input_message": items[0]["input_message"], "result_json": items[0]["result_json"]}
    except Exception:
        logger.warning("get_latest_run failed", exc_info=True)
    return None


# ── Agent call lifecycle ───────────────────────────────────────────────────────

def create_agent_call(agent_call_id: str, run_id: str, phase: str, model: str) -> None:
    try:
        _ddb().Table(AGENT_CALLS_TABLE).put_item(Item={
            "id": agent_call_id,
            "run_id": run_id,
            "phase": phase,
            "model": model,
            "started_at": _now(),
            "status": "running",
        })
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
        _ddb().Table(AGENT_CALLS_TABLE).update_item(
            Key={"id": agent_call_id},
            UpdateExpression=(
                "SET #s = :s, duration_ms = :d, input_tokens = :it, output_tokens = :ot, "
                "cache_read_tokens = :cr, cache_write_tokens = :cw, "
                "tool_calls_count = :tc, output_text = :txt"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "complete",
                ":d": duration_ms,
                ":it": input_tokens,
                ":ot": output_tokens,
                ":cr": cache_read_tokens,
                ":cw": cache_write_tokens,
                ":tc": tool_calls_count,
                ":txt": output_text[:10_000],
            },
        )
    except Exception:
        logger.warning("complete_agent_call failed", exc_info=True)


def fail_agent_call(agent_call_id: str, error: str, duration_ms: int) -> None:
    try:
        _ddb().Table(AGENT_CALLS_TABLE).update_item(
            Key={"id": agent_call_id},
            UpdateExpression="SET #s = :s, error = :e, duration_ms = :d",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "error", ":e": error, ":d": duration_ms},
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
        _ddb().Table(TOOL_CALLS_TABLE).put_item(Item={
            "id": str(uuid.uuid4()),
            "agent_call_id": agent_call_id,
            "run_id": run_id,
            "tool_name": tool_name,
            "input_json": input_json,
            "output_text": output_text[:5_000],
            "duration_ms": duration_ms,
            "success": 1 if success else 0,
        })
    except Exception:
        logger.warning("record_tool_call failed", exc_info=True)
