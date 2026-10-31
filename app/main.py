"""Hormuz Pulse web service.

  GET  /                static dashboard
  GET  /healthz         liveness
  GET  /api/history     Brent daily history (FRED), refreshed from FRED at most every 12 hours
  GET  /api/forecast    data-driven 48-hour range (optionally anchored on a price you supply)
  GET  /api/backtest    walk-forward backtest of that range, computed from the same data
  GET  /api/quality     guardrail self-evaluation on the attack corpus
  GET  /api/evidence    published results of the news / implied-volatility feature experiment
  POST /api/brief       runs the Gemini research agents and returns the written briefing
"""
import asyncio
import logging
import os
import re
import threading
import time
import urllib.error
import uuid
from collections import OrderedDict, defaultdict, deque
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from hormuz import backtest, guardrails, market_data, quality, stat_model, telemetry

logger = logging.getLogger("hormuz.app")
STATIC = Path(__file__).parent / "static"

REFRESH_SECONDS = 12 * 3600
BRIEF_TIMEOUT_SECONDS = int(os.environ.get("BRIEF_TIMEOUT_SECONDS", "240"))
BRIEFS_PER_IP_PER_HOUR = int(os.environ.get("BRIEFS_PER_IP_PER_HOUR", "5"))
BRIEFS_PER_DAY = int(os.environ.get("BRIEFS_PER_DAY", "300"))
CHATS_PER_IP_PER_HOUR = int(os.environ.get("CHATS_PER_IP_PER_HOUR", "60"))


class _Data:
    """Brent history held in memory; refreshed from FRED, falling back to the bundled snapshot."""

    def __init__(self) -> None:
        rows = market_data.load_history()
        self.dates = [d for d, _ in rows]
        self.prices = [p for _, p in rows]
        self.ovx_rows = market_data.load_ovx()
        self._align()
        self.source = "bundled FRED snapshot"
        self.loaded_at = 0.0
        self.backtest: Optional[dict] = None
        self.lock = threading.Lock()

    def _align(self) -> None:
        self.implied, self.implied_now = market_data.align_implied_vol(self.dates, self.ovx_rows)

    def refresh_if_stale(self) -> None:
        if time.time() - self.loaded_at < REFRESH_SECONDS:
            return
        with self.lock:
            if time.time() - self.loaded_at < REFRESH_SECONDS:
                return
            self.loaded_at = time.time()  # also throttles retries after a failure
            try:
                import urllib.request
                with urllib.request.urlopen(market_data.FRED_URL, timeout=15) as resp:
                    rows = market_data.parse(resp.read().decode("utf-8"))
                with urllib.request.urlopen(market_data.OVX_URL, timeout=15) as resp:
                    ovx = market_data.parse_ovx(resp.read().decode("utf-8"))
                if len(rows) >= 5000 and len(ovx) >= 3000:
                    self.dates, self.prices, self.ovx_rows = [d for d, _ in rows], [p for _, p in rows], ovx
                    self._align()
                    self.source = "FRED (live)"
                    self.backtest = None
            except (urllib.error.URLError, OSError, ValueError) as exc:
                logger.warning("FRED refresh failed, using %s: %s", self.source, type(exc).__name__)


data = _Data()


class _Limiter:
    """In-memory sliding-window limits. Per instance: Cloud Run may run several instances."""

    def __init__(self) -> None:
        self.by_ip: dict[str, deque] = defaultdict(deque)
        self.chat_by_ip: dict[str, deque] = defaultdict(deque)
        self.day_start = time.time()
        self.day_count = 0
        self.lock = threading.Lock()

    def allow(self, ip: str, mode: str = "briefing") -> Optional[str]:
        now = time.time()
        with self.lock:
            if mode == "chat":
                chat = self.chat_by_ip[ip]
                while chat and now - chat[0] > 3600:
                    chat.popleft()
                if len(chat) >= CHATS_PER_IP_PER_HOUR:
                    return f"Limit of {CHATS_PER_IP_PER_HOUR} chat messages per hour reached."
                chat.append(now)
                return None
            if now - self.day_start > 86400:
                self.day_start, self.day_count = now, 0
            if self.day_count >= BRIEFS_PER_DAY:
                return "The daily briefing limit has been reached. Try again tomorrow."
            window = self.by_ip[ip]
            while window and now - window[0] > 3600:
                window.popleft()
            if len(window) >= BRIEFS_PER_IP_PER_HOUR:
                return f"Limit of {BRIEFS_PER_IP_PER_HOUR} briefings per hour reached."
            window.append(now)
            self.day_count += 1
            return None


