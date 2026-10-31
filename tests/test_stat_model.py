import math
import random
import unittest

from hormuz import stat_model
from hormuz.agent import historical_range_48h
from hormuz.market_data import load_history, parse


class MarketDataTests(unittest.TestCase):
    def test_parse_skips_missing_markers(self):
        rows = parse("observation_date,DCOILBRENTEU\n2020-01-01,60.5\n2020-01-02,.\n2020-01-03,-3\n")
        self.assertEqual(rows, [("2020-01-01", 60.5)])

    def test_bundled_snapshot_is_large_and_sane(self):
        rows = load_history()
        self.assertGreater(len(rows), 9000)
        self.assertTrue(all(p > 0 for _, p in rows))


class StatModelTests(unittest.TestCase):
    def test_range_is_ordered_and_scales_with_volatility(self):
        rng = random.Random(1)

        def series(vol):
            p, out = 50.0, []
            for _ in range(600):
                p *= math.exp(rng.gauss(0, vol))
                out.append(p)
            return out
        calm, wild = stat_model.forecast(series(0.005)), stat_model.forecast(series(0.03))
        for r in (calm, wild):
            self.assertLess(r["low"], r["median"])
            self.assertLess(r["median"], r["high"])
        self.assertGreater((wild["high"] - wild["low"]) / wild["last_price"],
                           (calm["high"] - calm["low"]) / calm["last_price"])

    def test_too_little_history_rejected(self):
        with self.assertRaises(ValueError):
            stat_model.forecast([50.0] * 100)

    def test_tool_uses_live_price_and_real_history(self):
        result = historical_range_48h(100.0)
        self.assertLess(result["low"], 100.0)
        self.assertGreater(result["high"], 100.0)
        self.assertGreater(result["training_samples"], 9000)

    def test_tool_rejects_bad_price(self):
        for bad in (0, -5, float("nan"), True, "80"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                historical_range_48h(bad)


if __name__ == "__main__":
    unittest.main()
