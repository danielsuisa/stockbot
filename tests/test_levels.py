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


class Costs(unittest.TestCase):
    """Section 5: commission over 4 orders, the usable quote, cost in R, the stress case, the cost guard."""

    def ticket(self):
        bars = daily(20)
        return levels.plan("AAPL", bars, bars[-1][0])  # 50 shares, R = 2, legs (25, 25)

    def test_spread(self):
        self.assertAlmostEqual(levels.spread((10.00, 10.02, 1, 1, 5)), 0.02)
        self.assertEqual(levels.spread((10.00, 10.00, 1, 1, 5)), 0.01)  # locked
        self.assertEqual(levels.spread((10.02, 10.00, 1, 1, 5)), 0.01)  # crossed
        self.assertIsNone(levels.spread(None))
        self.assertIsNone(levels.spread((0.0, 10.0, 1, 1, 5)))
        self.assertIsNone(levels.spread((10.0, 10.02, 1, 1, 61)))  # older than 60 s
        self.assertAlmostEqual(levels.spread((10.0, 10.02, 1, 1, 60)), 0.02)

    def test_commission(self):
        self.assertAlmostEqual(levels.commission((25, 25)), 1.40)  # 25 x 0.0035 is below the $0.35 minimum
        self.assertAlmostEqual(levels.commission((500, 500)), 7.00)

    def test_cost_and_stress(self):
        t = self.ticket()
        self.assertAlmostEqual(levels.cost_r(t, 0.02), (1.40 + 1.00) / 100)
        self.assertAlmostEqual(levels.cost_r(t, 0.02, stress=True), (1.40 + 1.50) / 100)

    def test_cost_guard_edge(self):
        t = self.ticket()
        g = levels.guard(t, (50.0, 50.172, 1, 1, 1))  # (1.40 + 50 x 0.172) / 100 = 0.10: a ticket
        self.assertTrue(g["ok"])
        self.assertAlmostEqual(g["cost_r"], 0.10)
        self.assertAlmostEqual(g["guard_spread"], 0.172)
        self.assertEqual(levels.guard(t, (50.0, 50.1721, 1, 1, 1)), {"ok": False, "reason": "cost above 10% of risk"})
        self.assertEqual(levels.guard(t, None), {"ok": False, "reason": "no usable quote"})
        self.assertEqual(levels.guard({"ok": False, "reason": "shares below 2"}, None)["reason"], "shares below 2")

    def test_quote_times(self):
        cal = {"2026-11-25": ("09:30", "16:00"), "2026-11-27": ("09:30", "13:00")}
        from bot import alpaca
        self.assertEqual(levels.guard_time("2026-11-25", cal), alpaca.utc("2026-11-25", "15:55"))
        self.assertEqual(levels.guard_time("2026-11-27", cal), alpaca.utc("2026-11-27", "12:55"))  # half day
        self.assertEqual(levels.entry_time("2026-11-27", cal), alpaca.utc("2026-11-27", "09:35"))


def after(*ohlc, start="2025-02-03"):
    """Synthetic sessions after D: (o, h, l, c) rows -> bars with weekday dates."""
    d, out = dt.date.fromisoformat(start), []
    for o, h, l, c in ohlc:
        while d.weekday() >= 5:
            d += dt.timedelta(1)
        out.append((d.isoformat(), o, h, l, c, 1_000))
        d += dt.timedelta(1)
    return out


FILL0 = (50.0, 50.6, 50.4, 50.5)  # triggers the breakout at 50.51 and touches nothing else


