"""The study deck's alert rules (bot/deck.py): one positive and one negative fixture per pattern, and the worked
bracket arithmetic from slides 24, 40 and 55."""
import unittest

from bot import deck, ticket


def b(start, o, h, l, c, v=100, vwap=None):
    end = ticket.hhmm(ticket.mins(start) + 5)
    d = {"start": start, "end": end, "o": o, "h": h, "l": l, "c": c, "v": v}
    if vwap is not None:
        d["vwap"] = vwap
    return d


def ctx(**kw):
    base = {"atr": 1.0, "prev_high": None, "prev_low": None, "prev_close": None}
    base.update(kw)
    return base


def names(bars, **kw):
    return [s["setup"] for s in deck.signals(bars, ctx(**kw))]


class Shapes(unittest.TestCase):
    def test_slide_11_is_bullish_with_those_wicks_and_is_not_yet_a_hammer(self):
        # close 20.30, low 19.40, high 20.50, open 20.00 — the chapter 1 answer
        candle = b("10:00", 20.00, 20.50, 19.40, 20.30)
        self.assertGreater(candle["c"], candle["o"])
        self.assertAlmostEqual(deck.body(candle), 0.30)
        self.assertAlmostEqual(candle["h"] - max(candle["o"], candle["c"]), 0.20)
        self.assertAlmostEqual(min(candle["o"], candle["c"]) - candle["l"], 0.60)
        self.assertFalse(deck.is_hammer(candle))  # the slide calls this "almost" a hammer

    def test_hammer_needs_the_upper_third_and_a_real_body(self):
        self.assertTrue(deck.is_hammer(b("10:00", 20.40, 20.50, 19.40, 20.45)))
        self.assertFalse(deck.is_hammer(b("10:00", 20.05, 20.50, 19.40, 20.20)))  # wick is long, body sits below the upper third
        self.assertFalse(deck.is_hammer(b("10:00", 20.30, 20.50, 20.20, 20.45)))  # lower wick is shorter than the body
        self.assertTrue(deck.is_doji(b("10:00", 20.40, 20.50, 19.40, 20.40)))
        self.assertFalse(deck.is_hammer(b("10:00", 20.40, 20.50, 19.40, 20.40)))

    def test_engulfing_covers_the_body_only(self):
        bear, bull = b("10:00", 20.2, 20.3, 19.8, 20.0), b("10:05", 19.95, 20.5, 19.9, 20.35)
        self.assertTrue(deck.is_bull_engulfing(bear, bull))
        self.assertFalse(deck.is_bull_engulfing(bull, bear))  # the mirror is not a long
        self.assertFalse(deck.is_bull_engulfing(bear, b("10:05", 20.05, 20.5, 19.7, 20.4)))  # gap leaves the body uncovered
        self.assertFalse(deck.is_bull_engulfing(bear, b("10:05", 19.9, 20.5, 19.7, 20.1)))  # wicks cover, the body does not

    def test_shooting_star_is_the_mirror_and_not_a_buy(self):
        star = b("10:15", 20.10, 21.20, 20.00, 20.05)
        self.assertTrue(deck.is_shooting_star(star))
        self.assertFalse(deck.is_hammer(star))
        self.assertFalse(deck.is_shooting_star(b("10:15", 20.40, 20.50, 19.40, 20.45)))

    def test_inside_bar_is_strict_and_nr7_is_the_narrowest_of_seven(self):
        mother, ins = b("10:00", 30.2, 31.0, 30.0, 30.8), b("10:05", 30.5, 30.7, 30.3, 30.4)
        self.assertTrue(deck.is_inside(mother, ins))
        self.assertFalse(deck.is_inside(mother, b("10:05", 30.5, 31.0, 30.3, 30.6)))  # equal high is outside
        bars = [b(f"10:{m:02d}", 10, 10.5, 9.5, 10.2) for m in range(0, 30, 5)]
        bars.append(b("10:30", 10.1, 10.2, 10.0, 10.15))  # range 0.2, the others are 1.0
        self.assertTrue(deck.is_nr7(bars))
        bars[-2]["h"], bars[-2]["l"] = 10.15, 10.0  # a tie for the narrowest
        self.assertFalse(deck.is_nr7(bars))


