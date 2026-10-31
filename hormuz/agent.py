"""Hormuz Pulse — Gemini research plus an illustrative Brent price heuristic.

  collect (parallel) ─┬─ news_agent       Gemini + Google Search → Hormuz risk level
                      ├─ sentiment_agent  Gemini + Google Search → bullish/bearish score
                      └─ data_agent       Gemini + Google Search → live Brent price data
  → predictor_agent   Gemini extracts inputs; Python heuristic calculates scenarios
  → critic_agent      Gemini challenges the scenarios and writes the final briefing

The price heuristic is not a trained or backtested financial model.
"""
import math
import os

from google.adk.agents import Agent, ParallelAgent, SequentialAgent
from google.adk.tools import google_search

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")


def predict_48h(price: float, change_5d_pct: float, sentiment_score: float,
                risk_level: str) -> dict:
    """Calculate an illustrative 48-hour Brent scenario, not an ML forecast.

    Args:
      price: latest Brent price in USD/barrel
      change_5d_pct: Brent % change over the last 5 trading days
      sentiment_score: -1 (very bearish) to +1 (very bullish)
      risk_level: Strait of Hormuz disruption risk: "low", "medium" or "high"

    Returns:
      An illustrative point estimate and range derived from fixed assumptions.

    Raises:
      ValueError: If any input is missing, invalid, or outside its allowed range.
    """
    numeric_inputs = {
        "price": price,
        "change_5d_pct": change_5d_pct,
        "sentiment_score": sentiment_score,
    }
    for name, value in numeric_inputs.items():
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value)):
            raise ValueError(f"{name} must be a finite number")
    if price <= 0:
        raise ValueError("price must be greater than zero")
    if not -1 <= sentiment_score <= 1:
        raise ValueError("sentiment_score must be between -1 and 1")
    if not isinstance(risk_level, str):
        raise ValueError("risk_level must be low, medium, or high")
    risk = risk_level.lower().strip()
    if risk not in {"low", "medium", "high"}:
        raise ValueError("risk_level must be low, medium, or high")

    momentum = 0.3 * (change_5d_pct / 5) * 2
    sentiment = 1.5 * sentiment_score
    premium = {"low": 0.0, "medium": 1.0, "high": 3.0}[risk]
    move = momentum + sentiment + premium
    band = 1.28 * 2.0 * math.sqrt(2) * (1.5 if risk == "high" else 1.0)
    p = price * (1 + move / 100)
    return {"forecast_48h": round(p, 2), "move_pct": round(move, 2),
            "illustrative_range": [
                round(p * (1 - band / 100), 2),
                round(p * (1 + band / 100), 2),
            ],
            "direction": "UP" if move > 0.3 else "DOWN" if move < -0.3 else "FLAT",
            "drivers_pct": {"momentum": round(momentum, 2), "sentiment": round(sentiment, 2),
                            "hormuz_risk": premium}}


news_agent = Agent(
    name="news_agent", model=MODEL, tools=[google_search], output_key="news",
    instruction=(
        "Search news from the last 48 hours on the Strait of Hormuz: tanker traffic, "
        "seizures or attacks, Iran/US tensions, naval activity, OPEC+ decisions. "
        "Give 5-7 dated bullets with source names. Finish with exactly: "
        "'RISK LEVEL: low|medium|high' and one reason."),
)

sentiment_agent = Agent(
    name="sentiment_agent", model=MODEL, tools=[google_search], output_key="sentiment",
    instruction=(
        "Search oil market commentary from the last 48 hours: analyst notes, market wraps, "
        "bank forecasts, trader positioning. Label 5-7 items bullish/bearish/neutral for "
        "crude, with sources. Finish with exactly: 'SENTIMENT SCORE: <-1 to 1>'."),
)

data_agent = Agent(
    name="data_agent", model=MODEL, tools=[google_search], output_key="market",
    instruction=(
        "Search for the latest Brent crude futures price and its level 5 trading days ago. "
        "Reply in exactly these lines:\nBRENT PRICE: <number>\nBRENT 5D CHANGE %: <number>\n"
        "WTI PRICE: <number>\nAS OF: <date/time and source>"),
)

collect = ParallelAgent(name="collect", sub_agents=[news_agent, sentiment_agent, data_agent])

predictor_agent = Agent(
    name="predictor_agent", model=MODEL, tools=[predict_48h], output_key="forecast",
    instruction=(
        "NEWS:\n{news}\n\nSENTIMENT:\n{sentiment}\n\nMARKET:\n{market}\n\n"
        "Call predict_48h with BRENT PRICE, BRENT 5D CHANGE %, SENTIMENT SCORE and RISK LEVEL. "
        "Then call it again with risk_level='high' as the Hormuz stress case. "
        "Do not invent missing values; if a required input is unavailable, say that the "
        "scenario cannot be calculated. Show both results and their illustrative ranges "
        "in a markdown table. State that the formula is a heuristic, not a trained or "
        "backtested ML model, and that its range is not a calibrated confidence interval."),
)

critic_agent = Agent(
    name="critic_agent", model=MODEL,
    instruction=(
        "Forecast:\n{forecast}\n\nNews:\n{news}\nSentiment:\n{sentiment}\nMarket:\n{market}\n\n"
        "Write the final briefing:\n"
        "1. **48h scenario** — direction, Brent price, illustrative range\n"
        "2. Table: base case vs. Hormuz stress case\n"
        "3. **Why** — top 3 drivers with sources\n"
        "4. **Watch next 48h** — 3 signals that would flip the call\n"
        "5. **Critic** — 2 weaknesses or conflicts in the inputs\n"
        "6. State the heuristic is not a trained or backtested ML model and its range is "
        "not a calibrated confidence interval. Educational only, not financial advice."),
)

root_agent = SequentialAgent(
    name="hormuz_pulse",
    description=(
        "Gemini research agents and an illustrative Python heuristic produce Brent "
        "scenarios from Hormuz news; this is not a trained or backtested ML forecast."
    ),
    sub_agents=[collect, predictor_agent, critic_agent],
)