limiter = _Limiter()
app = FastAPI(title="Hormuz Pulse", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def client_ip(request: Request) -> str:
    # Cloud Run appends the connecting client address as the last X-Forwarded-For entry.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/history")
def history(years: int = 3) -> dict:
    data.refresh_if_stale()
    years = max(1, min(years, 40))
    cutoff = int(data.dates[-1][:4]) - years
    keep = [i for i, d in enumerate(data.dates) if int(d[:4]) >= cutoff]
    return {"source": data.source, "series": "FRED DCOILBRENTEU, USD per barrel",
            "dates": [data.dates[i] for i in keep], "prices": [data.prices[i] for i in keep],
            "total_observations": len(data.prices), "first_date": data.dates[0]}


@app.get("/api/forecast")
def forecast(price: Optional[float] = None) -> dict:
    data.refresh_if_stale()
    if price is not None and not (10 <= price <= 500):
        raise HTTPException(422, "price must be between 10 and 500 USD per barrel")
    result = stat_model.forecast_blend(data.prices, data.implied, data.implied_now, anchor=price)
    result.update({"as_of": data.dates[-1], "anchored_on": "your price" if price else "latest FRED close",
                   "horizon": "2 trading days", "source": data.source})
    return result


@app.get("/api/backtest")
def get_backtest() -> dict:
    data.refresh_if_stale()
    with data.lock:
        if data.backtest is None:
            data.backtest = backtest.run(data.dates, data.prices, data.implied, start_year=2015)
        return data.backtest


@app.get("/api/quality")
def quality_report() -> dict:
    """Live self-check of the guardrails against the bundled attack corpus."""
    return quality.guardrail_report()


@app.get("/api/evidence")
def evidence() -> dict:
    """Result of scripts/experiment.py: do news and market-implied features improve the forecast?"""
    path = Path(market_data.DATA_DIR) / "experiment_results.json"
    if not path.exists():
        raise HTTPException(404, "experiment results not generated")
    import json
    return json.loads(path.read_text())


class BriefRequest(BaseModel):
    question: str = Field(default="Predict Brent for the next 48 hours.", max_length=guardrails.MAX_INPUT_CHARS)
    session_id: Optional[str] = Field(default=None, max_length=64)


_runners: dict = {}
_runner_lock = threading.Lock()
_session_service = None
_sessions: "OrderedDict[str, float]" = OrderedDict()
_SESSION_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "200"))
SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", "3600"))
MAX_TURNS_PER_SESSION = int(os.environ.get("MAX_TURNS_PER_SESSION", "30"))
_BRIEFING_INTENT = re.compile(
    r"\b(predict|forecast|briefing|brief me|research|analy[sz]e|what is driving|what's driving|"
    r"latest news|what.?s happening|how risky|next 48|fresh|update)\b", re.I)


def route(question: str) -> str:
    """Deterministic router: full research pipeline only when the user asks for fresh analysis."""
    return "briefing" if _BRIEFING_INTENT.search(question) else "chat"


def _get_runner(mode: str):
    global _session_service
    with _runner_lock:
        if _session_service is None:
            from google.adk.sessions import InMemorySessionService
            _session_service = InMemorySessionService()
        if mode not in _runners:
            from google.adk.runners import Runner
            from hormuz.agent import chat_agent, root_agent
            _runners[mode] = Runner(agent=chat_agent if mode == "chat" else root_agent,
                                    app_name="hormuz", session_service=_session_service)
        return _runners[mode], _session_service


