"""
Multi-Agent Travel Planner
==========================

Architecture
------------
4 agents, 2 MCP servers:

  planner     → no tools  — reads user request, emits JSON manifest with real booking URLs
  scout       → playwright (MCP) — single-pass: navigates booking pages AND extracts prices
  budget      → calculator MCP — verified arithmetic only, no mental math
  aggregator  → no tools  — assembles final markdown dossier

MCP Servers
-----------
  browser         npx @playwright/mcp@latest   (headless Chromium, no API key needed)
  financial_quant uvx calculator-mcp-server    (no API key needed)

Cost optimisations applied
--------------------------
  1. Prompt caching (cache_control="ephemeral") on all system prompts — saves ~80% on
     repeated system-prompt tokens after the first turn.
  2. Researcher + Pricer merged into a single "scout" agent — halves Playwright overhead
     and eliminates one full Sonnet multi-turn session.
  3. max_turns per role: planner 2, scout 20, budget 12, aggregator 3.
  4. Tool result cap: 8 000 chars (was 12 000).
  5. Context passed to budget/aggregator truncated to 2 000 chars (was 3 000/4 000).
  6. Aggregator downgraded to Haiku — it only formats structured markdown, no tool use.
  7. max_tokens set per-role: Sonnet roles 4 096, Haiku roles 2 048.

Setup
-----
  pip install anthropic mcp playwright
  python -m playwright install chromium   # or set PLAYWRIGHT_BROWSERS_PATH
  export ANTHROPIC_API_KEY="sk-ant-..."
  python travel_agent.py "bus from Toronto to Montreal May 15-18 2026 for 2 people"
"""

import asyncio
import json
import logging
import os
import re
import sys
from contextlib import AsyncExitStack
from datetime import datetime
from typing import Callable, Optional

from anthropic import AsyncAnthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# ── Playwright Python fallback ──────────────────────────────────────────── #
try:
    from playwright.async_api import async_playwright, Browser, BrowserContext
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# ── Logging ─────────────────────────────────────────────────────────────── #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("MCPAgent")


# ================================================================== #
#  PLAYWRIGHT BROWSER PATH DETECTION                                   #
# ================================================================== #
def detect_playwright_browsers_path() -> Optional[str]:
    """
    Find where Playwright's Chromium binary lives.
    Checks env var first, then common install locations.
    Returns the path to pass as PLAYWRIGHT_BROWSERS_PATH, or None.
    """
    # Explicit env override always wins
    env_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env_path and os.path.isdir(env_path):
        logger.info(f"Playwright browsers path from env: {env_path}")
        return env_path

    # Common locations to probe
    candidates = [
        "/opt/pw-browsers",                                    # system-level install
        os.path.expanduser("~/.cache/ms-playwright"),          # default pip install
        "/ms-playwright",                                       # Docker images
        "/root/.cache/ms-playwright",                          # root installs
    ]
    for path in candidates:
        if os.path.isdir(path):
            # Make sure there's actually a chromium build in there
            for item in os.listdir(path):
                if "chromium" in item.lower():
                    logger.info(f"Found Playwright browsers at: {path}")
                    return path

    logger.warning(
        "Could not auto-detect PLAYWRIGHT_BROWSERS_PATH. "
        "Set it explicitly if Playwright fails to connect. "
        "e.g. export PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers"
    )
    return None


# ================================================================== #
#  MCP SERVER CONFIGS                                                  #
# ================================================================== #
def build_server_configs(pw_browsers_path: Optional[str]) -> dict:
    """Build MCP server configs at runtime so env is always current."""
    env = dict(os.environ)
    if pw_browsers_path:
        env["PLAYWRIGHT_BROWSERS_PATH"] = pw_browsers_path

    configs = {
        "browser": StdioServerParameters(
            command="npx",
            args=["-y", "@playwright/mcp@latest"],
            env={**env, "PLAYWRIGHT_HEADLESS": "true"},
        ),
        "financial_quant": StdioServerParameters(
            command="uvx",
            args=["calculator-mcp-server"],
        ),
    }
    return configs