class Location(unittest.TestCase):
    def _decline(self):
        return [b("09:30", 21.2, 21.3, 20.8, 20.9), b("09:35", 20.9, 21.0, 20.3, 20.4),
                b("09:40", 20.4, 20.5, 19.9, 20.0)]

    def test_hammer_after_a_decline_on_support_and_not_in_chop(self):
        hammer = b("09:45", 20.40, 20.50, 19.40, 20.45)
        bars = self._decline() + [hammer]
        got = deck.signals(bars, ctx(prev_low=19.50))
        self.assertIn("hammer", [s["setup"] for s in got])
        hit = next(s for s in got if s["setup"] == "hammer")
        self.assertTrue(hit["spring"])  # low 19.40 pierced 19.50 and the close came back above
        self.assertEqual(deck.geometry(hit["trigger_high"], hit["stop_anchor"])["entry"], 20.51)
        self.assertEqual(deck.geometry(hit["trigger_high"], hit["stop_anchor"])["stop"], 19.39)
        # slide 18, graph B: the same shape with no decline and no level is noise
        chop = [b("09:30", 20.4, 20.5, 20.2, 20.45), b("09:35", 20.45, 20.55, 20.3, 20.5),
                b("09:40", 20.5, 20.6, 20.35, 20.55), hammer]
        self.assertNotIn("hammer", names(chop, prev_low=19.50))
        self.assertNotIn("hammer", names(bars, prev_low=10.0))  # decline, but no level near the wick

    def test_engulfing_stop_is_the_lower_of_the_two_lows(self):
        bars = [b("09:30", 21.0, 21.1, 20.6, 20.7), b("09:35", 20.7, 20.8, 20.2, 20.3),
                b("09:40", 20.2, 20.25, 19.90, 20.0), b("09:45", 19.95, 20.40, 19.85, 20.30)]
        hit = next(s for s in deck.signals(bars, ctx(prev_low=19.85)) if s["setup"] == "engulf")
        self.assertEqual(hit["stop_anchor"], 19.85)
        self.assertEqual(hit["trigger_high"], 20.40)
        self.assertNotIn("engulf", names(bars[:-1] + [b("09:45", 20.05, 20.5, 19.8, 20.4)], prev_low=19.85))


class InsideAndBreakout(unittest.TestCase):
    def test_slide_24_orders_and_only_the_regular_one_is_alerted(self):
        orders = deck.inside_orders(31.00, 30.00, 30.70, 30.30)
        self.assertEqual(orders["regular"], {"entry": 31.01, "stop": 29.99, "R": 1.02, "target": 33.05, "limit": 31.06})
        self.assertEqual((orders["aggressive"]["entry"], orders["aggressive"]["stop"], orders["aggressive"]["R"]),
                         (30.71, 30.29, 0.42))
        self.assertAlmostEqual(orders["regular"]["R"] / orders["aggressive"]["R"], 2.4, places=1)
        bars = [b("09:30", 20.15, 20.2, 19.9, 20.0), b("09:35", 20.0, 20.4, 20.0, 20.3),
                b("09:40", 20.3, 20.8, 20.2, 20.7), b("09:45", 20.7, 21.0, 20.5, 20.9),
                b("09:50", 20.85, 20.95, 20.6, 20.8)]
        hit = next(s for s in deck.signals(bars, ctx()) if s["setup"] == "inside")
        self.assertEqual((hit["trigger_high"], hit["stop_anchor"]), (21.0, 20.5))  # the mother, not the inside bar
        down = [b("09:30", 21.5, 21.6, 21.2, 21.3), b("09:35", 21.3, 21.4, 20.9, 21.0),
                b("09:40", 21.0, 21.1, 20.6, 20.7), b("09:45", 20.7, 21.0, 20.5, 20.6),
                b("09:50", 20.6, 20.9, 20.55, 20.7)]
        self.assertNotIn("inside", names(down))

    def test_nr7_is_not_a_ticket(self):
        bars = [b(ticket.hhmm(9 * 60 + 30 + 5 * i), 10, 11, 9, 10) for i in range(6)]
        bars.append(b("10:00", 10.1, 10.3, 10.0, 10.2))
        self.assertTrue(deck.is_nr7(bars))
        self.assertNotIn("nr7", names(bars))

    def _ceiling(self, last):
        prior = [b("09:30", 44.2, 44.6, 44.0, 44.4), b("09:35", 44.4, 44.8, 44.2, 44.5),
                 b("09:40", 44.5, 45.0, 44.3, 44.6), b("09:45", 44.6, 44.95, 44.5, 44.7),
                 b("09:50", 44.7, 45.0, 44.7, 44.8), b("09:55", 44.8, 45.0, 44.85, 44.9)]
        return prior + [last]

    def test_slide_32_false_breakout_does_not_alert_and_a_real_close_does(self):
        trap = b("10:00", 44.9, 45.30, 44.70, 44.90, 50)  # back under 45, long upper wick, light volume
        self.assertNotIn("breakout", names(self._ceiling(trap)))
        real = b("10:00", 44.9, 45.40, 44.95, 45.25, 250)
        hit = next(s for s in deck.signals(self._ceiling(real), ctx()) if s["setup"] == "breakout")
        order = deck.geometry(hit["trigger_high"], hit["stop_anchor"])
        self.assertEqual((order["entry"], order["stop"]), (45.01, 44.49))  # tick above 45, tick under the base
        self.assertEqual(order["target"], cents_target(order))
        quiet = b("10:00", 44.9, 45.40, 44.95, 45.25, 120)  # closed above, but not double the volume
        self.assertNotIn("breakout", names(self._ceiling(quiet)))


