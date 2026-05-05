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

# ── Pretty banner helpers ─────────────────────────────────────────────────────

def _banner(title: str) -> None:
    bar = "─" * 60
    logger.info("\n┌%s┐\n│  %-56s  │\n└%s┘", bar, title, bar)

def _phase(emoji: str, name: str) -> None:
    logger.info("  %s  %s", emoji, name)

def _ok(label: str, detail: str = "") -> None:
    logger.info("  ✅  %s%s", label, f"  — {detail}" if detail else "")

def _warn(label: str, detail: str = "") -> None:
    logger.warning("  ⚠️   %s%s", label, f"  — {detail}" if detail else "")

def _err(label: str, detail: str = "") -> None:
    logger.error("  ❌  %s%s", label, f"  — {detail}" if detail else "")

def _preview(text: str, chars: int = 120) -> str:
    text = text.strip()
    return (text[:chars] + "…") if len(text) > chars else text


# ── Agent class ───────────────────────────────────────────────────────────────

class TravelAgent:
    def __init__(self, api_key: str):
        self._client = AsyncAnthropic(api_key=api_key)
        self._stack: Optional[AsyncExitStack] = None
        self._executor: Optional[ToolExecutor] = None
        self._runner: Optional[AgentRunner] = None

    async def connect(self) -> None:
        if self._runner:
            return
        _banner("CONNECTING MCP SERVERS")
        self._stack = AsyncExitStack()
        tools: list[dict] = []
        session_map: dict = {}

        for name, params in build_mcp_server_configs().items():
            t0 = time.monotonic()
            try:
                read, write = await self._stack.enter_async_context(stdio_client(params))
                session = await self._stack.enter_async_context(ClientSession(read, write))
                await asyncio.wait_for(session.initialize(), timeout=45)
                session_map[name] = session
                resp = await session.list_tools()
                n_tools = len(resp.tools)
                for t in resp.tools:
                    tools.append({
                        "name": t.name,
                        "description": t.description,
                        "inputSchema": t.inputSchema,
                        "server_name": name,
                    })
                _ok(f"MCP [{name}]", f"{n_tools} tools  ({time.monotonic()-t0:.1f}s)")
            except Exception as exc:
                _err(f"MCP [{name}] failed to connect", str(exc))

        logger.info("  Total tools loaded: %d", len(tools))
        self._executor = ToolExecutor(tools, session_map)
        self._runner = AgentRunner(self._client, self._executor)

    async def disconnect(self) -> None:
        if self._stack:
            try:
                await self._stack.aclose()
            except Exception as exc:
                # anyio cancel-scope task mismatch on eviction — harmless
                logger.debug("Disconnect cleanup error (non-fatal): %s", exc)
            finally:
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

        _banner(f"NEW RUN  {run_id[:8]}")
        logger.info("  Input   : %s", _preview(user_input))
        logger.info("  Session : %s", session_id or "—")
        logger.info("  Date    : %s", now)

        try:
            # ── Phase 1: Planner ──────────────────────────────────────────────
            _phase("🗺️", "PHASE 1 — PLANNER  (no tools)")
            await _progress("Planning your route...")
            t0 = time.monotonic()

            plan_raw = await self._runner.run(
                "planner",
                f"Today is {now}.\n\nUser travel request:\n{user_input}",
                max_turns=MAX_TURNS["planner"],
                run_id=run_id,
                agent_call_id=str(uuid.uuid4()),
            )
            elapsed1 = time.monotonic() - t0
            logger.info("  Planner raw (%d chars, %.1fs): %s", len(plan_raw), elapsed1, _preview(plan_raw))

            manifest = _parse_json(plan_raw)
            if not manifest or "trip" not in manifest:
                _err("Planner", "JSON parse failed or missing 'trip' key")
                logger.error("  Full planner output: %s", plan_raw[:800])
                db.fail_run(run_id, "Planning failed", int((time.monotonic() - run_start) * 1000))
                return json.dumps({"error": "Planning failed — could not parse trip manifest."})

            trip = manifest["trip"]
            nights = _calculate_nights(trip.get("depart_date"), trip.get("return_date"))
            _ok("Planner done", (
                f"{trip.get('origin')} → {trip.get('destination')}  "
                f"| {trip.get('depart_date')} – {trip.get('return_date')}  "
                f"| {nights} nights  | {trip.get('travellers')} pax  "
                f"| transport: {trip.get('preferred_transport')}  "
                f"| ({elapsed1:.1f}s)"
            ))

            ctx = _build_trip_context(trip, nights)
            operators = manifest.get("transport_operators", [])
            t_urls, h_urls, a_urls = _flatten_booking_urls(manifest)
            logger.info("  Operators: %s", operators)
            logger.info("  Booking URLs — transport: %d, accommodation: %d, activities: %d",
                        len(t_urls), len(h_urls), len(a_urls))

            # ── Phase 2: Pricer ───────────────────────────────────────────────
            _phase("🔍", "PHASE 2 — PRICER  (live Google searches via browser)")
            await _progress("Scouting live prices on Google...")
            t0 = time.monotonic()

            depart_fmt = _fmt_search_date(trip.get("depart_date", ""))
            return_fmt = _fmt_search_date(trip.get("return_date", ""))
            date_range_fmt = _fmt_date_range(
                trip.get("depart_date", ""), trip.get("return_date", "")
            )
            logger.info("  Search dates — outbound: %s  return: %s  range: %s",
                        depart_fmt, return_fmt, date_range_fmt)
            logger.info("  Max turns: %d", MAX_TURNS["pricer"])

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
            elapsed2 = time.monotonic() - t0

            if "Agent reached turn limit" in price_data:
                _warn("Pricer", f"hit turn limit after {elapsed2:.1f}s — no live data")
                price_data = "No live price data available (pricer did not complete)."
            else:
                _ok("Pricer done", f"{len(price_data)} chars  ({elapsed2:.1f}s)")
                logger.info("  Price data preview:\n%s", _preview(price_data, 400))

            # ── Phase 3: Budget ───────────────────────────────────────────────
            _phase("💰", "PHASE 3 — BUDGET  (calculator tools)")
            await _progress("Calculating budget tiers...")
            t0 = time.monotonic()

            budget = await self._runner.run(
                "budget",
                f"{ctx}\n\nPRICE DATA:\n{price_data[:4000]}\n\n"
                f"Calculate all three budget tiers using calculator tools for all arithmetic.",
                max_turns=MAX_TURNS["budget"],
                run_id=run_id,
                agent_call_id=str(uuid.uuid4()),
            )
            elapsed3 = time.monotonic() - t0
            _ok("Budget done", f"{len(budget)} chars  ({elapsed3:.1f}s)")
            logger.info("  Budget preview:\n%s", _preview(budget, 300))

            # ── Phase 4: Aggregator ───────────────────────────────────────────
            _phase("📋", "PHASE 4 — AGGREGATOR  (JSON compiler)")
            await _progress("📋 Finalising your travel dossier...")
            t0 = time.monotonic()

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
            elapsed4 = time.monotonic() - t0
            logger.info("  Aggregator raw (%d chars, %.1fs): %s",
                        len(raw_final), elapsed4, _preview(raw_final, 200))

            try:
                parsed = json.loads(_clean_json(raw_final))
                result_str = json.dumps(parsed)
                total_s = time.monotonic() - run_start
                _ok("Aggregator", "valid JSON produced")
                _banner(
                    f"RUN COMPLETE  {run_id[:8]}  "
                    f"total {total_s:.0f}s  "
                    f"(p1:{elapsed1:.0f}s p2:{elapsed2:.0f}s p3:{elapsed3:.0f}s p4:{elapsed4:.0f}s)"
                )
                db.complete_run(run_id, result_str, int(total_s * 1000))
                return result_str
            except json.JSONDecodeError as exc:
                _err("Aggregator", f"JSON parse failed: {exc}")
                logger.error("  Raw aggregator output (first 1000 chars):\n%s", raw_final[:1000])
                err = json.dumps({"error": str(exc), "raw": raw_final[:500]})
                db.fail_run(run_id, str(exc), int((time.monotonic() - run_start) * 1000))
                return err

        except Exception as exc:
            _err("Pipeline exception", str(exc))
            logger.exception("Unhandled pipeline error for run %s", run_id)
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
    try:
        from datetime import datetime as _dt
        dt = _dt.strptime(date_str, "%Y-%m-%d")
        day = dt.day
        suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        return f"{day}{suffix} {dt.strftime('%B %Y')}"
    except (ValueError, AttributeError):
        return date_str


def _fmt_date_range(depart: str, ret: str) -> str:
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