class FillModel(unittest.TestCase):
    """Section 3's daily-bar simulation, rule by rule, on synthetic bars (stop 50.51, limit 50.76, R 2)."""

    def setUp(self):
        bars = daily(20)
        self.t = levels.plan("AAPL", bars, bars[-1][0])
        self.o = levels.plan("AAPL", bars, bars[-1][0], mode="open")  # limit 50.5

    def test_breakout_fills(self):
        t = self.t
        self.assertEqual(levels.fill(t, after(FILL0)), (0, 50.51))
        self.assertEqual(levels.fill(t, after((50.60, 51.0, 50.5, 50.9))), (0, 50.60))  # open between stop and limit
        self.assertEqual(levels.fill(t, after((51.0, 51.2, 50.70, 51.0))), (0, 50.76))  # rests at the limit
        self.assertEqual(levels.fill(t, after((51.0, 51.2, 50.9, 51.0), (51.0, 51.0, 50.7, 50.8))), (1, 50.76))
        quiet = (50.0, 50.4, 49.9, 50.0)
        self.assertIsNone(levels.fill(t, after(quiet, quiet, quiet)))
        self.assertIsNone(levels.fill(t, after(quiet, quiet, quiet, FILL0)))  # expired after 3 sessions

    def test_open_fills(self):
        self.assertEqual(levels.fill(self.o, after((50.4, 50.6, 50.3, 50.5))), (0, 50.4))
        self.assertIsNone(levels.fill(self.o, after((50.6, 50.8, 50.5, 50.7))))

    def test_stop_gap_rule1(self):
        ex = levels.exits(self.t, 50.51, 0, after(FILL0, (48.0, 48.2, 47.5, 48.0)))
        self.assertEqual(ex[0], ("stop", 48.0, 1))  # leg A at the open below the stop
        self.assertEqual(ex[1][1:], (48.0, 1))  # leg B at the open below the level carried (50.6 - 2 = 48.6)

    def test_stop_first_rule2(self):
        ex = levels.exits(self.t, 50.51, 0, after(FILL0, (50.5, 54.0, 48.0, 51.0)))
        self.assertEqual(ex[0], ("stop", 48.51, 1))

    def test_entry_session_stop_rule3(self):
        ex = levels.exits(self.t, 50.51, 0, after((48.3, 50.6, 48.2, 50.0)))  # opened below F - R before the fill
        self.assertEqual(ex[0], ("stop", 48.51, 0))

    def test_trailing_rule4(self):
        bars = after(FILL0, (50.6, 52.0, 50.5, 51.8), (51.9, 54.0, 52.5, 53.9), (53.0, 53.5, 51.9, 52.0))
        ex = levels.exits(self.t, 50.51, 0, bars)
        self.assertEqual(ex[0], ("target", 53.51, 2))
        self.assertEqual(ex[1], ("trail", 52.0, 3))  # running max 54, level 52
        gap = after(FILL0, (50.6, 52.0, 50.5, 51.8), (51.9, 54.0, 52.5, 53.9), (51.5, 51.8, 51.0, 51.2))
        self.assertEqual(levels.exits(self.t, 50.51, 0, gap)[1], ("trail", 51.5, 3))  # open below the carried 52

    def test_target_at_open_rule5(self):
        ex = levels.exits(self.t, 50.51, 0, after(FILL0, (54.0, 54.5, 53.8, 54.2), (53.0, 53.0, 52.0, 52.2)))
        self.assertEqual(ex[0], ("target", 54.0, 1))  # leg B closes on the next bar (level 52.5)

    def test_time_exit(self):
        flat = (50.6, 50.7, 50.5, 50.6)
        ex = levels.exits(self.t, 50.51, 0, after(FILL0, *[flat] * 21))
        self.assertEqual(ex, [("time", 50.6, 21), ("time", 50.6, 21)])  # the close of the 21st session after entry
        self.assertIsNone(levels.exits(self.t, 50.51, 0, after(FILL0, *[flat] * 20)))  # bars end before it

    def test_unfilled_is_zero(self):
        quiet = (50.0, 50.4, 49.9, 50.0)
        self.assertEqual(levels.simulate(self.t, after(quiet, quiet, quiet), 0.02),
                         {"filled": False, "gross_r": 0.0, "cost_r": 0.0})

    def test_gross_r_by_hand(self):
        self.assertAlmostEqual(levels.gross_r(self.t, 50.51, [("target", 53.51, 2), ("trail", 52.51, 3)]), 1.25)

    def test_simulate_filled(self):
        bars = after(FILL0, (54.0, 54.5, 53.8, 54.2), (54.0, 54.0, 52.0, 52.5))
        got = levels.simulate(self.t, bars, 0.02)  # A at 54.0; B: max 54.5, level 52.5, low 52.0 -> 52.5
        self.assertEqual(got["filled"], True)
        self.assertAlmostEqual(got["gross_r"], (25 * (54.0 - 50.51) + 25 * (52.5 - 50.51)) / 100)
        self.assertAlmostEqual(got["cost_r"], (1.40 + 1.00) / 100)


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
