import unittest

from src.analysis.trade_geometry import compute_trade_plan


def _bars():
    bars = []
    # downtrend then flat platform ~ 100-110 with low volume, then breakout
    for i in range(30):
        bars.append({"date": f"d{i}", "close": 140 - i, "high": 141 - i, "low": 139 - i,
                     "open": 140 - i, "volume": 1000})
    for i in range(40):
        c = 105 + (i % 5)
        bars.append({"date": f"p{i}", "close": c, "high": c + 1, "low": c - 1,
                     "open": c, "volume": 500 if i != 20 else 100})
    for i in range(5):
        bars.append({"date": f"b{i}", "close": 112 + i, "high": 113 + i, "low": 111 + i,
                     "open": 111 + i, "volume": 2000 if i == 0 else 600})
    return bars


class TradeGeometryTests(unittest.TestCase):
    def test_insufficient_data(self):
        self.assertIsNone(compute_trade_plan([{"close": 10, "volume": 1}] * 10))
        self.assertIsNone(compute_trade_plan(None))

    def test_plan_levels_ordered(self):
        plan = compute_trade_plan(_bars())
        self.assertIsNotNone(plan)
        self.assertLess(plan["stop_loss"], plan["box_low"])
        self.assertGreater(plan["entry"], plan["box_high"])
        self.assertGreater(plan["target_1"], plan["entry"])
        self.assertGreater(plan["target_2"], plan["target_1"])

    def test_breakout_annotation(self):
        plan = compute_trade_plan(_bars())
        kinds = {a["kind"] for a in plan["annotations"]}
        self.assertIn("breakout", kinds)
        self.assertIn("volume_low", kinds)
        self.assertTrue(plan["breakout_confirmed"])

    def test_wide_range_is_not_platform(self):
        bars = [{"close": 100 + i * 5, "high": 102 + i * 5, "low": 98 + i * 5, "volume": 1}
                for i in range(60)]
        self.assertIsNone(compute_trade_plan(bars))


if __name__ == "__main__":
    unittest.main()

