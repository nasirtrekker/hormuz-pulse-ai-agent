"""Print the walk-forward backtest. Usage: python scripts/backtest.py [START_YEAR]"""
import json
import sys

sys.path.insert(0, ".")
from hormuz import backtest
from hormuz.market_data import load_history

rows = load_history()
print(json.dumps(backtest.run([d for d, _ in rows], [p for _, p in rows],
                              int(sys.argv[1]) if len(sys.argv) > 1 else 2015), indent=2))
