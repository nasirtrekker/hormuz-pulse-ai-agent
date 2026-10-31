"""Ingress, tool-output and egress guardrails wired into the ADK agents as callbacks.

  ingress   before_agent_callback   validates the user's request before any model runs
  tool out  after_agent_callback    sanitizes and validates what the research agents
            after_tool_callback     (web search output) and Python tools return
            on_tool_error_callback  turns tool failures into data the model can report
  egress    after_model_callback    redacts secrets and enforces the disclaimer
"""
import math
import re
from typing import Any, Optional

from google.genai import types

from . import model_armor

MAX_INPUT_CHARS = 1000
MAX_FIELD_CHARS = 6000
MAX_OUTPUT_CHARS = 12000
DISCLAIMER = "Educational only, not financial advice."

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_INJECTION = re.compile(
    r"ignore\s+(all\s+|any\s+)?(previous|prior|above)\s+(instructions|prompts)"
    r"|disregard\s+.{0,40}instructions"
    r"|(reveal|print|show|leak)\s+.{0,40}(system\s+prompt|instructions|api\s*key|secret|credentials)"
    r"|you\s+are\s+now\s+",
    re.IGNORECASE,
)
_SECRETS = re.compile(
    r"AIza[0-9A-Za-z_\-]{35}"
    r"|ya29\.[0-9A-Za-z_\-]{20,}"
    r"|gh[pousr]_[0-9A-Za-z]{30,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----|$)"
)
_NUMBER = r"(-?\d+(?:\.\d+)?)"


def check_input(text: str) -> Optional[str]:
    """Return a reason to block the request, or None if it is acceptable."""
    if not text.strip():
        return "the request is empty"
    if len(text) > MAX_INPUT_CHARS:
        return f"the request is longer than {MAX_INPUT_CHARS} characters"
    if _CONTROL_CHARS.search(text):
        return "the request contains control characters"
    if _INJECTION.search(text):
        return "the request looks like an attempt to override the agent's instructions"
    return None


def ingress_guard(callback_context: Any) -> Optional[types.Content]:
    """before_agent_callback: stop the run before any model call if the request is unsafe."""
    content = callback_context.user_content
    parts = (content.parts or []) if content else []
    if any(p.inline_data or p.file_data for p in parts):
        reason = "attachments are not supported"
    else:
        text = " ".join(p.text for p in parts if p.text)
        reason = check_input(text) or model_armor.screen("input", text)
    if reason is None:
        return None
    return types.Content(
        role="model",
        parts=[types.Part(text=f"Request blocked: {reason}. "
                               "Ask for a Brent forecast, e.g. 'Predict Brent for the next 48 hours.'")],
    )


def redact_secrets(text: str) -> str:
    return _SECRETS.sub("[REDACTED]", text)


def _field_problems(key: str, text: str) -> list[str]:
    problems = []

    def number(label: str) -> Optional[float]:
        m = re.search(rf"{label}:\s*\$?\s*{_NUMBER}", text, re.IGNORECASE)
        return float(m.group(1)) if m else None

    if key == "news":
        if not re.search(r"RISK LEVEL:\s*(low|medium|high)\b", text, re.IGNORECASE):
            problems.append("RISK LEVEL is missing or not low/medium/high")
    elif key == "sentiment":
        score = number("SENTIMENT SCORE")
        if score is None or not -1 <= score <= 1:
            problems.append("SENTIMENT SCORE is missing or outside -1..1")
    elif key == "market":
        price = number("BRENT PRICE")
        change = number("BRENT 5D CHANGE %")
        if price is None or not 10 <= price <= 500:
            problems.append("BRENT PRICE is missing or outside 10..500 USD")
        if change is None or not -50 <= change <= 50:
            problems.append("BRENT 5D CHANGE % is missing or outside -50..50")
    return problems


def sanitize_research(key: str, value: Any) -> str:
    """Treat web-derived text as untrusted: clean it, bound it, and flag bad fields."""
    if not isinstance(value, str) or not value.strip():
        return "VALIDATION FAILED: no data was returned."
    text = _CONTROL_CHARS.sub("", value)[:MAX_FIELD_CHARS]
    text = _INJECTION.sub("[removed]", text)
    text = redact_secrets(text)
    problems = _field_problems(key, text)
    if problems:
        text += "\nVALIDATION FAILED: " + "; ".join(problems) + "."
    return text


def research_guard(key: str):
    """after_agent_callback factory for the agents that write `key` into session state."""
    def callback(callback_context: Any) -> None:
        cleaned = sanitize_research(key, callback_context.state.get(key))
        # Web-derived text is untrusted: screen it for indirect prompt injection.
        if model_armor.screen("input", cleaned):
            cleaned = "VALIDATION FAILED: content was blocked by the safety filter."
        callback_context.state[key] = cleaned
    return callback


def _numbers_ok(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_numbers_ok(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(_numbers_ok(v) for v in value)
    return True


def tool_output_guard(tool: Any, args: dict, tool_context: Any, tool_response: Any) -> Optional[dict]:
    """after_tool_callback: reject malformed or implausible tool results."""
    if not isinstance(tool_response, dict):
        return {"error": f"{tool.name} returned an unexpected result type."}
    if not _numbers_ok(tool_response):
        return {"error": f"{tool.name} returned a non-finite number."}
    low, mid, high = (tool_response.get(k) for k in ("low", "median", "high"))
    if None not in (low, mid, high) and not low <= mid <= high:
        return {"error": f"{tool.name} returned an inconsistent range; discard it."}
    forecast = tool_response.get("forecast_48h", mid)
    price = args.get("price")
    if isinstance(forecast, (int, float)) and isinstance(price, (int, float)) and price > 0:
        if not 0.5 * price <= forecast <= 1.5 * price:
            return {"error": f"{tool.name} produced an implausible forecast; discard it."}
    return None


def tool_error_guard(tool: Any, args: dict, tool_context: Any, error: Exception) -> Optional[dict]:
    """on_tool_error_callback: report invalid inputs to the model instead of crashing the run."""
    if isinstance(error, ValueError):
        return {"error": f"Invalid input to {tool.name}: {error}"}
    return None


def egress_guard(require_disclaimer: bool = False):
    """after_model_callback factory: redact secrets, cap length, enforce the disclaimer."""
    def callback(callback_context: Any, llm_response: Any) -> Optional[Any]:
        content = getattr(llm_response, "content", None)
        if llm_response.partial or not content or not content.parts:
            return None
        if any(p.function_call for p in content.parts):
            return None
        parts = []
        for p in content.parts:
            if p.text and not p.thought:
                p = types.Part(text=redact_secrets(p.text)[:MAX_OUTPUT_CHARS])
            parts.append(p)
        texts = [p.text for p in parts if p.text and not p.thought]
        if require_disclaimer and texts:
            reason = model_armor.screen("output", " ".join(texts))
            if reason:
                blocked = types.Part(text=f"Response withheld: {reason}. {DISCLAIMER}")
                return llm_response.model_copy(
                    update={"content": types.Content(role=content.role, parts=[blocked])})
        if require_disclaimer and texts and "not financial advice" not in " ".join(texts).lower():
            parts.append(types.Part(text=f"\n\n{DISCLAIMER}"))
        return llm_response.model_copy(
            update={"content": types.Content(role=content.role, parts=parts)})
    return callback
