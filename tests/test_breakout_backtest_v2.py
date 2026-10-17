"""v2 grade lock. No network. The v1 fill and cost tests stay in test_breakout_backtest."""
import datetime as dt
import unittest
from pathlib import Path

from bot import breakout, breakout_backtest as v1
from bot import breakout_backtest_v2 as v2


def bars(n=25, end=dt.date(2024, 6, 2)):
    out = []
    for i in range(n):
        d = (end - dt.timedelta(days=n - 1 - i)).isoformat()
        out.append((d, 97.5, 100.0, 97.0, 97.5, 200_000))
    return out


def tracked():
    row = breakout.context_row(bars(), "2024-06-03")
    assert breakout.passes(row)
    return breakout.track(row, "AAA")


class Lock(unittest.TestCase):
    def test_the_window_costs_and_gate_match_v1_and_the_volume_floor_is_five_percent(self):
        self.assertEqual(v2.VOL_MIN, 0.05)
        self.assertEqual((v2.START, v2.YEARS, v2.GATED), ("2024-01-02", ("2024", "2025", "2026"), ("A", "B")))
        self.assertEqual((v1.RISK_USD, v1.MIN_SHARES, v1.EXTRA_SLIP), (100.0, 10, 0.01))
        text = Path(v2.__file__).read_text(encoding="utf-8")
        self.assertIn("at least 100 trades", text)
        self.assertIn("t >= 2", text)
        self.assertIn("95th percentile", text)
        self.assertIn("2024 and in 2025 and in 2026", text)
        self.assertNotIn("ib_insync", text)
        self.assertNotIn("submit", text.lower())
        self.assertIn("does not place or transmit an order", text)

    def test_five_percent_volume_is_b_and_a_tight_spread_makes_it_a(self):
        self.assertEqual(v2.grade_v2(1.0, 1.5, 0.049), "C")
        self.assertEqual(v2.grade_v2(None, 1.5, None), "C")
        self.assertEqual(v2.grade_v2(1.0, 1.5, 0.05), "B")  # 1.00 > 0.25 * 1.5
        self.assertEqual(v2.grade_v2(None, 1.5, 0.05), "B")  # a missing spread stays B
        self.assertEqual(v2.grade_v2(0.375, 1.5, 0.05), "A")

    def test_volume_stops_at_the_trigger_minute(self):
        name = {"avg_vol": 200_000}
        minutes = [("08:00", 1, 1, 1, 1, 8_000), ("10:00", 1, 1, 1, 1, 2_000), ("10:05", 1, 1, 1, 1, 50_000)]
        self.assertAlmostEqual(v2.vol_ratio(name, minutes, "10:00"), 0.05)
        self.assertEqual(v2.vol_ratio({"avg_vol": 0}, minutes, "10:00"), None)


class Feed:
    def sessions(self, start, end):
        return [("2024-06-03", "16:00")]

    def daily(self, start, end):
        return {"AAA": bars()}

    def minutes(self, symbols, day):
        # A 1% gap stays under the v1 B band. Volume through the cross is 25.5% of the average day.
        return {"AAA": [("08:00", 98.5, 98.6, 98.2, 98.5, 50_000),
                        ("10:00", 99.8, 100.2, 99.7, 100.05, 1_000),
                        ("15:55", 101.0, 101.2, 100.8, 101.0, 500)]}

    def trades(self, symbol, day, minute):
        return [99.9, 100.02, 100.10]

    def quote(self, symbol, day, minute):
        return (100.0, 100.2, 100, 100, 0.0)


class Replay(unittest.TestCase):
    def test_the_same_fill_is_v1_c_and_v2_a(self):
        result = v2.replay(Feed(), today="2024-06-04")
        self.assertTrue(result["complete"])
        (trade,) = result["trades"]
        self.assertEqual((trade["grade_v1"], trade["grade"]), ("C", "A"))
        self.assertGreaterEqual(trade["vol_ratio"], 0.05)
        self.assertEqual(result["judge"]["stats"]["n"], 1)
        self.assertEqual(result["v1_c"]["n"], 1)
        self.assertAlmostEqual(result["v1_c"]["mean"], trade["net_r"])
        text = v2.render(result)
        self.assertIn("v1 grade C", text)
        self.assertIn("Breakout rule v2", text)


if __name__ == "__main__":
    unittest.main()
