"""Tool filtering and execution for agentic turns."""
import asyncio
import json
import logging
import time
from typing import Optional

from app import db

logger = logging.getLogger(__name__)


class ToolExecutor:
    def __init__(self, tools: list[dict], session_map: dict):
        self._tools = tools
        self._session_map = session_map

    def filter_tools(self, role: str, role_servers: dict[str, list[str]]) -> list[dict]:
        allowed = role_servers.get(role, [])
        return [t for t in self._tools if t["server_name"] in allowed]

    async def execute(
        self,
        tool_call,
        role: str,
        agent_call_id: str,
        run_id: str,
    ) -> str:
        tool_def = next((t for t in self._tools if t["name"] == tool_call.name), None)
        if not tool_def:
            return f"TOOL_NOT_FOUND: {tool_call.name}"

        session = self._session_map[tool_def["server_name"]]
        start = time.monotonic()
        success = True
        result_text = ""
        is_browser = tool_call.name.startswith("browser_")
        timeout = 45 if is_browser else 30
        result_cap = 5_000 if is_browser else 2_000
        try:
            result = await asyncio.wait_for(
                session.call_tool(tool_call.name, tool_call.input),
                timeout=timeout,
            )
            content = getattr(result, "content", [])
            text = "\n".join(c.text for c in content if hasattr(c, "text")).strip()
            result_text = text[:result_cap] if text else "EMPTY_RESULT"
            logger.info("[%s] ← %s: %d chars", role, tool_call.name, len(result_text))
            return result_text
        except asyncio.TimeoutError:
            success = False
            result_text = f"TOOL_TIMEOUT: {tool_call.name} after 30s"
            return result_text
        except Exception as exc:
            success = False
            result_text = f"TOOL_ERROR: {exc}"
            return result_text
        finally:
            duration_ms = int((time.monotonic() - start) * 1000)
            db.record_tool_call(
                agent_call_id, run_id, tool_call.name,
                json.dumps(tool_call.input) if tool_call.input else "",
                result_text[:5_000], duration_ms, success,
            )