def cents_target(order):
    return deck.cents(order["entry"] + 2 * order["R"])


class VwapAndFlag(unittest.TestCase):
    def _vwap(self, start="09:45", vwap=20.50):
        return [b("09:30", 20.2, 20.6, 20.1, 20.5, 100, 20.0), b("09:35", 20.5, 20.9, 20.4, 20.8, 100, 20.2),
                b("09:40", 20.8, 21.0, 20.6, 20.9, 90, 20.4),
                b(start, 20.70, 20.80, 20.50, 20.75, 80, vwap)]

    def test_vwap_pullback_hammer_inside_the_window_only(self):
        hit = next(s for s in deck.signals(self._vwap(), ctx()) if s["setup"] == "vwap_hammer")
        order = deck.geometry(hit["trigger_high"], hit["stop_anchor"])
        self.assertEqual((order["entry"], order["stop"], order["R"], order["target"]), (20.81, 20.49, 0.32, 21.45))
        self.assertTrue(hit["band"])
        self.assertNotIn("vwap_hammer", names(self._vwap("15:00")))  # slide 36: 09:40–11:30 only
        below = self._vwap()
        for candle in below[:-1]:
            candle["c"] = candle["vwap"] - 0.2
        self.assertNotIn("vwap_hammer", names(below))
        built = deck.build(hit, atr=1.0, risk_dollars=100.0)
        self.assertTrue(built["ok"])
        far = dict(hit, trigger_high=20.10, stop_anchor=18.0)  # R far outside 1 x ATR
        self.assertEqual(deck.build(far, atr=1.0, risk_dollars=100.0)["reason"], "stop outside the volatility band")

    def test_slide_40_flag_and_the_rejects(self):
        bars = [b("09:30", 20.00, 21.00, 19.90, 20.90, 500), b("09:35", 20.90, 22.00, 20.80, 21.85, 600),
                b("09:40", 21.85, 21.90, 21.50, 21.60, 200), b("09:45", 21.60, 21.70, 21.40, 21.45, 100),
                b("09:50", 21.45, 21.95, 21.42, 21.88, 400)]
        hit = next(s for s in deck.signals(bars, ctx()) if s["setup"] == "flag")
        order = deck.geometry(hit["trigger_high"], hit["stop_anchor"])
        self.assertEqual((order["entry"], order["stop"], order["R"], order["target"]), (21.91, 21.39, 0.52, 22.95))
        deep = [dict(x) for x in bars]
        deep[2]["l"], deep[3]["l"] = 20.90, 20.80  # gives back more than half the pole
        self.assertNotIn("flag", names(deep))
        rising_vol = [dict(x) for x in bars]
        rising_vol[2]["v"], rising_vol[3]["v"] = 100, 200
        self.assertNotIn("flag", names(rising_vol))
        quiet = [dict(x) for x in bars]
        quiet[-1]["v"] = 50  # the breakout candle does not bring volume back
        self.assertNotIn("flag", names(quiet))
        half = [dict(x) for x in bars]
        half[2]["l"] = half[3]["l"] = 21.00  # (22 - 21) / (22 - 20) = exactly half, which is not "less than half"
        self.assertNotIn("flag", names(half))


