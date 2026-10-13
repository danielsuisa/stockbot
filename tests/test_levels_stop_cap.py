"""The owner's stop cap (docs/superpowers/specs/2026-10-13-list-tickets-design.md section 1): R = min(2 x ATR14, 0.10 x
P), in bot/levels.plan, the one code path of /ticket and the list tickets. SYNTHETIC bars only."""
import unittest

from bot import levels


def bars(n=30, close=50.0, rng=1.0, vol=10_000_000, last="2026-03-09"):
    """n daily bars ending on `last` (weekdays), each with a true range of `rng` around a flat close."""
    import datetime as dt
    d, out = dt.date.fromisoformat(last), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append((d.isoformat(), close, close + rng / 2, close - rng / 2, close, vol))
        d -= dt.timedelta(1)
    return out[::-1]


class StopCap(unittest.TestCase):
    def test_volatile_stock_is_capped_at_10pct(self):
        b = bars(rng=5.0)  # ATR 5: 2 x ATR = 10 = about 19.6% of P = 52.51
        t = levels.plan("VOL", b, "2026-03-09", risk=100.0)
        P = t["planned_entry"]
        self.assertAlmostEqual(P, 52.51)  # breakout: the day's high + 1 cent
        self.assertAlmostEqual(t["R"], 0.10 * P)
        self.assertTrue(t["stop_capped"])
        self.assertAlmostEqual(t["stop_loss"], levels.px(P - 0.10 * P))  # 10% below the planned entry
        self.assertAlmostEqual(t["target"], levels.px(P + 1.5 * 0.10 * P))
        self.assertAlmostEqual(t["trail"], levels.px(0.10 * P, ref=P))
        self.assertEqual(t["shares"], int(100.0 / (0.10 * P) + levels.EPS))  # shares = risk / R, as today
        self.assertAlmostEqual(t["atr"], 5.0)  # the ATR itself is unchanged

    def test_calm_stock_keeps_2_atr(self):
        t = levels.plan("CALM", bars(rng=1.0), "2026-03-09", risk=100.0)  # 2 x ATR = 2 = about 4% of P
        self.assertAlmostEqual(t["R"], 2.0)
        self.assertFalse(t["stop_capped"])

    def test_boundary(self):
        self.assertEqual(levels.r_of(2.5, 50.0), (5.0, False))  # 2 x ATR exactly 10% of P: not capped
        self.assertEqual(levels.r_of(2.51, 50.0), (5.0, True))  # just above: the cap
        self.assertEqual(levels.r_of(2.49, 50.0), (4.98, False))

    def test_open_mode_cap_uses_the_close(self):
        t = levels.plan("VOL", bars(rng=5.0), "2026-03-09", mode="open", risk=100.0)
        self.assertAlmostEqual(t["planned_entry"], 50.0)
        self.assertAlmostEqual(t["R"], 5.0)  # 10% of the close, below 2 x ATR = 10

    def test_refusals_unchanged(self):
        self.assertEqual(levels.plan("X", bars(n=10), "2026-03-09")["reason"], "fewer than 15 bars")
        self.assertEqual(levels.plan("X", bars(), "2026-03-10")["reason"], "no bar on the signal day")
        self.assertEqual(levels.plan("X", bars(rng=5.0), "2026-03-09", risk=5.0)["reason"], "shares below 2")
        tiny = bars(rng=5.0, vol=100)
        self.assertEqual(levels.plan("X", tiny, "2026-03-09", risk=100.0)["reason"], "size above 1% of dollar volume")


if __name__ == "__main__":
    unittest.main()
