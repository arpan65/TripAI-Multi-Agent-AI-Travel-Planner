"""Single-role agentic loop — one API conversation until stop_reason != tool_use."""
import logging
import time
import uuid
from typing import Optional

from anthropic import AsyncAnthropic

from app import db
from .config import MODEL, MAX_TOKENS, ROLE_TOOL_SERVERS
from .executor import ToolExecutor
from .prompts import AGENT_PROMPTS

logger = logging.getLogger(__name__)


class AgentRunner:
    def __init__(self, client: AsyncAnthropic, executor: ToolExecutor):
        self._client = client
        self._executor = executor

    async def run(
        self,
        role: str,
        message: str,
        max_turns: int,
        run_id: str,
        agent_call_id: str,
    ) -> str:
        model = MODEL[role]
        max_tokens = MAX_TOKENS[role]
        system_prompt = AGENT_PROMPTS[role]

        system = [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
        history = [{"role": "user", "content": message}]

        available = self._executor.filter_tools(role, ROLE_TOOL_SERVERS)
        claude_tools = [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": {
                    "type": "object",
                    "properties": t.get("inputSchema", {}).get("properties", {}),
                    "required": t.get("inputSchema", {}).get("required", []),
                },
            }
            for t in available
        ]

        db.create_agent_call(agent_call_id, run_id, role, model)
        call_start = time.monotonic()
        total_input = total_output = total_cache_read = total_cache_write = tool_count = 0
        final_text = ""

        for turn in range(max_turns):
            kwargs: dict = dict(model=model, system=system, max_tokens=max_tokens, messages=history)
            if claude_tools:
                kwargs["tools"] = claude_tools

            response = await self._client.messages.create(**kwargs)

            usage = getattr(response, "usage", None)
            if usage:
                total_input       += getattr(usage, "input_tokens", 0)
                total_output      += getattr(usage, "output_tokens", 0)
                total_cache_read  += getattr(usage, "cache_read_input_tokens", 0)
                total_cache_write += getattr(usage, "cache_creation_input_tokens", 0)

            history.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                final_text = "\n".join(
                    b.text for b in response.content if hasattr(b, "text")
                ).strip()
                break

            turn_calls = [b for b in response.content if b.type == "tool_use"]
            tool_count += len(turn_calls)
            results = []
            for tc in turn_calls:
                res = await self._executor.execute(tc, role, agent_call_id, run_id)
                results.append({"type": "tool_result", "tool_use_id": tc.id, "content": res})
            history.append({"role": "user", "content": results})
        else:
            final_text = "Agent reached turn limit."

        duration_ms = int((time.monotonic() - call_start) * 1000)
        db.complete_agent_call(
            agent_call_id, duration_ms,
            total_input, total_output, total_cache_read, total_cache_write,
            tool_count, final_text,
        )
        logger.info("[%s] done: %d chars, %d input tokens, %d output tokens",
                    role, len(final_text), total_input, total_output)
        return final_text
