"""The cache-only H1 signal count (bot/intraday_count.py): detectors replayed through the scan loop, the power gate
arithmetic, and the no-network guard."""
import unittest
from unittest import mock

from bot import intraday_count
from test_intraday_screener import minutes, ATR, PREV


class Count(unittest.TestCase):
    def test_flag_counted_once_and_touch_flag(self):
        bars = minutes("09:30", [(20, 20.2, 19.9, 20.1, 100), (20.1, 20.9, 20.05, 20.8, 300),
                                 (20.8, 20.85, 20.6, 20.65, 80), (20.65, 20.7, 20.55, 20.6, 60),
                                 (20.6, 20.65, 20.5, 20.55, 50)]) + minutes("10:30", [(20.6, 21.2, 20.5, 21.1, 400)])
        got = intraday_count.signals_of(bars, ATR, PREV)
        self.assertEqual([(g[0], g[1], g[2], g[4]) for g in got],
                         [("09:40", "orb5", 20.91, True), ("09:50", "flag", 20.91, True), ("10:35", "orb15", 21.21, False)])

    def test_power_numbers_match_the_audit(self):
        n, years = intraday_count.power(750)
        self.assertEqual(round(n), 1340)
        self.assertEqual(round(years, 1), 1.8)
        self.assertEqual(round(intraday_count.power(900)[1], 1), 1.5)

    def test_blocked_fetch_counts_a_miss(self):
        with self.assertRaises(intraday_count.CacheMiss):
            intraday_count._blocked("https://data.alpaca.markets/v2/stocks/bars", {"symbols": "AAPL"})
        self.assertEqual(intraday_count.MISSES["v2/stocks/bars"], 1)

    def test_render_fail_and_pass(self):
        rows = [{"setup": "flag", "touched": True}] * 100 + [{"setup": "orb5", "touched": False}] * 5
        cov = {"sessions with a top-20 list": 50, "sessions whose minute bars are not cached": 0, "ticker-days": 1000,
               "ticker-days without minute bars": 0, "ticker-days without a previous close": 0}
        text = intraday_count.render(rows, cov, ["d"] * 50, 1.0, "2024", "")
        self.assertIn("504/year", text)  # 100 x 252 / 50
        self.assertIn("2.7 years → FAIL", text)
        text = intraday_count.render(rows * 2, cov, ["d"] * 50, 1.0, "2024", "")
        self.assertIn("1.3 years → PASS", text)


if __name__ == "__main__":
    unittest.main()
