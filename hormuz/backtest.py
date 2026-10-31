"""Walk-forward backtest of the 2-trading-day Brent range. Each forecast sees only earlier data."""
import math
from typing import Sequence

from . import stat_model as sm


def run(dates: Sequence[str], prices: Sequence[float], start_year: int = 2015, step: int = 5) -> dict:
    h = sm.HORIZON
    rets = sm._log_returns(prices)
    start = next(i for i, d in enumerate(dates) if int(d[:4]) >= start_year)
    rw_err, mom_err, width = [], [], []
    hits_data = hits_fixed = n = 0
    z = None
    for i in range(start, len(prices) - h, step):
        if z is None or (i - start) % 250 == 0:
            z = sorted(sm.standardised_returns(prices[: i + 1]))
        sigma = sm._vol(rets[i - sm.VOL_WINDOW:i]) * math.sqrt(h)
        last, actual = prices[i], prices[i + h]
        lo = last * math.exp(sm._quantile(z, 0.10) * sigma)
        hi = last * math.exp(sm._quantile(z, 0.90) * sigma)
        mom = last * (1 + 0.12 * (last / prices[i - 5] - 1))
        rw_err.append(abs(actual - last) / actual)
        mom_err.append(abs(actual - mom) / actual)
        hits_data += lo <= actual <= hi
        hits_fixed += last * (1 - 0.0362) <= actual <= last * (1 + 0.0362)
        width.append((hi - lo) / last)
        n += 1
    return {
        "forecasts": n, "from": dates[start], "to": dates[-1],
        "coverage_data_driven_pct": round(100 * hits_data / n, 1),
        "coverage_fixed_band_pct": round(100 * hits_fixed / n, 1),
        "target_coverage_pct": 80,
        "mape_no_change_pct": round(100 * sum(rw_err) / n, 2),
        "mape_momentum_pct": round(100 * sum(mom_err) / n, 2),
        "mean_interval_width_pct": round(100 * sum(width) / n, 2),
    }
