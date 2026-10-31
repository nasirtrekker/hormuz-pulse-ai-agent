"""Walk-forward backtest: each forecast uses only data available before the forecast date.

Compares, for the 2-trading-day horizon:
  random walk  point = last price           (the baseline any model must beat)
  momentum     point = last * (1 + 0.12 * 5-day % change / 100)   (the old heuristic, momentum term only)
  data range   80% interval from hormuz.stat_model (empirical, volatility-scaled)
  fixed band   80% interval from the old heuristic's fixed 3.62% band
"""
import math
import sys

sys.path.insert(0, ".")
from hormuz import stat_model as sm
from hormuz.market_data import load_history

START_YEAR = int(sys.argv[1]) if len(sys.argv) > 1 else 2015
STEP = 5  # evaluate every 5th day to keep the run short

rows = load_history()
dates = [d for d, _ in rows]
prices = [p for _, p in rows]
H = sm.HORIZON
rets = sm._log_returns(prices)

rw_err, mom_err, hits_data, hits_fixed, n, width_data = [], [], 0, 0, 0, []
z_cache = None
start = next(i for i, d in enumerate(dates) if int(d[:4]) >= START_YEAR)
for i in range(start, len(prices) - H, STEP):
    hist = prices[: i + 1]
    # Refit the quantiles only on data whose outcome was already known at day i.
    z = sorted(sm.standardised_returns(prices[: i + 1])) if (z_cache is None or (i - start) % 250 == 0) else z_cache
    z_cache = z
    sigma = sm._vol(rets[i - sm.VOL_WINDOW:i]) * math.sqrt(H)
    last, actual = prices[i], prices[i + H]
    lo = last * math.exp(sm._quantile(z, 0.10) * sigma)
    hi = last * math.exp(sm._quantile(z, 0.90) * sigma)
    mom = last * (1 + 0.12 * (last / prices[i - 5] - 1))
    flo, fhi = last * (1 - 0.0362), last * (1 + 0.0362)
    rw_err.append(abs(actual - last) / actual)
    mom_err.append(abs(actual - mom) / actual)
    hits_data += lo <= actual <= hi
    hits_fixed += flo <= actual <= fhi
    width_data.append((hi - lo) / last)
    n += 1

print(f"forecasts: {n} from {dates[start]} to {dates[-1]}")
print(f"point error (MAPE)  random walk: {100*sum(rw_err)/n:.3f}%   momentum heuristic: {100*sum(mom_err)/n:.3f}%")
print(f"80% interval coverage (target 80%)  data-driven: {100*hits_data/n:.1f}%   fixed band: {100*hits_fixed/n:.1f}%")
print(f"mean data-driven interval width: {100*sum(width_data)/n:.2f}% of price (fixed band: 7.24%)")
