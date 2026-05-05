"""Four-phase travel planning pipeline: planner → pricer → budget → aggregator."""
import asyncio
import json
import logging
import re
import time
import uuid
from contextlib import AsyncExitStack
from datetime import datetime
from typing import Callable, Optional

from anthropic import AsyncAnthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app import db
from .config import MAX_TURNS, build_mcp_server_configs
from .executor import ToolExecutor
from .runner import AgentRunner

logger = logging.getLogger(__name__)


class TravelAgent:
    def __init__(self, api_key: str):
        self._client = AsyncAnthropic(api_key=api_key)
        self._stack: Optional[AsyncExitStack] = None
        self._executor: Optional[ToolExecutor] = None
        self._runner: Optional[AgentRunner] = None

    async def connect(self) -> None:
        if self._runner:
            return
        self._stack = AsyncExitStack()
        tools: list[dict] = []
        session_map: dict = {}

        for name, params in build_mcp_server_configs().items():
            try:
                read, write = await self._stack.enter_async_context(stdio_client(params))
                session = await self._stack.enter_async_context(ClientSession(read, write))
                await asyncio.wait_for(session.initialize(), timeout=45)
                session_map[name] = session
                resp = await session.list_tools()
                for t in resp.tools:
                    tools.append({
                        "name": t.name,
                        "description": t.description,
                        "inputSchema": t.inputSchema,
                        "server_name": name,
                    })
                logger.info("Connected MCP: %s (%d tools)", name, len(resp.tools))
            except Exception as exc:
                logger.error("MCP connection failed for %s: %s", name, exc)

        self._executor = ToolExecutor(tools, session_map)
        self._runner = AgentRunner(self._client, self._executor)

    async def disconnect(self) -> None:
        if self._stack:
            await self._stack.aclose()
            self._stack = None
            self._executor = None
            self._runner = None

    async def run_agent(
        self,
        user_input: str,
        progress_callback: Optional[Callable] = None,
        session_id: Optional[str] = None,
        transport_mode: str = "any",
    ) -> str:
        if not self._runner:
            await self.connect()

        run_id = str(uuid.uuid4())
        run_start = time.monotonic()

        if transport_mode and transport_mode.lower() != "any":
            user_input = f"Preferred transport: {transport_mode}. {user_input}"

        db.create_run(run_id, user_input, session_id)

        async def _progress(msg: str) -> None:
            if progress_callback:
                await progress_callback(msg)

        now = datetime.now().strftime("%B %d, %Y")

        try:
            # Phase 1: Plan
            logger.info("=== PHASE 1: PLANNING ===")
            await _progress("Planning your route...")
            plan_raw = await self._runner.run(
                "planner",
                f"Today is {now}.\n\nUser travel request:\n{user_input}",
                max_turns=MAX_TURNS["planner"],
                run_id=run_id,
                agent_call_id=str(uuid.uuid4()),
            )
            manifest = _parse_json(plan_raw)
            if not manifest or "trip" not in manifest:
                db.fail_run(run_id, "Planning failed", int((time.monotonic() - run_start) * 1000))
                return json.dumps({"error": "Planning failed — could not parse trip manifest."})

            trip = manifest["trip"]
            nights = _calculate_nights(trip.get("depart_date"), trip.get("return_date"))
            ctx = _build_trip_context(trip, nights)
            operators = manifest.get("transport_operators", [])
            t_urls, h_urls, a_urls = _flatten_booking_urls(manifest)

            # Phase 2: Price (live Google search)
            logger.info("=== PHASE 2: PRICING ===")
            await _progress("Scouting live prices on Google...")
            depart_fmt = _fmt_search_date(trip.get("depart_date", ""))
            return_fmt = _fmt_search_date(trip.get("return_date", ""))
            date_range_fmt = _fmt_date_range(
                trip.get("depart_date", ""), trip.get("return_date", "")
            )
            price_data = await self._runner.run(
                "pricer",
                f"{ctx}\n\n"
                f"Expected operators: {', '.join(operators)}\n"
                f"Formatted dates for search queries:\n"
                f"  Outbound:   {depart_fmt}\n"
                f"  Return:     {return_fmt}\n"
                f"  Date range: {date_range_fmt}\n"
                f"  Travellers: {trip.get('travellers', 1)}\n\n"
                f"Search Google for live prices. Use the formatted dates above exactly as shown "
                f"in your search queries.",
                max_turns=MAX_TURNS["pricer"],
                run_id=run_id,
                agent_call_id=str(uuid.uuid4()),
            )

            # Phase 3: Budget
            logger.info("=== PHASE 3: BUDGET ===")
            await _progress("Calculating budget tiers...")
            budget = await self._runner.run(
                "budget",
                f"{ctx}\n\nPRICE DATA:\n{price_data[:4000]}\n\n"
                f"Calculate all three budget tiers using calculator tools for all arithmetic.",
                max_turns=MAX_TURNS["budget"],
                run_id=run_id,
                agent_call_id=str(uuid.uuid4()),
            )

            # Phase 4: Aggregate
            logger.info("=== PHASE 4: AGGREGATION ===")
            await _progress("📋 Finalising your travel dossier...")
            t_url_lines = "\n".join(
                f"  {u.get('label','')}: {u.get('url','')}"
                for u in manifest.get("booking_urls", {}).get("transport", [])
            )
            h_url_lines = "\n".join(
                f"  {u.get('label','')}: {u.get('url','')}"
                for u in manifest.get("booking_urls", {}).get("accommodation", [])
            )
            raw_final = await self._runner.run(
                "aggregator",
                f"{ctx}\n\n"
                f"--- TRANSPORT BOOKING URLS ---\n{t_url_lines}\n\n"
                f"--- ACCOMMODATION BOOKING URLS ---\n{h_url_lines}\n\n"
                f"--- PRICE DATA ---\n{price_data[:4000]}\n\n"
                f"--- BUDGET ---\n{budget[:3000]}\n\n"
                f"Output the complete JSON travel dossier now.",
                max_turns=MAX_TURNS["aggregator"],
                run_id=run_id,
                agent_call_id=str(uuid.uuid4()),
            )

            try:
                parsed = json.loads(_clean_json(raw_final))
                result_str = json.dumps(parsed)
                db.complete_run(run_id, result_str, int((time.monotonic() - run_start) * 1000))
                return result_str
            except json.JSONDecodeError as exc:
                logger.error("Aggregator JSON parse error: %s", exc)
                err = json.dumps({"error": str(exc), "raw": raw_final[:500]})
                db.fail_run(run_id, str(exc), int((time.monotonic() - run_start) * 1000))
                return err

        except Exception as exc:
            db.fail_run(run_id, str(exc), int((time.monotonic() - run_start) * 1000))
            raise


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_json(raw: str) -> dict:
    clean = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        return {}


