"""Hormuz Pulse web service.

  GET  /                static dashboard
  GET  /healthz         liveness
  GET  /api/history     Brent daily history (FRED), refreshed from FRED at most every 12 hours
  GET  /api/forecast    data-driven 48-hour range (optionally anchored on a price you supply)
  GET  /api/backtest    walk-forward backtest of that range, computed from the same data
  POST /api/brief       runs the Gemini research agents and returns the written briefing
"""
import asyncio
import logging
import os
import threading
import time
import urllib.error
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from hormuz import backtest, guardrails, market_data, stat_model

logger = logging.getLogger("hormuz.app")
STATIC = Path(__file__).parent / "static"

REFRESH_SECONDS = 12 * 3600
BRIEF_TIMEOUT_SECONDS = int(os.environ.get("BRIEF_TIMEOUT_SECONDS", "240"))
BRIEFS_PER_IP_PER_HOUR = int(os.environ.get("BRIEFS_PER_IP_PER_HOUR", "5"))
BRIEFS_PER_DAY = int(os.environ.get("BRIEFS_PER_DAY", "300"))


class _Data:
    """Brent history held in memory; refreshed from FRED, falling back to the bundled snapshot."""

    def __init__(self) -> None:
        rows = market_data.load_history()
        self.dates = [d for d, _ in rows]
        self.prices = [p for _, p in rows]
        self.source = "bundled FRED snapshot"
        self.loaded_at = 0.0
        self.backtest: Optional[dict] = None
        self.lock = threading.Lock()

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
                if len(rows) >= 5000:
                    self.dates, self.prices = [d for d, _ in rows], [p for _, p in rows]
                    self.source = "FRED (live)"
                    self.backtest = None
            except (urllib.error.URLError, OSError, ValueError) as exc:
                logger.warning("FRED refresh failed, using %s: %s", self.source, type(exc).__name__)


data = _Data()


class _Limiter:
    """In-memory sliding-window limits. Per instance: Cloud Run may run several instances."""

    def __init__(self) -> None:
        self.by_ip: dict[str, deque] = defaultdict(deque)
        self.day_start = time.time()
        self.day_count = 0
        self.lock = threading.Lock()

    def allow(self, ip: str) -> Optional[str]:
        now = time.time()
        with self.lock:
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
    result = stat_model.forecast(data.prices, anchor=price)
    result.update({"as_of": data.dates[-1], "anchored_on": "your price" if price else "latest FRED close",
                   "horizon": "2 trading days", "source": data.source})
    return result


@app.get("/api/backtest")
def get_backtest() -> dict:
    data.refresh_if_stale()
    with data.lock:
        if data.backtest is None:
            data.backtest = backtest.run(data.dates, data.prices, 2015)
        return data.backtest


class BriefRequest(BaseModel):
    question: str = Field(default="Predict Brent for the next 48 hours.", max_length=guardrails.MAX_INPUT_CHARS)


_runner = None
_runner_lock = threading.Lock()


def _get_runner():
    global _runner
    with _runner_lock:
        if _runner is None:
            from google.adk.runners import InMemoryRunner
            from hormuz.agent import root_agent
            _runner = InMemoryRunner(agent=root_agent, app_name="hormuz")
        return _runner


async def _run_agents(question: str) -> str:
    from google.genai import types
    runner = _get_runner()
    session = await runner.session_service.create_session(app_name="hormuz", user_id="web")
    message = types.Content(role="user", parts=[types.Part(text=question)])
    final = ""
    async for event in runner.run_async(user_id="web", session_id=session.id, new_message=message):
        if event.is_final_response() and event.content and event.content.parts:
            text = "".join(p.text for p in event.content.parts if p.text and not p.thought)
            if text:
                final = text
    await runner.session_service.delete_session(app_name="hormuz", user_id="web", session_id=session.id)
    return final


@app.post("/api/brief")
async def brief(body: BriefRequest, request: Request) -> JSONResponse:
    question = body.question.strip()
    reason = guardrails.check_input(question)
    if reason:
        return JSONResponse({"blocked": True, "briefing": f"Request blocked: {reason}."}, status_code=200)
    limited = limiter.allow(client_ip(request))
    if limited:
        raise HTTPException(429, limited)
    request_id = uuid.uuid4().hex[:8]
    started = time.time()
    try:
        text = await asyncio.wait_for(_run_agents(question), timeout=BRIEF_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.error("brief %s timed out", request_id)
        raise HTTPException(504, "The research agents took too long. Please try again.")
    except Exception as exc:
        logger.error("brief %s failed: %s", request_id, type(exc).__name__)
        raise HTTPException(502, f"The research agents are unavailable (reference {request_id}).")
    logger.info("brief %s ok in %.1fs", request_id, time.time() - started)
    return JSONResponse({"blocked": False, "briefing": text or "No briefing was produced."})