class Bracket(unittest.TestCase):
    def test_slide_55_full_ticket(self):
        # hammer high 30.40, low 29.90, 1% of 2,500, limit 0.05 above the trigger
        setup = {"setup": "vwap_hammer", "trigger_high": 30.40, "stop_anchor": 29.90, "note": "x", "score": 5,
                 "candle_end": "10:00", "valid_mode": "next", "band": False}
        t = deck.build(setup, atr=1.0, risk_dollars=0, equity=2500)
        self.assertTrue(t["ok"])
        self.assertEqual((t["entry_stop"], t["stop_loss"], t["R"], t["shares"], t["target"], t["limit"]),
                         (30.41, 29.89, 0.52, 48, 31.45, 30.46))
        self.assertEqual(t["risk"], 24.96)  # 48 x 0.52, floored below the 25 budget
        self.assertAlmostEqual(t["shares"] * (t["limit"] - t["stop_loss"]), 27.36, places=2)
        self.assertEqual(t["valid_until"], "10:05")

    def test_cash_cap_cost_gate_and_expired_order(self):
        tight = {"setup": "flag", "trigger_high": 30.40, "stop_anchor": 30.22, "note": "x", "score": 4,
                 "candle_end": "10:00", "valid_mode": "window", "band": False}
        capped = deck.build(tight, atr=None, risk_dollars=0, equity=2500)  # R = 0.20, cash binds before 1% does
        self.assertEqual(capped["shares"], int(2500 / capped["limit"]))
        poor = {"setup": "hammer", "trigger_high": 30.40, "stop_anchor": 29.90, "note": "x", "score": 2,
                "candle_end": "10:00", "valid_mode": "next", "band": False}
        self.assertEqual(deck.build(poor, atr=1.0, risk_dollars=0, equity=100)["reason"], "commissions eat the risk")
        late = dict(poor, candle_end="11:30", valid_mode="window", setup="flag")
        self.assertEqual(deck.build(late, atr=1.0, risk_dollars=100)["reason"], "no time left on the order")
        self.assertEqual(deck.build(dict(poor, candle_end="11:25", valid_mode="window"), atr=1.0, risk_dollars=100)["ok"],
                         True)

    def test_shooting_star_on_resistance_is_a_caution_without_a_bracket(self):
        bars = [b("09:30", 20.0, 20.2, 19.9, 20.1), b("09:35", 20.1, 20.5, 20.0, 20.4),
                b("09:40", 20.4, 20.8, 20.3, 20.7), b("09:45", 20.10, 21.20, 20.00, 20.05)]
        self.assertNotIn("shooting_star", names(bars, prev_high=21.20))
        star = deck.shooting_star(bars, ctx(prev_high=21.20))
        self.assertTrue(star["warn"])
        self.assertIsNone(deck.shooting_star(bars, ctx(prev_high=30)))  # not on a level
        no_rise = [b("09:30", 20.5, 20.8, 20.2, 20.3), b("09:35", 20.3, 20.6, 20.1, 20.2),
                   b("09:40", 20.2, 20.4, 20.0, 20.1), bars[-1]]
        self.assertIsNone(deck.shooting_star(no_rise, ctx(prev_high=21.20)))

    def test_orb_and_red_to_green_are_not_deck_setups(self):
        bars = [b("09:30", 20, 20.5, 19.8, 20.3, 100, 20.1), b("09:35", 20.3, 20.45, 20.1, 20.4, 90, 20.2),
                b("09:40", 20.4, 20.8, 20.35, 20.7, 300, 20.3)]
        self.assertFalse(any(s["setup"].startswith("orb") or s["setup"] == "red_to_green" for s in deck.signals(bars, ctx())))


if __name__ == "__main__":
    unittest.main()
