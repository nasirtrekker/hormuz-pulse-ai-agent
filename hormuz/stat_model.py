"""Data-driven 48-hour (2 trading day) Brent range from public history.

Method: volatility-scaled empirical quantiles. For every past day t we measure the
2-day log return divided by the volatility seen in the 20 days before t. The quantiles
of those standardised returns come from the data itself (no normal assumption); today's
range is those quantiles times today's trailing volatility.
"""
import math
from typing import Sequence

HORIZON = 2
VOL_WINDOW = 20
DEFAULT_QUANTILES = (0.10, 0.50, 0.90)


def _log_returns(prices: Sequence[float]) -> list[float]:
    return [math.log(b / a) for a, b in zip(prices, prices[1:])]


def _vol(returns: Sequence[float]) -> float:
    mean = sum(returns) / len(returns)
    return math.sqrt(sum((r - mean) ** 2 for r in returns) / (len(returns) - 1))


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    pos = q * (len(sorted_values) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def standardised_returns(prices: Sequence[float]) -> list[float]:
    """2-day log return / (trailing daily vol * sqrt(2)), for each day with enough history."""
    rets = _log_returns(prices)
    out = []
    for i in range(VOL_WINDOW, len(prices) - HORIZON):
        sigma = _vol(rets[i - VOL_WINDOW:i])
        if sigma > 0:
            out.append(math.log(prices[i + HORIZON] / prices[i]) / (sigma * math.sqrt(HORIZON)))
    return out


def forecast(prices: Sequence[float], quantiles: Sequence[float] = DEFAULT_QUANTILES,
             anchor: float = None) -> dict:
    """Range two trading days ahead, anchored on `anchor` (a live price) or the last historical price."""
    if len(prices) < VOL_WINDOW + 250:
        raise ValueError("need at least 270 prices of history")
    z = sorted(standardised_returns(prices))
    sigma = _vol(_log_returns(prices[-(VOL_WINDOW + 1):])) * math.sqrt(HORIZON)
    last = prices[-1] if anchor is None else anchor
    lo, mid, hi = (round(last * math.exp(_quantile(z, q) * sigma), 2) for q in quantiles)
    return {"last_price": last, "median": mid, "low": lo, "high": hi,
            "quantiles": list(quantiles), "daily_vol_pct": round(sigma / math.sqrt(HORIZON) * 100, 2),
            "training_samples": len(z)}


def _blend_scale(realised_daily: float, implied_daily: float) -> float:
    """Geometric mean of realised and market-implied (OVX) daily volatility."""
    return math.sqrt(realised_daily * implied_daily)


def forecast_blend(prices: Sequence[float], implied_daily_vol: Sequence[float], implied_now: float,
                   quantiles: Sequence[float] = DEFAULT_QUANTILES, anchor: float = None) -> dict:
    """Range two trading days ahead using realised and implied volatility together.

    implied_daily_vol is aligned with prices (NaN where unknown); implied_now is today's value
    (OVX / 100 / sqrt(252)). Standardised-return quantiles are learned from days with both inputs.
    """
    if len(prices) != len(implied_daily_vol):
        raise ValueError("prices and implied volatility must be aligned")
    if len(prices) < VOL_WINDOW + 250 or not implied_now > 0:
        raise ValueError("need at least 270 prices and a positive implied volatility")
    rets = _log_returns(prices)
    z = []
    for i in range(VOL_WINDOW, len(prices) - HORIZON):
        iv = implied_daily_vol[i]
        if not iv > 0:
            continue
        sigma = _vol(rets[i - VOL_WINDOW:i])
        if sigma > 0:
            z.append(math.log(prices[i + HORIZON] / prices[i]) / (_blend_scale(sigma, iv) * math.sqrt(HORIZON)))
    if len(z) < 500:
        raise ValueError("not enough days with implied volatility")
    z.sort()
    realised = _vol(rets[-VOL_WINDOW:])
    scale = _blend_scale(realised, implied_now) * math.sqrt(HORIZON)
    last = prices[-1] if anchor is None else anchor
    lo, mid, hi = (round(last * math.exp(_quantile(z, q) * scale), 2) for q in quantiles)
    return {"last_price": last, "median": mid, "low": lo, "high": hi, "quantiles": list(quantiles),
            "daily_vol_pct": round(scale / math.sqrt(HORIZON) * 100, 2),
            "realised_daily_vol_pct": round(realised * 100, 2),
            "implied_daily_vol_pct": round(implied_now * 100, 2),
            "training_samples": len(z)}
