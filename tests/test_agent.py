import math
import unittest

from hormuz.agent import predict_48h


class Predict48hTests(unittest.TestCase):
    def test_base_case_returns_illustrative_scenario(self):
        result = predict_48h(100, 5, 0.5, "medium")

        self.assertEqual(result["forecast_48h"], 102.35)
        self.assertEqual(result["move_pct"], 2.35)
        self.assertEqual(result["illustrative_range"], [98.64, 106.06])
        self.assertEqual(result["direction"], "UP")
        self.assertEqual(
            result["drivers_pct"],
            {"momentum": 0.6, "sentiment": 0.75, "hormuz_risk": 1.0},
        )

    def test_high_risk_widens_range_and_increases_scenario(self):
        base = predict_48h(100, 0, 0, "low")
        stress = predict_48h(100, 0, 0, "high")

        self.assertGreater(stress["forecast_48h"], base["forecast_48h"])
        self.assertGreater(
            stress["illustrative_range"][1] - stress["illustrative_range"][0],
            base["illustrative_range"][1] - base["illustrative_range"][0],
        )

    def test_risk_level_is_case_insensitive(self):
        self.assertEqual(
            predict_48h(100, 0, 0, " HIGH ")["forecast_48h"],
            predict_48h(100, 0, 0, "high")["forecast_48h"],
        )

    def test_invalid_inputs_are_rejected(self):
        cases = [
            (0, 0, 0, "low"),
            (-1, 0, 0, "low"),
            (math.nan, 0, 0, "low"),
            (math.inf, 0, 0, "low"),
            (100, 0, 1.01, "low"),
            (100, 0, 0, "unknown"),
            (100, 0, 0, None),
        ]
        for inputs in cases:
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                predict_48h(*inputs)


if __name__ == "__main__":
    unittest.main()
