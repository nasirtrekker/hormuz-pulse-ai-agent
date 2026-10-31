"""Print the walk-forward backtest. Usage: python scripts/backtest.py [START_YEAR]"""
import json
import sys

sys.path.insert(0, ".")
from hormuz import backtest
from hormuz.market_data import align_implied_vol, load_history, load_ovx

rows = load_history()
dates = [d for d, _ in rows]
implied, _ = align_implied_vol(dates, load_ovx())
print(json.dumps(backtest.run(dates, [p for _, p in rows], implied,
                              int(sys.argv[1]) if len(sys.argv) > 1 else 2015), indent=2))
