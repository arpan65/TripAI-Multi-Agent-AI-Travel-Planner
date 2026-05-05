"""Runtime constants and MCP server configuration."""
import logging
import os
import pathlib
from typing import Optional
from mcp import StdioServerParameters

_REPO_ROOT = pathlib.Path(__file__).parent.parent.parent

logger = logging.getLogger(__name__)

MODEL: dict[str, str] = {
    "planner":    "claude-haiku-4-5-20251001",
    "pricer":     "claude-haiku-4-5-20251001",
    "budget":     "claude-haiku-4-5-20251001",
    "aggregator": "claude-haiku-4-5-20251001",
}

MAX_TOKENS: dict[str, int] = {
    "planner":    1024,
    "pricer":     3000,
    "budget":     2048,
    "aggregator": 3072,
}

MAX_TURNS: dict[str, int] = {
    "planner":    2,
    "pricer":     14,
    "budget":     10,
    "aggregator": 3,
}

ROLE_TOOL_SERVERS: dict[str, list[str]] = {
    "planner":    [],
    "pricer":     ["browser"],
    "budget":     ["financial_quant"],
    "aggregator": [],
}


def build_mcp_server_configs() -> dict[str, StdioServerParameters]:
    playwright_config = str(_REPO_ROOT / "playwright-mcp.config.json")
    base_env = {
        **os.environ,
        "PLAYWRIGHT_BROWSERS_PATH": os.path.expanduser("~/.cache/ms-playwright"),
    }
    return {
        "financial_quant": StdioServerParameters(
            command="uvx",
            args=["calculator-mcp-server"],
            env=base_env,
        ),
        "browser": StdioServerParameters(
            command="playwright-mcp",
            args=["--browser", "chromium", "--config", playwright_config, "--isolated"],
            env=base_env,
        ),
    }
