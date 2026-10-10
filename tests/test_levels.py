"""The levels engine of the recommendation algorithm (spec 2026-10-10 revision 7, sections 3 and 5): pure functions,
synthetic bars only. No past ticket's outcome is computed on market data anywhere in these tests."""
import datetime as dt
import unittest

from bot import levels


def daily(n, close=50.0, rng=1.0, vol=1_000_000, start="2025-01-02"):
    """n weekday bars (date, o, h, l, c, v) with a constant close, so every true range is `rng`."""
    d, out = dt.date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append((d.isoformat(), close, close + rng / 2, close - rng / 2, close, vol))
        d += dt.timedelta(1)
    return out


class Levels(unittest.TestCase):
    def test_breakout_levels(self):
        bars = daily(20)
        t = levels.plan("AAPL", bars, bars[-1][0])
        self.assertTrue(t["ok"])
        self.assertEqual((t["mode"], t["horizon"]), ("breakout", 21))
        self.assertAlmostEqual(t["atr"], 1.0)
        self.assertAlmostEqual(t["R"], 2.0)
        self.assertEqual((t["entry_stop"], t["limit"], t["planned_entry"]), (50.51, 50.76, 50.51))
        self.assertEqual((t["stop_loss"], t["target"], t["trail"]), (48.51, 53.51, 2.0))
        self.assertEqual((t["shares"], t["legs"]), (50, (25, 25)))

    def test_open_levels(self):
        bars = daily(20)
        t = levels.plan("AAPL", bars, bars[-1][0], mode="open")
        self.assertEqual((t["limit"], t["planned_entry"]), (50.5, 50.0))
        self.assertNotIn("entry_stop", t)

    def test_bars_after_day_are_ignored(self):
        bars = daily(20)
        day = bars[-1][0]
        later = bars + [("2099-01-02", 999.0, 999.0, 1.0, 999.0, 1)]
        self.assertEqual(levels.plan("AAPL", later, day), levels.plan("AAPL", bars, day))

    def test_sizing_edges(self):
        t = levels.plan("X", daily(20, rng=20.0, close=500.0), daily(20)[-1][0])  # R = 40: 100 / 40 = 2.5
        self.assertEqual((t["shares"], t["legs"]), (2, (1, 1)))
        t = levels.plan("X", daily(20, rng=25.005, close=500.0), daily(20)[-1][0])  # R = 50.01
        self.assertEqual(t, {"ok": False, "reason": "shares below 2"})
        t = levels.plan("X", daily(20, rng=30.0, close=500.0), daily(20)[-1][0], risk=200.0)  # R = 60: 3 shares
        self.assertEqual(t["legs"], (1, 2))

    def test_fewer_than_15_bars(self):
        self.assertEqual(levels.plan("X", daily(14), daily(14)[-1][0]), {"ok": False, "reason": "fewer than 15 bars"})
        self.assertTrue(levels.plan("X", daily(15), daily(15)[-1][0])["ok"])

    def test_dollar_volume_share(self):
        bars = daily(20, vol=4_000)  # ADV 200,000: 1% is 2,000 < 50 x 50.51
        self.assertEqual(levels.plan("X", bars, bars[-1][0]),
                         {"ok": False, "reason": "size above 1% of dollar volume"})
        bars = daily(20, vol=5_051)  # 1% of ADV = 2,525.50 = the position: a ticket
        self.assertTrue(levels.plan("X", bars, bars[-1][0])["ok"])

    def test_dollar_volume_with_fewer_than_20_bars(self):
        bars = daily(15, vol=4_000)  # 15 bars: the mean over the bars there are
        self.assertEqual(levels.plan("X", bars, bars[-1][0])["reason"], "size above 1% of dollar volume")

    def test_risk_scales_shares(self):
        bars = daily(20)
        self.assertEqual(levels.plan("X", bars, bars[-1][0], risk=200.0)["shares"], 100)

    def test_px(self):
        self.assertEqual(levels.px(50.7651), 50.77)
        self.assertEqual(levels.px(0.123456), 0.1235)
        self.assertEqual(levels.px(1234.5649), 1234.56)


class UniverseMark(unittest.TestCase):
    """The partial universe mark of phase B (owner 2026-10-11): price, dollar volume, history; never a refusal."""

    def test_inside(self):
        bars = daily(253, close=50.0, vol=200_000)  # $10M a day, 252 bars before D
        self.assertEqual(levels.universe_mark(bars, bars[-1][0]), [])

    def test_price_threshold(self):
        ok = daily(253, close=5.0, vol=2_000_000)
        self.assertEqual(levels.universe_mark(ok, ok[-1][0]), [])
        low = daily(253, close=4.99, vol=3_000_000)
        self.assertEqual(levels.universe_mark(low, low[-1][0]), ["price below $5"])

    def test_dollar_volume_threshold(self):
        ok = daily(253, close=50.0, vol=200_000)
        self.assertEqual(levels.universe_mark(ok, ok[-1][0]), [])
        thin = daily(253, close=50.0, vol=199_999)
        self.assertEqual(levels.universe_mark(thin, thin[-1][0]), ["dollar volume below $10M"])

    def test_history_threshold(self):
        short = daily(252, close=50.0, vol=200_000)  # 251 bars before D
        self.assertEqual(levels.universe_mark(short, short[-1][0]), ["fewer than 252 sessions"])

    def test_two_failures_and_no_refusal(self):
        bars = daily(100, close=4.0, vol=3_000_000)  # $12M a day: only price and history fail
        self.assertEqual(levels.universe_mark(bars, bars[-1][0]), ["price below $5", "fewer than 252 sessions"])
        self.assertTrue(levels.plan("X", bars, bars[-1][0])["ok"])  # the mark never refuses a ticket


if __name__ == "__main__":
    unittest.main()