async def _ensure_session(service, session_id: Optional[str]) -> str:
    """Return a live session id, creating one if the client's id is unknown, expired or invalid."""
    now = time.time()
    for sid, seen in list(_sessions.items()):
        if now - seen > SESSION_TTL_SECONDS or len(_sessions) > MAX_SESSIONS:
            _sessions.pop(sid, None)
            await service.delete_session(app_name="hormuz", user_id="web", session_id=sid)
    if session_id and _SESSION_ID.match(session_id) and session_id in _sessions:
        _sessions[session_id] = now
        _sessions.move_to_end(session_id)
        return session_id
    sid = session_id if session_id and _SESSION_ID.match(session_id) else uuid.uuid4().hex
    await service.create_session(app_name="hormuz", user_id="web", session_id=sid)
    _sessions[sid] = now
    return sid


async def _run_agents(question: str, trace: telemetry.Trace, session_id: Optional[str] = None) -> tuple:
    """Run chat or the research pipeline inside the visitor's session. Returns (text, session_id)."""
    from google.genai import types
    from hormuz.agent import collect
    mode = trace.mode = route(question)
    runner, service = _get_runner(mode)
    sid = await _ensure_session(service, session_id)
    session = await service.get_session(app_name="hormuz", user_id="web", session_id=sid)
    turns = sum(1 for e in session.events if e.author == "user")
    if turns >= MAX_TURNS_PER_SESSION:
        raise HTTPException(429, "This conversation is full. Start a new chat to continue.")
    parallel = frozenset(a.name for a in collect.sub_agents) if mode == "briefing" else frozenset()
    message = types.Content(role="user", parts=[types.Part(text=question)])
    final = ""
    async for event in runner.run_async(user_id="web", session_id=sid, new_message=message):
        trace.observe(event, parallel)
        if event.is_final_response() and event.content and event.content.parts:
            text = "".join(p.text for p in event.content.parts if p.text and not p.thought)
            if text:
                final = text
    return final, sid


@app.post("/api/brief")
async def brief(body: BriefRequest, request: Request) -> JSONResponse:
    question = body.question.strip()
    trace = telemetry.begin(len(question))
    reason = guardrails.check_input(question)
    if reason:
        telemetry.finish(trace, "blocked")
        return JSONResponse({"blocked": True, "briefing": f"Request blocked: {reason}.",
                             "trace_id": trace.id}, status_code=200)
    limited = limiter.allow(client_ip(request), route(question))
    if limited:
        telemetry.count("rate_limited")
        telemetry.finish(trace, "rate_limited")
        raise HTTPException(429, limited)
    started = time.time()
    try:
        text, session_id = await asyncio.wait_for(
            _run_agents(question, trace, body.session_id), timeout=BRIEF_TIMEOUT_SECONDS)
    except HTTPException:
        telemetry.finish(trace, "rate_limited")
        raise
    except asyncio.TimeoutError:
        logger.error("brief %s timed out", trace.id)
        telemetry.finish(trace, "timeout")
        raise HTTPException(504, "The research agents took too long. Please try again.")
    except Exception as exc:
        logger.error("brief %s failed: %s", trace.id, type(exc).__name__)
        telemetry.finish(trace, "error")
        raise HTTPException(502, f"The research agents are unavailable (reference {trace.id}).")
    trace.checks = telemetry.check_answer(text, trace.tool_range)
    telemetry.finish(trace, "ok")
    logger.info("brief %s ok in %.1fs", trace.id, time.time() - started)
    return JSONResponse({"blocked": False, "briefing": text or "No briefing was produced.",
                         "trace_id": trace.id, "checks": trace.checks,
                         "session_id": session_id, "mode": trace.mode})


@app.get("/api/ops")
def ops_summary() -> dict:
    """Operational metrics for the /ops dashboard (metadata only, per instance)."""
    return telemetry.summary()


@app.get("/api/traces")
def ops_traces(limit: int = 30) -> list:
    return telemetry.recent(max(1, min(limit, 100)))


@app.get("/ops", include_in_schema=False)
def ops_page() -> FileResponse:
    return FileResponse(STATIC / "ops.html")
