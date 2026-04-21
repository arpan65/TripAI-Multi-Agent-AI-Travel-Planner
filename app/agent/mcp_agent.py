import asyncio
import os
from contextlib import AsyncExitStack
from typing import Any
from anthropic import Anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# --- SERVER CONFIGURATIONS ---
SERVER_CONFIGS = {
    "duckduckgo": StdioServerParameters(
        command="uvx",
        args=["duckduckgo-mcp-server"],
        env=None
    ),
    "playwright": StdioServerParameters(
        command="npx",
        args=["-y", "@playwright/mcp@latest"], # -y avoids the "Need to install?" prompt
        env={
            **os.environ, 
            "PLAYWRIGHT_HEADLESS": "false", # FORCES browser window to show
            "DEBUG": "pw:api"               # Shows browser logs in your terminal
        }
    )
}

class MCPAgent:
    def __init__(self, api_key: str, max_history: int = 20):
        self.client = Anthropic(api_key=api_key)
        self.history = []
        self.max_history = max_history
        self.stack = None
        self.sessions = []
        self.tools = []

    async def connect(self):
        """Connect to all configured MCP servers."""
        if self.sessions: return
            
        self.stack = AsyncExitStack()
        self.sessions = []
        self.tools = []

        for name, params in SERVER_CONFIGS.items():
            print(f"[*] Connecting to {name}...")
            # Using try-except to catch connection hangs early
            try:
                read, write = await self.stack.enter_async_context(stdio_client(params))
                session = await self.stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                
                mcp_tools_resp = await session.list_tools()
                for t in mcp_tools_resp.tools:
                    self.tools.append({
                        "name": t.name,
                        "description": t.description,
                        "input_schema": t.inputSchema,
                        "server_name": name
                    })
                self.sessions.append(session)
                print(f"[✅] {name} connected.")
            except Exception as e:
                print(f"[❌] Failed to connect to {name}: {e}")

    async def run_agent(self, user_input: str):
        if not self.sessions: await self.connect()

        self.history.append({"role": "user", "content": user_input})

        while True:
            # Strip metadata for Claude
            claude_tools = [{k: v for k, v in t.items() if k != 'server_name'} for t in self.tools]
            
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001", # Updated for better tool reasoning
                max_tokens=2000,
                messages=self.history,
                tools=claude_tools
            )

            self.history.append({"role": "assistant", "content": response.content})
            tool_calls = [b for b in response.content if b.type == "tool_use"]
            
            if not tool_calls:
                return next((b.text for b in reversed(response.content) if b.type == "text"), "No response")

            for tool_call in tool_calls:
                tool_def = next((t for t in self.tools if t['name'] == tool_call.name), None)
                if not tool_def: continue

                # Map back to the correct server session
                session_idx = list(SERVER_CONFIGS.keys()).index(tool_def['server_name'])
                session = self.sessions[session_idx]

                print(f"[*] {tool_def['server_name']} action: {tool_call.name}")
                try:
                    result = await session.call_tool(tool_call.name, tool_call.input)
                    tool_output = self._format_tool_result(result)
                except Exception as e:
                    # Keep the conversation alive even when a tool fails.
                    tool_output = f"Tool execution failed: {e}"

                self.history.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool_call.id, "content": tool_output}]
                })

    async def disconnect(self):
        if self.stack:
            print("[*] Shutting down MCP servers...")
            try:
                await self.stack.aclose()
            except Exception as e:
                # AnyIO/MCP can raise task-bound cancel-scope errors during uvicorn reload.
                # We log and continue so server reload/shutdown does not fail hard.
                print(f"[!] MCP shutdown warning: {e}")
            self.sessions = []
            self.stack = None

    def _format_tool_result(self, result: Any) -> str:
        content = getattr(result, "content", None)
        if not content:
            return "Tool returned no content."

        chunks = []
        for item in content:
            text = getattr(item, "text", None)
            if text:
                chunks.append(str(text))
                continue

            # Fallback for non-text content blocks.
            chunks.append(str(item))

        return "\n".join(chunks).strip() or "Tool returned empty content."