def _clean_json(raw: str) -> str:
    return re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()


def _calculate_nights(depart: Optional[str], ret: Optional[str]) -> int:
    if not depart or not ret:
        return 0
    try:
        from datetime import datetime as _dt
        return max((_dt.strptime(ret, "%Y-%m-%d") - _dt.strptime(depart, "%Y-%m-%d")).days, 0)
    except ValueError:
        return 0


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


def _fmt_search_date(date_str: str) -> str:
    """Convert YYYY-MM-DD to '15th May 2026' for Google search queries."""
    try:
        from datetime import datetime as _dt
        dt = _dt.strptime(date_str, "%Y-%m-%d")
        day = dt.day
        suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        return f"{day}{suffix} {dt.strftime('%B %Y')}"
    except (ValueError, AttributeError):
        return date_str


def _fmt_date_range(depart: str, ret: str) -> str:
    """Convert two YYYY-MM-DD dates to '15th to 18th May 2026' format."""
    try:
        from datetime import datetime as _dt
        d = _dt.strptime(depart, "%Y-%m-%d")
        r = _dt.strptime(ret, "%Y-%m-%d")
        def _ord(n: int) -> str:
            suf = "th" if 11 <= n <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
            return f"{n}{suf}"
        if d.month == r.month and d.year == r.year:
            return f"{_ord(d.day)} to {_ord(r.day)} {d.strftime('%B %Y')}"
        return f"{_ord(d.day)} {d.strftime('%B')} to {_ord(r.day)} {r.strftime('%B %Y')}"
    except (ValueError, AttributeError):
        return f"{depart} to {ret}"


def _flatten_booking_urls(manifest: dict) -> tuple[list[dict], list[dict], list[dict]]:
    bu = manifest.get("booking_urls", {})
    return (
        [i for i in bu.get("transport", []) if isinstance(i, dict) and i.get("url", "").startswith("http")],
        [i for i in bu.get("accommodation", []) if isinstance(i, dict) and i.get("url", "").startswith("http")],
        [i for i in bu.get("activities", []) if isinstance(i, dict) and i.get("url", "").startswith("http")],
    )
