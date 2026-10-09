"""ZBA v2 (spec section 14): the improvement rules as ticket._zba options and their simulation."""
import unittest

from bot import ticket, ticket_backtest as tb
from test_ticket import ctx, zba_day

RISING = (10.0, 10.5, 9.9, 10.4)  # range 0.60, body 0.40: a long, buy stop at 10.50


def sig(bars, spread=0.02, **kw):
    return ticket.signal("ZBA", "REGULAR", bars, ctx(**kw), lambda at: spread)


def with_volume(bars, vols):
    """Replace the volume of the given minutes {"HH:MM": v}."""
    return [(m, o, h, l, c, vols.get(m, v), vw) for m, o, h, l, c, v, vw in bars]


class Default(unittest.TestCase):
    def test_no_option_is_k10s_as_before(self):
        bars = zba_day(RISING, [(10.4, 10.45, 10.3, 10.4), (10.4, 10.5, 10.35, 10.45)])
        t = sig(bars)
        self.assertEqual((t["at"], t["level"], t["entry_type"], t["limit"], t["R"], t["shares"]),
                         ("09:36", 10.5, "STP", None, 0.20, 500))
        self.assertNotIn("ioc", t)


class Candle(unittest.TestCase):
    def test_range_over_atr_and_body_ratio(self):
        bars = zba_day(RISING, [(10.5, 10.6, 10.45, 10.55)])
        self.assertTrue(sig(bars, candle=(0.10, 0.60, 0.50))["ok"])  # 0.6 / 2.0 = 0.30, body 0.67
        self.assertEqual(sig(bars, atr=0.5, candle=(0.10, 0.60, 0.50))["reason"], "candle")  # 1.20 of ATR
        self.assertEqual(sig(bars, atr=8.0, candle=(0.10, 0.60, 0.50))["reason"], "candle")  # 0.075 of ATR
        thin = zba_day((10.0, 10.5, 9.9, 10.1), [(10.5, 10.6, 10.45, 10.55)])  # body 0.10 / 0.60
        self.assertEqual(sig(thin, candle=(0.10, 0.60, 0.50))["reason"], "candle")


class Confirm(unittest.TestCase):
    def bars(self, vol):
        b = zba_day(RISING, [(10.4, 10.55, 10.4, 10.45), (10.45, 10.6, 10.45, 10.58), (10.6, 10.7, 10.55, 10.65)])
        return with_volume(b, {"09:36": vol})

    def test_a_close_beyond_the_level_on_volume_then_the_next_open(self):
        t = sig(self.bars(1500), confirm=1.5)  # the 09:35 touch closes below; 09:36 closes above on 1.5 x 1000
        self.assertEqual((t["at"], t["entry_type"], t["level"], t["entry"]), ("09:37", "MKT", 10.5, 10.58))
        self.assertEqual((t["R"], t["shares"]), (0.20, 500))
        self.assertEqual(sig(self.bars(1499), confirm=1.5)["reason"], "trigger")  # under 1.5 x the mean minute
        self.assertEqual(sig(self.bars(1999), confirm=2)["reason"], "trigger")

    def test_the_market_entry_fills_at_the_next_open(self):
        t = sig(self.bars(3000), confirm=2)
        res = tb.simulate(t, self.bars(3000), ctx())
        self.assertEqual((res["filled"], res["fill_at"], res["fill"]), (True, "09:37", 10.6))


class VolStop(unittest.TestCase):
    def test_the_far_side_of_the_candle_when_wider(self):
        bars = zba_day(RISING, [(10.5, 10.6, 10.45, 10.55)])
        t = sig(bars, vstop=0.10)  # max(0.10 x 2.0, 10.50 - 9.90)
        self.assertEqual((t["R"], t["shares"], t["stop"]), (0.60, 166, 9.90))
        self.assertEqual(sig(bars, vstop=0.50)["R"], 1.0)  # 0.50 x 2.0 is wider
        short = zba_day((10.0, 10.1, 9.5, 9.6), [(9.55, 9.6, 9.4, 9.45)])
        self.assertEqual(sig(short, atr=1.0, vstop=0.10)["R"], 0.60)  # 10.10 - 9.50