# ================================================================== #
#  AGENT SYSTEM PROMPTS                                                #
# ================================================================== #
AGENT_SYSTEM_PROMPTS = {

# ── PLANNER ─────────────────────────────────────────────────────────
"planner": """You are the PLANNER agent.

Your ONLY job: read the user's travel request, reason about it, then emit ONE valid JSON manifest.
You have NO tools. Output raw JSON only — no markdown fences, no explanation.

Required JSON structure:
{
  "trip": {
    "origin": "<city, country>",
    "destination": "<city, country>",
    "depart_date": "<YYYY-MM-DD>",
    "return_date": "<YYYY-MM-DD or null if one-way>",
    "travellers": <integer>,
    "preferred_transport": "<bus|train|flight|ferry|any>",
    "currency": "<ISO 4217: CA→CAD, US→USD, GB→GBP, EU→EUR, AU→AUD, IN→INR, JP→JPY>",
    "budget_tier_preference": "<economy|mid-range|comfort|all>"
  },
  "transport_operators": ["<operator name>"],
  "booking_urls": {
    "transport": [
      {
        "label": "<operator name>",
        "url": "<complete working URL — must deep-link to route/date search if possible>"
      }
    ],
    "accommodation": [
      {
        "label": "<site name>",
        "url": "<complete working URL with destination + checkin/checkout params>"
      }
    ],
    "activities": [
      {
        "label": "<site or attraction name>",
        "url": "<URL>"
      }
    ]
  }
}

URL CONSTRUCTION RULES — these must be real, navigable URLs:

Bus (Canada/US):
  Busbud:   https://www.busbud.com/en-ca/bus/{origin-slug}/{dest-slug}/yyyy-mm-dd?pax=N
            e.g. https://www.busbud.com/en-ca/bus/toronto/montreal/2026-05-15?pax=2
  Wanderu:  https://www.wanderu.com/en/depart/{Origin-City,-ST}/{Dest-City,-ST}/yyyy-mm-dd/
            e.g. https://www.wanderu.com/en/depart/Toronto,-ON/Montr%C3%A9al,-QC/2026-05-15/
  Megabus:  https://ca.megabus.com/  (homepage — can't deep-link dates)
  FlixBus:  https://www.flixbus.ca/bus/{origin}-{destination}
            e.g. https://www.flixbus.ca/bus/toronto-montreal

Bus (Europe):
  FlixBus:  https://www.flixbus.com/bus/{origin}-{destination}
  Busbud:   https://www.busbud.com/en/bus/{origin}/{destination}/yyyy-mm-dd

Train (UK):
  Trainline: https://www.thetrainline.com/train-times/{origin}-to-{destination}
  National Rail: https://www.nationalrail.co.uk/

Train (Europe):
  Omio:     https://www.omio.com/
  Trainline: https://www.thetrainline.com/

Train (Canada):
  VIA Rail: https://www.viarail.ca/en/fares-and-packages/train-fares

Flight:
  Google Flights: https://www.google.com/travel/flights/search?tfs=...
  Skyscanner: https://www.skyscanner.net/transport/flights/{from}/{to}/{depart-yyyymmdd}/{return-yyyymmdd}/
  Kayak: https://www.kayak.com/flights/{IATA1}-{IATA2}/{yyyy-mm-dd}/{yyyy-mm-dd}/{N}adults

Accommodation (always include dates and guest count):
  Booking.com: https://www.booking.com/searchresults/en-gb.html?ss={Destination}&checkin={YYYY-MM-DD}&checkout={YYYY-MM-DD}&group_adults={N}&no_rooms=1
  Hostelworld: https://www.hostelworld.com/findabed.php/ChosenCity.{Destination}/ChosenCountry.{Country}/DateFrom.{dd-Mon-yyyy}/DateTo.{dd-Mon-yyyy}/guests.{N}
  Airbnb: https://www.airbnb.com/s/{Destination}/homes?checkin={YYYY-MM-DD}&checkout={YYYY-MM-DD}&adults={N}

Activities:
  TripAdvisor: https://www.tripadvisor.com/{Destination}-Attractions
  Viator: https://www.viator.com/en-CA/{Destination}/d{id}-ttd (use city name)
  Timeout: https://www.timeout.com/{destination-slug}/things-to-do

Provide 3-4 transport URLs, 3 accommodation URLs, 2 activity URLs.
Output ONLY the JSON object.
""",

# ── SCOUT (merged researcher + pricer) ──────────────────────────────
"scout": """You are the SCOUT agent — Travel Data Extractor.

You have Playwright browser tools. Work through the URL list CATEGORY BY CATEGORY.
Stop a category as soon as ONE site in it yields usable prices — do NOT visit remaining URLs for that category.

Tools (use in order per URL):
  browser_navigate → browser_snapshot → interact only if no results visible (one more snapshot) → STOP this category if prices found

For EACH URL:
1. browser_navigate to the URL
2. browser_snapshot — read results immediately
3. Only if a search form is shown with no results: fill dates/locations with browser_type,
   browser_click to submit, then one more browser_snapshot
4. If prices are visible → record them and move to the next CATEGORY (skip remaining URLs in current category)
5. If blocked/CAPTCHA/empty → note it and try next URL in same category

What to record:
- Transport: operator, depart time, arrive time, duration, price/person (lowest + mid + high)
- Hotels: name, stars, neighbourhood, price/night, total for stay
- Activities: name, price
- Any CAPTCHA / block: note and skip

Stop entirely once you have transport prices AND hotel prices (one source each is enough).

Output compact Markdown:

## 🚌 Transport
| Operator | Depart | Arrive | Duration | Price/person | Notes |
|----------|--------|--------|----------|--------------|-------|

## 🏨 Accommodation
| Name | Stars | Area | Price/night | Notes |
|------|-------|------|-------------|-------|

## 🎭 Activities
| Name | Price | Notes |
|------|-------|-------|

## ⚠️ Issues
(CAPTCHAs, blocks, empty results — one line each)
""",

# ── BUDGET ──────────────────────────────────────────────────────────
"budget": """You are the BUDGET agent — Financial Analyst.

STRICT RULES:
- Use calculator tools for ALL arithmetic — never compute mentally, ever
- Show your calculation steps (e.g. "90 × 2 people × 2 trips = ?")
- All output in the trip's stated currency
- Flag every FETCH_FAILED / ESTIMATE / INTERACTIVE_REQUIRED item
- Three tiers (Economy / Mid-Range / Comfort) from real price data
- If fewer than 3 price points exist, reuse values and note it

Required output:

## 💵 Verified Price Inventory
| Item | Price | Source | Confidence |
|------|-------|--------|------------|

## 📊 Budget Calculations

### Economy Tier
**Transport** (cheapest option found):
- Per person one-way: [value]
- × [N] people × [1 or 2] trips = [use calculator]

**Accommodation** ([N] nights, cheapest option):
- Per night: [value]
- × [N] nights = [subtotal]
- + tax ([rate]% if known): [use calculator]

**Economy Total:** [use calculator to sum]

### Mid-Range Tier
(same structure)

### Comfort Tier
(same structure)

## 👤 Per-Person Summary
| Tier | Transport | Accommodation | Total pp |
|------|-----------|---------------|----------|

## ⚠️ Data Gaps
(be specific: which items are estimated/missing/failed)
""",

# ── AGGREGATOR ──────────────────────────────────────────────────────
"aggregator": """You are the AGGREGATOR agent — Travel Data Compiler.

Your ONLY job: combine trip context, scout data, and budget calculations into ONE valid JSON object.
Output raw JSON only — no markdown fences, no code blocks, no explanation, nothing before or after.

Required JSON structure (fill in all values from the data you received):
{
  "trip": {
    "origin": "<city, country>",
    "destination": "<city, country>",
    "depart_date": "<YYYY-MM-DD>",
    "return_date": "<YYYY-MM-DD or null>",
    "nights": <integer>,
    "travellers": <integer>,
    "currency": "<ISO code e.g. CAD>"
  },
  "transport": {
    "mode": "<bus|train|flight|ferry>",
    "emoji": "<one of: bus=🚌 train=🚆 flight=✈️ ferry=⛴️>",
    "outbound": [
      {
        "operator": "<name>",
        "depart": "<HH:MM or N/A>",
        "arrive": "<HH:MM or N/A>",
        "duration": "<e.g. 5h 30m or N/A>",
        "price_per_person": "<e.g. CAD 55 or N/A>",
        "url": null
      }
    ],
    "return_trips": []
  },
  "accommodation": [
    {
      "name": "<property name>",
      "type": "<Hotel|Hostel|Airbnb|Guesthouse>",
      "neighbourhood": "<area or N/A>",
      "stars": <1-5 or null>,
      "price_per_night": "<e.g. CAD 120 or N/A>",
      "total_stay": "<e.g. CAD 360 or N/A>",
      "url": null
    }
  ],
  "budget": {
    "notes": "<one sentence on meal and activity estimate assumptions>",
    "economy": {
      "transport": "<e.g. CAD 110>",
      "accommodation": "<e.g. CAD 300>",
      "meals": "<e.g. CAD 150>",
      "activities": "<e.g. CAD 60>",
      "total": "<e.g. CAD 620>",
      "per_person": "<e.g. CAD 310>"
    },
    "mid_range": {
      "transport": "...", "accommodation": "...", "meals": "...",
      "activities": "...", "total": "...", "per_person": "..."
    },
    "comfort": {
      "transport": "...", "accommodation": "...", "meals": "...",
      "activities": "...", "total": "...", "per_person": "..."
    }
  },
  "itinerary": [
    {
      "day": 1,
      "date": "<e.g. May 15, 2026>",
      "label": "<e.g. Arrival Day>",
      "morning": "<brief activity description or null>",
      "afternoon": "<brief activity description or null>",
      "evening": "<brief activity description or null>"
    }
  ],
  "getting_around": [
    {
      "option": "<e.g. Metro>",
      "cost": "<e.g. CAD 3.50/ride or Free>",
      "notes": "<brief note>"
    }
  ],
  "data_notes": {
    "fetch_failed": ["<item: URL if applicable>"],
    "estimates": ["<what was estimated and the assumption used>"],
    "missing": ["<what data was unavailable>"]
  }
}

Rules:
- Use "N/A" for unknown strings, null for missing optional fields
- Every money amount must include the currency code (e.g. "CAD 415" not "$415")
- Budget must have all 3 tiers (economy, mid_range, comfort) each with all 6 fields
- Itinerary needs one entry per day (nights + 1 entries total, from depart to return)
- Output ONLY the JSON — nothing before or after it
""",
}

