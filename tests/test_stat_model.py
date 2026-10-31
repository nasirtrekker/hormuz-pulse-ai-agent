import math
import random
import unittest

from hormuz import stat_model
from hormuz.agent import historical_range_48h
from hormuz.market_data import align_implied_vol, load_history, load_ovx, parse, parse_ovx


class MarketDataTests(unittest.TestCase):
    def test_parse_skips_missing_markers(self):
        rows = parse("observation_date,DCOILBRENTEU\n2020-01-01,60.5\n2020-01-02,.\n2020-01-03,-3\n")
        self.assertEqual(rows, [("2020-01-01", 60.5)])

    def test_bundled_snapshot_is_large_and_sane(self):
        rows = load_history()
        self.assertGreater(len(rows), 9000)
        self.assertTrue(all(p > 0 for _, p in rows))


class ImpliedVolTests(unittest.TestCase):
    def test_parse_ovx_skips_missing(self):
        rows = parse_ovx("observation_date,OVXCLS\n2020-01-01,30.5\n2020-01-02,.\n")
        self.assertEqual(rows, [("2020-01-01", 30.5)])

    def test_alignment_carries_forward_only_briefly(self):
        dates = ["2020-01-01", "2020-01-03", "2020-02-01"]
        aligned, latest = align_implied_vol(dates, [("2020-01-01", 25.2)])
        self.assertAlmostEqual(aligned[0], 25.2 / 100 / math.sqrt(252))
        self.assertAlmostEqual(aligned[1], aligned[0])
        self.assertTrue(math.isnan(aligned[2]))
        self.assertAlmostEqual(latest, aligned[0])

    def test_bundled_ovx_is_real(self):
        self.assertGreater(len(load_ovx()), 4500)


class BlendModelTests(unittest.TestCase):
    def setUp(self):
        rows = load_history()
        self.prices = [p for _, p in rows]
        self.implied, self.now = align_implied_vol([d for d, _ in rows], load_ovx())

    def test_range_ordered_and_scales_with_implied_vol(self):
        calm = stat_model.forecast_blend(self.prices, self.implied, 0.01)
        wild = stat_model.forecast_blend(self.prices, self.implied, 0.05)
        for r in (calm, wild):
            self.assertLess(r["low"], r["median"])
            self.assertLess(r["median"], r["high"])
        self.assertGreater(wild["high"] - wild["low"], calm["high"] - calm["low"])

    def test_rejects_misaligned_or_bad_input(self):
        with self.assertRaises(ValueError):
            stat_model.forecast_blend(self.prices, self.implied[:-1], self.now)
        with self.assertRaises(ValueError):
            stat_model.forecast_blend(self.prices, self.implied, 0.0)


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
        self.assertGreater(result["training_samples"], 4000)
        self.assertIn("implied_daily_vol_pct", result)

    def test_tool_rejects_bad_price(self):
        for bad in (0, -5, float("nan"), True, "80"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                historical_range_48h(bad)


if __name__ == "__main__":
    unittest.main()


class FuturesFeedTests(unittest.TestCase):
    def _feed(self, closes):
        import json
        return json.dumps({"chart": {"result": [{"timestamp": [1761782400 + 86400 * i for i in range(len(closes))],
                                                  "indicators": {"quote": [{"close": closes}]}}]}})

    def test_takes_latest_non_null_close(self):
        from hormuz import market_data
        date, price = market_data.parse_futures(self._feed([114.88, 117.64, None]), 123.1)
        self.assertEqual(price, 117.64)

    def test_rejects_implausible_value(self):
        from hormuz import market_data
        self.assertIsNone(market_data.parse_futures(self._feed([40.0]), 123.1))