class Reward(unittest.TestCase):
    def test_room_to_open_plus_atr_against_twice_d(self):
        bars = zba_day(RISING, [(10.5, 10.6, 10.45, 10.55)])
        self.assertTrue(sig(bars, rr=(1.0, 2.0))["ok"])  # 12.00 - 10.50 = 1.50 >= 0.40
        self.assertEqual(sig(bars, atr=0.6, rr=(1.0, 2.0))["reason"], "reward")  # 10.60 - 10.50 = 0.10 < 0.12


class StopLimit(unittest.TestCase):
    def test_the_limit_and_no_fill_beyond_it(self):
        bars = zba_day(RISING, [(10.4, 10.45, 10.3, 10.4), (10.6, 10.7, 10.55, 10.6)])  # gaps over the level
        t = sig(bars, limit_d=0.25)
        self.assertEqual((t["entry_type"], t["limit"], t["ioc"]), ("STP_LMT", 10.55, True))  # 10.50 + 0.25 x 0.20
        self.assertFalse(tb.simulate(t, bars, ctx())["filled"])  # opened at 10.60, over the limit: cancelled
        ok = zba_day(RISING, [(10.4, 10.45, 10.3, 10.4), (10.5, 10.6, 10.45, 10.55)])
        res = tb.simulate(sig(ok, limit_d=0.25), ok, ctx())
        self.assertEqual((res["filled"], res["fill"]), (True, 10.5))
        cheap = sig(zba_day(RISING, [(10.5, 10.6, 10.45, 10.55)]), atr=0.2, limit_d=0.25, spread=0.001)
        self.assertEqual(cheap["limit"], 10.51)  # at least one cent


class Gates(unittest.TestCase):
    def test_time_of_day_volume_and_depth(self):
        bars = zba_day(RISING, [(10.5, 10.6, 10.45, 10.55)])
        seen = []
        tod = lambda m: seen.append(m) or 2.0  # noqa: E731
        self.assertTrue(sig(bars, tod=tod, tod_min=2.0)["ok"])
        self.assertEqual(seen, ["09:35"])  # asked at the trigger minute
        self.assertEqual(sig(bars, tod=lambda m: 1.99, tod_min=2.0)["reason"], "tod")
        self.assertTrue(sig(bars, depth=lambda m: (100, 500), depth_min=500)["ok"])  # a long needs the ask side
        self.assertEqual(sig(bars, depth=lambda m: (900, 400), depth_min=500)["reason"], "depth")
        self.assertEqual(sig(bars, depth=lambda m: None, depth_min=500)["reason"], "depth")
        short = zba_day((10.0, 10.1, 9.5, 9.6), [(9.55, 9.6, 9.4, 9.45)])
        self.assertTrue(sig(short, atr=1.0, depth=lambda m: (500, 0), depth_min=500)["ok"])  # a short: the bid


class Ticks(unittest.TestCase):
    """simulate_ticks for the market (confirmation) entry and the stop-limit."""

    def test_market_entry_fills_at_the_first_trade_and_ioc_cancels(self):
        b = zba_day(RISING, [(10.4, 10.55, 10.4, 10.45), (10.45, 10.6, 10.45, 10.58), (10.6, 10.7, 10.55, 10.65)])
        b = with_volume(b, {"09:36": 3000})
        t = sig(b, confirm=2)
        r = tb.simulate_ticks(t, b, ctx(), [("a", 10.61), ("b", 10.62)])
        self.assertEqual((r["filled"], r["fill"], r["status"]), (True, 10.61, "ok"))
        bars = zba_day(RISING, [(10.4, 10.45, 10.3, 10.4), (10.5, 10.6, 10.45, 10.55)])
        t = sig(bars, limit_d=0.25)  # limit 10.55
        r = tb.simulate_ticks(t, bars, ctx(), [("a", 10.50), ("b", 10.56)])  # triggers, fills at 10.56
        self.assertEqual((r["filled"], r["status"]), (False, "limit"))
        r = tb.simulate_ticks(t, bars, ctx(), [("a", 10.50), ("b", 10.55)])
        self.assertEqual((r["filled"], r["fill"]), (True, 10.55))


if __name__ == "__main__":
    unittest.main()