# Scout needs Sonnet for multi-step tool use and complex page interaction.
# Planner, budget, and aggregator are structured tasks where Haiku is sufficient.
MODEL_PER_ROLE: dict[str, str] = {
    "planner":    "claude-haiku-4-5-20251001",
    "scout":      "claude-sonnet-4-6",
    "budget":     "claude-haiku-4-5-20251001",
    "aggregator": "claude-haiku-4-5-20251001",
}

# Output token budget per role — Haiku roles capped at 2 048 to reduce cost.
MAX_TOKENS_PER_ROLE: dict[str, int] = {
    "planner":    1024,
    "scout":      4096,
    "budget":     2048,
    "aggregator": 4096,   # markdown output can be long; keep generous cap
}


# ================================================================== #
#  PYTHON PLAYWRIGHT FALLBACK FETCHER                                  #
# ================================================================== #
class PlaywrightFallback:
    """
    Direct async Playwright fetcher used when the MCP browser server
    fails to connect or a URL needs more interaction than MCP provides.
    """

    def __init__(self, browsers_path: Optional[str]):
        self.browsers_path = browsers_path
        self._pw = None
        self._browser: Optional[Browser] = None

    async def start(self):
        if not PLAYWRIGHT_AVAILABLE:
            logger.warning("Python playwright package not installed — fallback disabled.")
            return
        if self._browser:
            return
        env_backup = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        if self.browsers_path:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = self.browsers_path
        try:
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            logger.info(
                f"Python Playwright fallback started (Chromium {self._browser.version})"
            )
        except Exception as e:
            logger.error(f"Playwright fallback failed to start: {e}")
            self._browser = None
        finally:
            if env_backup is not None:
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = env_backup

    async def fetch(self, url: str, wait_ms: int = 3000) -> str:
        """Navigate to URL and return page text content."""
        if not self._browser:
            return f"FETCH_FAILED: Playwright fallback not available."
        ctx: BrowserContext = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )
        page = await ctx.new_page()
        try:
            await page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            await page.wait_for_timeout(wait_ms)
            text = await page.inner_text("body")
            return text[:10_000]  # cap to avoid token overload
        except Exception as e:
            return f"FETCH_FAILED: {e}"
        finally:
            await ctx.close()

    async def stop(self):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()


