"""In-process traces, counters and answer checks for the agent pipeline.

Only metadata is kept: no question text, no IP addresses, no model output. Data lives in
memory per instance and is lost on restart.
"""
import contextvars
import re
import threading
import time
import uuid
from collections import Counter, deque
from typing import Any, Optional

MAX_TRACES = 200
STARTED_AT = time.time()

_lock = threading.Lock()
_traces: deque = deque(maxlen=MAX_TRACES)
_counters: Counter = Counter()
_current: contextvars.ContextVar = contextvars.ContextVar("hormuz_trace", default=None)

_muted: contextvars.ContextVar = contextvars.ContextVar("hormuz_muted", default=False)

_NUMBER = re.compile(r"(?<![\w.])\$?\s?(\d{1,4}(?:,\d{3})*(?:\.\d+)?)")
_DOLLAR = re.compile(r"\$\s?(\d{2,3}(?:\.\d+)?)")


class Trace:
    def __init__(self, question_chars: int) -> None:
        self.id = uuid.uuid4().hex[:8]
        self.t0 = time.time()
        self.question_chars = question_chars
        self.outcome = "running"
        self.mode = "briefing"
        self.duration_ms = 0
        self.agents: dict[str, dict] = {}
        self.guardrails: Counter = Counter()
        self.checks: dict[str, Any] = {}
        self.tool_range: Optional[dict] = None
        self._last_event_ts = self.t0

    def observe(self, event: Any, parallel: frozenset = frozenset()) -> None:
        """Record one ADK event: which agent spoke, which tools it called."""
        ts = float(getattr(event, "timestamp", None) or time.time())
        author = getattr(event, "author", None) or "unknown"
        agent = self.agents.get(author)
        if agent is None:
            start = self.t0 if author in parallel else self._last_event_ts
            agent = self.agents[author] = {"start": start, "end": ts, "events": 0, "tools": []}
        agent["end"] = ts
        agent["events"] += 1
        for call in event.get_function_calls() or []:
            agent["tools"].append(call.name)
        for resp in event.get_function_responses() or []:
            if resp.name in ("historical_range_48h", "current_brent_range") and isinstance(resp.response, dict):
                self.tool_range = resp.response
        self._last_event_ts = max(self._last_event_ts, ts)

    def to_dict(self) -> dict:
        spans = [{
            "agent": name,
            "start_ms": round((a["start"] - self.t0) * 1000),
            "end_ms": round((a["end"] - self.t0) * 1000),
            "events": a["events"],
            "tools": sorted(set(a["tools"])),
        } for name, a in self.agents.items()]
        spans.sort(key=lambda s: (s["start_ms"], s["end_ms"]))
        return {
            "id": self.id,
            "at": time.strftime("%H:%M:%S", time.gmtime(self.t0)),
            "outcome": self.outcome,
            "mode": self.mode,
            "duration_ms": self.duration_ms,
            "question_chars": self.question_chars,
            "spans": spans,
            "guardrails": dict(self.guardrails),
            "checks": self.checks,
        }


class muted:
    """Context manager: self-tests must not count as real guardrail events."""

    def __enter__(self):
        self._token = _muted.set(True)

    def __exit__(self, *exc):
        _muted.reset(self._token)


def begin(question_chars: int) -> Trace:
    trace = Trace(question_chars)
    _current.set(trace)
    return trace


def finish(trace: Trace, outcome: str) -> None:
    trace.outcome = outcome
    trace.duration_ms = round((time.time() - trace.t0) * 1000)
    with _lock:
        _traces.append(trace)
        _counters["runs"] += 1
        _counters["outcome_" + outcome] += 1


def count(name: str) -> None:
    """Count a guardrail event, globally and on the trace of the current request if any."""
    if _muted.get():
        return
    with _lock:
        _counters["guardrail_" + name] += 1
    trace = _current.get()
    if trace is not None:
        trace.guardrails[name] += 1


def check_answer(text: str, tool_range: Optional[dict]) -> dict:
    """Ground the answer against the data-driven range the tool returned.

    range_quoted: both the low and the high of the tool's range appear in the text.
    off_range_figures: dollar amounts far from the tool's median (possible invented prices).
    """
    checks: dict[str, Any] = {"disclaimer": "not financial advice" in text.lower()}
    if not tool_range or not all(k in tool_range for k in ("low", "median", "high")):
        checks["range_quoted"] = None
        checks["off_range_figures"] = None
        return checks
    numbers = []
    for m in _NUMBER.finditer(text):
        try:
            numbers.append(float(m.group(1).replace(",", "")))
        except ValueError:
            continue
    near = lambda target: any(abs(n - target) <= 0.06 for n in numbers)
    checks["range_quoted"] = near(tool_range["low"]) and near(tool_range["high"])
    mid = float(tool_range["median"])
    checks["off_range_figures"] = sum(
        1 for m in _DOLLAR.finditer(text) if not 0.5 * mid <= float(m.group(1)) <= 1.5 * mid)
    return checks


def _percentile(values: list, q: float) -> Optional[int]:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def summary() -> dict:
    with _lock:
        traces = list(_traces)
        counters = dict(_counters)
    ok = [t for t in traces if t.outcome == "ok"]
    latencies = [t.duration_ms for t in ok]
    briefings = [t for t in ok if t.mode == "briefing"]
    graded = [t for t in ok if t.checks.get("range_quoted") is not None]
    return {
        "uptime_s": round(time.time() - STARTED_AT),
        "runs": counters.get("runs", 0),
        "outcomes": {k[8:]: v for k, v in counters.items() if k.startswith("outcome_")},
        "guardrail_events": {k[10:]: v for k, v in counters.items() if k.startswith("guardrail_")},
        "latency_ms": {"p50": _percentile(latencies, 0.5), "p95": _percentile(latencies, 0.95)},
        "graded_runs": len(graded),
        "range_quoted_rate": (
            round(100 * sum(1 for t in graded if t.checks["range_quoted"]) / len(graded), 1) if graded else None),
        "disclaimer_rate": (
            round(100 * sum(1 for t in briefings if t.checks.get("disclaimer")) / len(briefings), 1)
            if briefings else None),
        "off_range_runs": sum(1 for t in graded if t.checks.get("off_range_figures")),
    }


def recent(limit: int = 30) -> list:
    with _lock:
        return [t.to_dict() for t in list(_traces)[-limit:]][::-1]


def reset() -> None:
    """Clear state (used by tests)."""
    with _lock:
        _traces.clear()
        _counters.clear()