# ================================================================== #
#  MAIN AGENT CLASS                                                     #
# ================================================================== #
class MCPAgent:
    def __init__(self, anthropic_api_key: str = None, api_key: str = None):
        key = anthropic_api_key or api_key
        self.client = AsyncAnthropic(api_key=key)
        self.stack: Optional[AsyncExitStack] = None
        self.sessions: list = []
        self.session_map: dict = {}
        self.tools: list = []
        self.pw_browsers_path = detect_playwright_browsers_path()
        self.pw_fallback = PlaywrightFallback(self.pw_browsers_path)
        self._mcp_browser_connected = False

    # ── CONNECTION ────────────────────────────────────────────────── #
    async def connect(self):
        if self.sessions:
            return

        self.stack = AsyncExitStack()
        configs = build_server_configs(self.pw_browsers_path)

        for name, params in configs.items():
            try:
                read, write = await self.stack.enter_async_context(
                    stdio_client(params)
                )
                session = await self.stack.enter_async_context(
                    ClientSession(read, write)
                )
                await asyncio.wait_for(session.initialize(), timeout=45)
                self.session_map[name] = session
                self.sessions.append(session)

                tools_resp = await session.list_tools()
                count = len(tools_resp.tools)
                for t in tools_resp.tools:
                    self.tools.append(
                        {
                            "name": t.name,
                            "description": t.description,
                            "inputSchema": t.inputSchema,
                            "server_name": name,
                        }
                    )
                logger.info(f"✅ Connected: {name} ({count} tools)")
                for t in tools_resp.tools:
                    logger.info(f"   └─ {t.name}")

                if name == "browser":
                    self._mcp_browser_connected = True

            except Exception as e:
                logger.error(f"❌ MCP connection failed for {name}: {e}")
                if name == "browser":
                    logger.warning(
                        "Browser MCP unavailable — will use Python Playwright fallback."
                    )

        # Always start the Python playwright fallback regardless of MCP status
        await self.pw_fallback.start()

    # ── TOOL FILTERING ────────────────────────────────────────────── #
    def _filter_tools(self, role: str) -> list:
        allow: dict[str, list[str]] = {
            "planner":    [],
            "scout":      ["browser"],
            "budget":     ["financial_quant"],
            "aggregator": [],
        }
        permitted = allow.get(role, [])
        return [t for t in self.tools if t["server_name"] in permitted]

    # ── TOOL EXECUTION ────────────────────────────────────────────── #
    async def _execute_tool(self, tool_call, agent_role: str) -> str:
        tool_def = next(
            (t for t in self.tools if t["name"] == tool_call.name), None
        )
        if not tool_def:
            logger.warning(f"[{agent_role}] Tool not found: {tool_call.name}")
            return f"TOOL_NOT_FOUND: {tool_call.name}"

        session = self.session_map[tool_def["server_name"]]
        args_log = json.dumps(tool_call.input)[:200]
        logger.info(f"[{agent_role}] → {tool_call.name} | {args_log}")

        try:
            result = await asyncio.wait_for(
                session.call_tool(tool_call.name, tool_call.input),
                timeout=45,
            )
            content = getattr(result, "content", [])
            text = "\n".join(
                c.text for c in content if hasattr(c, "text")
            ).strip()

            # Calculator tools return short numbers (e.g. "110") — don't flag as thin
            is_calculator = tool_def.get("server_name") == "financial_quant"
            if not text:
                logger.warning(f"[{agent_role}] ⚠️  Empty result from {tool_call.name}")
                return "EMPTY_RESULT: 0 chars returned."
            if not is_calculator and len(text) < 20:
                logger.warning(
                    f"[{agent_role}] ⚠️  Thin result ({len(text)} chars) "
                    f"from {tool_call.name}"
                )
                return f"EMPTY_RESULT: {len(text)} chars returned."

            logger.info(f"[{agent_role}] ← {tool_call.name}: {len(text)} chars")
            return text[:8_000]  # cap to avoid token overload

        except asyncio.TimeoutError:
            logger.error(f"[{agent_role}] ⏱️  {tool_call.name} timed out (45s)")
            return "FETCH_FAILED: MCP tool timeout after 45s"
        except Exception as e:
            logger.error(f"[{agent_role}] ❌ {tool_call.name}: {e}")
            return f"FETCH_FAILED: {e}"

    # ── CORE AGENT RUNNER ─────────────────────────────────────────── #
    async def _run_agent(
        self,
        role: str,
        user_message: str,
        max_turns: int = 16,
    ) -> str:
        system_text = AGENT_SYSTEM_PROMPTS[role]
        # Wrap system prompt with cache_control so the first turn writes it to
        # the prompt cache and subsequent turns read it at ~10% of input cost.
        system = [
            {
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        history = [{"role": "user", "content": user_message}]
        available = self._filter_tools(role)

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

        model = MODEL_PER_ROLE.get(role, "claude-sonnet-4-6")
        max_tokens = MAX_TOKENS_PER_ROLE.get(role, 4096)

        for turn in range(max_turns):
            kwargs: dict = dict(
                model=model,
                system=system,
                max_tokens=max_tokens,
                messages=history,
            )
            if claude_tools:
                kwargs["tools"] = claude_tools

            response = await self.client.messages.create(**kwargs)

            # Log cache usage when available (helps validate caching is working)
            usage = getattr(response, "usage", None)
            if usage:
                cache_read = getattr(usage, "cache_read_input_tokens", 0)
                cache_write = getattr(usage, "cache_creation_input_tokens", 0)
                if cache_read or cache_write:
                    logger.info(
                        f"[{role}] turn={turn} cache_write={cache_write} "
                        f"cache_read={cache_read} "
                        f"input={usage.input_tokens} output={usage.output_tokens}"
                    )

            history.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                return "\n".join(
                    b.text for b in response.content if hasattr(b, "text")
                ).strip()

            tool_results = []
            for tc in [b for b in response.content if b.type == "tool_use"]:
                result = await self._execute_tool(tc, role)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": tc.id, "content": result}
                )
            history.append({"role": "user", "content": tool_results})

        return "⚠️ Agent reached turn limit — returning partial data."

    # ── FALLBACK FETCHER: used when MCP browser isn't connected ───── #
    async def _fallback_fetch_urls(
        self, urls: list[str], label: str = "fallback"
    ) -> str:
        """Use Python playwright directly to fetch a list of URLs."""
        if not PLAYWRIGHT_AVAILABLE or not self.pw_fallback._browser:
            return "FETCH_FAILED: Playwright fallback not available."

        results = []
        for url in urls[:6]:
            logger.info(f"[{label}] Fallback fetch: {url}")
            text = await self.pw_fallback.fetch(url)
            results.append(f"### {url}\n{text[:2000]}\n")
            await asyncio.sleep(1.5)  # polite delay between requests

        return "\n".join(results) if results else "No data fetched."

    # ── HELPERS ───────────────────────────────────────────────────── #
    @staticmethod
    def _parse_manifest(raw: str) -> dict:
        clean = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
        try:
            return json.loads(clean)
        except json.JSONDecodeError as e:
            logger.error(f"Manifest parse error: {e}\nRaw:\n{raw[:500]}")
            return {}

    @staticmethod
    def _flatten_booking_urls(manifest: dict) -> tuple[list[str], list[str], list[str]]:
        """Extract transport, accommodation, activity URLs from manifest."""
        bu = manifest.get("booking_urls", {})

        def extract(items: list) -> list[str]:
            urls = []
            for item in items:
                if isinstance(item, dict):
                    urls.append(item.get("url", ""))
                elif isinstance(item, str):
                    urls.append(item)
            return [u for u in urls if u.startswith("http")]

        return (
            extract(bu.get("transport", [])),
            extract(bu.get("accommodation", [])),
            extract(bu.get("activities", [])),
        )

    @staticmethod
    def _calculate_nights(depart: Optional[str], ret: Optional[str]) -> int:
        if not depart or not ret:
            return 0
        try:
            return max(
                (datetime.strptime(ret, "%Y-%m-%d")
                 - datetime.strptime(depart, "%Y-%m-%d")).days,
                0,
            )
        except ValueError:
            return 0

    @staticmethod
    def _build_trip_context(trip: dict, nights: int) -> str:
        return (
            f"TRIP CONTEXT:\n"
            f"  Origin:      {trip.get('origin', 'Unknown')}\n"
            f"  Destination: {trip.get('destination', 'Unknown')}\n"
            f"  Depart:      {trip.get('depart_date', 'Unknown')}\n"
            f"  Return:      {trip.get('return_date', 'one-way')}\n"
            f"  Nights:      {nights}\n"
            f"  Travellers:  {trip.get('travellers', 1)}\n"
            f"  Transport:   {trip.get('preferred_transport', 'any')}\n"
            f"  Currency:    {trip.get('currency', 'local')}\n"
            f"  Budget pref: {trip.get('budget_tier_preference', 'all')}\n"
        )

    # ================================================================ #
    #  PIPELINE                                                          #
    # ================================================================ #
    async def run_agent(
        self,
        user_input: str,
        progress_callback: Optional[Callable] = None,
    ) -> str:
        if not self.sessions:
            await self.connect()

        async def _progress(msg: str) -> None:
            if progress_callback:
                await progress_callback(msg)

        now = datetime.now().strftime("%B %d, %Y")

        # ── PHASE 1: PLANNING ──────────────────────────────────────── #
        self._log_phase("PHASE 1: PLANNING")
        await _progress("🗺️ Planning your route...")
        plan_raw = await self._run_agent(
            "planner",
            f"Today is {now}.\n\nUser travel request:\n{user_input}",
            max_turns=2,
        )
        manifest = self._parse_manifest(plan_raw)
        if not manifest or "trip" not in manifest:
            return (
                "Planning failed — could not parse trip manifest.\n"
                "Please include: origin, destination, dates, number of travellers."
            )

        trip = manifest["trip"]
        nights = self._calculate_nights(trip.get("depart_date"), trip.get("return_date"))
        ctx = self._build_trip_context(trip, nights)
        transport_urls, hotel_urls, activity_urls = self._flatten_booking_urls(manifest)
        operators = manifest.get("transport_operators", [])
        all_urls = transport_urls + hotel_urls + activity_urls

        logger.info(f"Manifest OK:\n{ctx}")
        logger.info(
            f"URLs from manifest: {len(transport_urls)} transport, "
            f"{len(hotel_urls)} hotel, {len(activity_urls)} activity"
        )

        # ── PHASE 2: SCOUT (single-pass research + pricing) ────────── #
        self._log_phase("PHASE 2: SCOUT (research + pricing)")
        await _progress("🔍 Scouting prices and travel options...")

        if self._mcp_browser_connected:
            # Limit to 2 per category — first success stops the category
            t_urls = transport_urls[:2]
            h_urls = hotel_urls[:2]
            a_urls = activity_urls[:1]
            scout_prompt = (
                f"{ctx}\n\n"
                f"Operators to look for: {', '.join(operators)}\n"
                f"Travel dates: {trip.get('depart_date')} → {trip.get('return_date')}, "
                f"{trip.get('travellers', 1)} traveller(s)\n\n"
                f"Work category by category. Within each category, stop at the first URL that yields prices "
                f"— do NOT visit the remaining URLs for that category.\n\n"
                f"TRANSPORT URLS (try in order, stop category on first success):\n"
                + "\n".join(f"  - {u}" for u in t_urls)
                + f"\n\nACCOMMODATION URLS (try in order, stop category on first success):\n"
                + "\n".join(f"  - {u}" for u in h_urls)
                + f"\n\nACTIVITY URLS (try in order, stop category on first success):\n"
                + "\n".join(f"  - {u}" for u in a_urls)
            )
            scout_data = await self._run_agent("scout", scout_prompt, max_turns=20)
        else:
            logger.warning("MCP browser not connected — using Python Playwright fallback.")
            raw_pages = await self._fallback_fetch_urls(all_urls, label="scout-fallback")
            scout_data = (
                f"## Raw Page Content (Python Playwright Fallback)\n\n"
                f"{raw_pages}\n\n"
                f"Note: MCP browser was unavailable. Content extracted directly."
            )

        logger.info(f"Scout complete: {len(scout_data)} chars")

        # ── PHASE 3: BUDGET CALCULATION ────────────────────────────── #
        self._log_phase("PHASE 3: BUDGET CALCULATION")
        await _progress("📊 Calculating budget tiers...")
        budget = await self._run_agent(
            "budget",
            f"{ctx}\n\n"
            f"SCOUT DATA:\n{scout_data[:5000]}\n\n"
            f"Calculate all three budget tiers. Use calculator tools for all arithmetic.",
            max_turns=12,  # 3 tiers × ~3 calculator calls each, allows batching
        )
        logger.info(f"Budget complete: {len(budget)} chars")

        # ── PHASE 4: AGGREGATION ────────────────────────────────────── #
        self._log_phase("PHASE 4: AGGREGATION")
        await _progress("📋 Finalising your travel dossier...")
        raw_final = await self._run_agent(
            "aggregator",
            f"{ctx}\n\n"
            f"--- SCOUT DATA ---\n{scout_data[:5000]}\n\n"
            f"--- BUDGET ---\n{budget}\n\n"
            f"Output the complete JSON travel dossier now.",
            max_turns=3,
        )

        # Parse and normalise the JSON output from the aggregator
        try:
            clean = re.sub(r"```(?:json)?", "", raw_final).strip().strip("`").strip()
            parsed = json.loads(clean)
            return json.dumps(parsed)
        except json.JSONDecodeError as e:
            logger.error(f"Aggregator JSON parse error: {e}\nRaw:\n{raw_final[:500]}")
            return json.dumps({"error": f"Could not parse result: {str(e)}", "raw": raw_final[:500]})

    @staticmethod
    def _log_phase(label: str):
        logger.info("=" * 60)
        logger.info(label)
        logger.info("=" * 60)

    async def disconnect(self):
        await self.pw_fallback.stop()
        if self.stack:
            await self.stack.aclose()
        logger.info("All sessions closed.")


# ================================================================== #
#  ENTRY POINT                                                          #
# ================================================================== #
async def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: Set ANTHROPIC_API_KEY environment variable.")
        sys.exit(1)

    if len(sys.argv) > 1:
        user_input = " ".join(sys.argv[1:])
    else:
        print(
            "\nMulti-Agent Travel Planner\n"
            "==========================\n"
            "Examples:\n"
            "  bus from Toronto to Montreal May 15-18 2026 for 2 people\n"
            "  fly from London to Tokyo April 5-12 2026, 2 travellers, comfort\n"
            "  train from Paris to Amsterdam June 1-5 2026, economy, 1 person\n"
            "  ferry from Seattle to Victoria BC, July 4-7 2026, 2 people\n"
        )
        user_input = input("> ").strip()
        if not user_input:
            print("No input provided.")
            sys.exit(1)

    agent = MCPAgent(anthropic_api_key=api_key)
    try:
        result = await agent.run_agent(user_input)
        print("\n" + "=" * 70)
        print(result)
        print("=" * 70)
    finally:
        await agent.disconnect()


if __name__ == "__main__":
    asyncio.run(main())