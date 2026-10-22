"""The deck screener's rules (bot/deck.py, docs/deck-screener-spec.md). Each setup has a fixture of 5-minute candles
shaped like its slide's drawing (c2-entry, c2-engulf, c3-inside, c3-nr7, c4-breakout, c4-flip, c5-pullback, c5-flag),
with the deck's own numbers wherever a slide gives them (c3-q, c5-q, c7-q, c7-size, c7-r); each asserts the exact
trigger, stop, target, risk per share and shares. Then one negative fixture per checklist item: one "no" blocks."""
import re
import unittest
from pathlib import Path
from unittest import mock

from bot import deck, ticket

SPEC = Path(__file__).resolve().parents[1] / "docs" / "deck-screener-spec.md"
# The zigzag that makes an uptrend: two swing highs and two swing lows, both rising (slide c1-trend), as (low, high)
# offsets from a base price; each candle closes in the upper part of its range.
ZIG = [(-0.20, 0.00), (-0.10, 0.20), (0.10, 0.50), (-0.05, 0.30), (-0.15, 0.10), (0.00, 0.40), (0.20, 0.80),
       (0.30, 0.60), (0.10, 0.50), (0.25, 0.70), (0.40, 0.90)]


def C(start, o, h, l, c, v=1000, vwap=None, n=5):
    return {"start": start, "end": ticket.hhmm(ticket.mins(start) + 5), "o": o, "h": h, "l": l, "c": c, "v": v,
            "n": n, "usable": n >= (deck.PRE_MIN_TRADES if start < deck.OPEN else 1), "vwap": vwap}


def run(start, specs, vwap=None):
    """(o, h, l, c[, v]) specs -> consecutive candles from `start`; regular-session ones get `vwap`."""
    out, t = [], ticket.mins(start)
    for s in specs:
        hh = ticket.hhmm(t)
        out.append(C(hh, *s, vwap=vwap if hh >= deck.OPEN else None))
        t += 5
    return out


def zig(start, base, vwap=None):
    return run(start, [(round(base + lo + 0.3 * (hi - lo), 2), round(base + hi, 2), round(base + lo, 2),
                        round(base + lo + 0.7 * (hi - lo), 2)) for lo, hi in ZIG], vwap)


def ctx(prev=None, atr=1.0, spread=0.01, **kw):
    return {"atr": atr, "prev": prev, "yday": [], "spread": spread, "time_exit": "15:55", "daily": [], **kw}


def tickets(cs, cx, now):
    return {t["setup"]: t for t in deck.evaluate("ACME", cs, cx, now)["tickets"]}


def numbers(t):
    return t["entry_stop"], t["stop_loss"], t["target"], t["R"], t["shares"], t["limit"]


# ---------- the fixtures ----------
def hammer_case():
    """c2-entry: a decline, then a Hammer on support (yesterday's low 49.80); c7-size / c7-r's 50.20 / 49.60."""
    cs = run("09:30", [(51.80, 51.90, 51.40, 51.50), (51.50, 51.60, 51.10, 51.20), (51.20, 51.30, 50.80, 50.90),
                       (50.90, 50.95, 50.40, 50.50), (50.02, 50.19, 49.61, 50.15)], vwap=52.0)
    return cs, ctx({"h": 52.50, "l": 49.80, "c": 52.00}), "09:55"


def engulf_case():
    """c2-engulf: a decline, a small down candle and an up candle whose body covers it, on the round 40.00."""
    cs = run("09:30", [(41.95, 42.00, 41.60, 41.70), (41.70, 41.75, 41.30, 41.40), (41.40, 41.45, 40.95, 41.05),
                       (41.05, 41.10, 40.60, 40.70), (40.40, 40.45, 40.10, 40.20), (40.15, 40.60, 40.05, 40.50)],
             vwap=42.0)
    return cs, ctx({"h": 43.00, "l": 41.20, "c": 42.50}), "10:00"


def inside_case():
    """c3-inside with c3-q's numbers: mother bar 31.00 / 30.00, inside bar 30.70 / 30.30, after an uptrend."""
    cs = zig("08:50", 29.00, vwap=29.0) + run("09:45", [(30.05, 31.00, 30.00, 30.90), (30.40, 30.70, 30.30, 30.60)],
                                              vwap=29.0)
    return cs, ctx({"h": 29.60, "l": 28.50, "c": 29.40}, atr=1.20), "09:55"


def nr7_case():
    """c3-nr7: seven candles narrowing to the smallest range of the seven, on the round 25.00."""
    tri = [(24.50, 25.60, 24.40, 25.50), (25.40, 25.48, 24.52, 24.60), (24.65, 25.40, 24.60, 25.35),
           (25.30, 25.32, 24.68, 24.70), (24.80, 25.24, 24.76, 25.20), (25.15, 25.18, 24.82, 24.85),
           (24.95, 25.10, 24.90, 25.05)]
    cs = zig("08:30", 24.00) + run("09:25", tri, vwap=24.0)
    return cs, ctx({"h": 26.00, "l": 23.00, "c": 24.10}), "10:00"


def breakout_case():
    """c4-breakout: rising lows pressed under a flat ceiling at yesterday's high 50.00."""
    cs = zig("08:35", 47.00) + run("09:30", [(47.95, 48.70, 47.90, 48.60), (48.60, 49.40, 48.55, 49.30),
                                             (49.30, 49.90, 49.10, 49.60), (49.60, 49.96, 49.25, 49.70),
                                             (49.70, 49.80, 49.45, 49.55), (49.55, 49.93, 49.50, 49.85),
                                             (49.85, 49.97, 49.55, 49.92)], vwap=48.0)
    return cs, ctx({"h": 50.00, "l": 46.50, "c": 47.00}), "10:05"


def retest_case():
    """c4-flip: touches under 30.00, a close above it on 3x volume, a return to it from above and a Hammer there."""
    cs = zig("08:30", 28.50) + run("09:25", [(29.50, 29.95, 29.45, 29.80), (29.80, 29.98, 29.60, 29.70),
                                             (29.70, 29.85, 29.55, 29.75), (29.80, 30.50, 29.75, 30.40, 3000),
                                             (30.40, 30.70, 30.35, 30.60), (30.60, 30.65, 30.25, 30.30),
                                             (30.30, 30.40, 30.10, 30.20), (30.20, 30.30, 29.97, 30.27)], vwap=29.5)
    return cs, ctx({"h": 30.00, "l": 28.00, "c": 28.60}), "10:05"


def vwap_case():
    """c5-pullback with c7-q's numbers: above VWAP and rising, back to VWAP, a Hammer 30.40 / 29.90 on it."""
    cs = zig("08:45", 29.00, vwap=29.4)
    vw = [29.80, 29.90, 29.95, 29.98, 30.00]
    for c, (o, h, l, cl) in zip(vw, [(29.95, 30.60, 29.90, 30.55), (30.55, 30.80, 30.50, 30.70),
                                     (30.70, 30.72, 30.45, 30.50), (30.50, 30.55, 30.30, 30.35),
                                     (30.25, 30.40, 29.90, 30.36)]):
        cs.append(C(ticket.hhmm(ticket.mins(cs[-1]["start"]) + 5), o, h, l, cl, vwap=c))
    return cs, ctx({"h": 31.50, "l": 28.00, "c": 28.90}), "10:05"


def flag_case():
    """c5-flag, c5-flag-order with c5-q's numbers: a pole 20.00 -> 22.00 on volume, a flag 21.90 / 21.40 on less."""
    cs = zig("08:40", 19.00, vwap=20.5) + run("09:35", [
        (20.00, 20.70, 19.98, 20.65, 3000), (20.65, 21.35, 20.60, 21.30, 3000), (21.30, 22.00, 21.25, 21.95, 3000),
        (21.85, 21.90, 21.60, 21.65, 1500), (21.65, 21.80, 21.50, 21.55, 1200), (21.55, 21.75, 21.45, 21.50, 1000),
        (21.60, 21.70, 21.40, 21.45, 800)], vwap=20.5)
    return cs, ctx({"h": 21.50, "l": 18.50, "c": 19.20}), "10:10"


class Fixtures(unittest.TestCase):
    """trigger, stop, target, R, shares, limit (limit = trigger + 5% of ATR14)."""

    def check(self, case, setup, want):
        cs, cx, now = case()
        got = tickets(cs, cx, now)
        self.assertIn(setup, got, deck.evaluate("ACME", cs, cx, now))
        self.assertEqual(numbers(got[setup]), want)
        self.assertEqual(set(got[setup]["checks"]), set(deck.CHECKS))
        return got

    def test_hammer_c2_entry(self):
        got = self.check(hammer_case, "hammer", (50.20, 49.60, 51.40, 0.60, 41, 50.25))  # c7-size: 41 shares
        self.assertEqual(list(got), ["hammer"])
        self.assertEqual(got["hammer"]["level"], (49.80, "yday_low"))

    def test_engulfing_c2_engulf(self):
        got = self.check(engulf_case, "engulf", (40.61, 40.04, 41.75, 0.57, 43, 40.66))
        self.assertEqual(got["engulf"]["level"], (40.0, "round"))

    def test_inside_bar_c3_inside_regular_version(self):
        self.check(inside_case, "inside", (31.01, 29.99, 33.05, 1.02, 24, 31.07))  # c3-a: R = 1.02

    def test_nr7_c3_nr7_and_the_cash_cap(self):
        got = self.check(nr7_case, "nr7", (25.11, 24.89, 25.55, 0.22, 99, 25.16))  # 25 / 0.22 = 113 > 2,500 / 25.16
        self.assertIn("inside", got)  # c3-nr7: the narrowest candle is also an inside bar

    def test_breakout_c4_breakout(self):
        got = self.check(breakout_case, "breakout", (50.01, 49.44, 51.15, 0.57, 43, 50.06))
        self.assertEqual(got["breakout"]["level"][0], 50.0)

    def test_retest_c4_flip(self):
        got = self.check(retest_case, "retest", (30.31, 29.96, 31.01, 0.35, 71, 30.36))
        self.assertEqual(got["retest"]["level"], (30.0, "yday_high"))

    def test_vwap_pullback_c5_pullback_c7_answer(self):
        got = self.check(vwap_case, "vwap_hammer", (30.41, 29.89, 31.45, 0.52, 48, 30.46))  # c7-a, line by line
        self.assertEqual(got["vwap_hammer"]["level"], (30.0, "vwap"))

    def test_bull_flag_c5_flag_c5_answer(self):
        self.check(flag_case, "flag", (21.91, 21.39, 22.95, 0.52, 48, 21.96))  # c5-a: 21.91 / 21.39 / 22.95


def blocked(cs, cx, now):
    return deck.evaluate("ACME", cs, cx, now)["blocked"]


class Checklist(unittest.TestCase):
    """c7-checklist: one "no" is enough to skip the trade."""

    def test_1_context(self):
        cs, cx, now = inside_case()
        flat = run("08:50", [(29.40, 29.50, 29.30, 29.45)] * 11, vwap=29.0)  # no trend, no decline
        self.assertEqual(blocked(flat + cs[11:], cx, now), [("inside", "context", "אין מגמה עולה ואין סוף ירידה")])

    def test_2_level(self):
        cs, cx, now = inside_case()
        cs[-2:] = run("09:45", [(31.85, 32.80, 31.80, 32.70), (32.20, 32.50, 32.10, 32.40)], vwap=29.0)
        self.assertEqual([b[:2] for b in blocked(cs, cx, now)], [("inside", "level")])

    def test_3_shape_doji(self):
        cs, cx, now = nr7_case()
        cs[-1] = dict(cs[-1], o=25.00, c=25.00)  # the narrowest candle is a Doji: never an entry (c2-doji)
        self.assertEqual({b[:2] for b in blocked(cs, cx, now)}, {("inside", "shape"), ("nr7", "shape")})

    def test_3_shape_premarket_candle_quality(self):
        cs, cx, now = inside_case()
        cs = [dict(c, start=ticket.hhmm(ticket.mins(c["start"]) - 60), end=ticket.hhmm(ticket.mins(c["end"]) - 60),
                   vwap=None) for c in cs]  # the same candles an hour earlier: all pre-market
        self.assertIn("inside", tickets(cs, cx, "08:55"))
        cs[-1] = dict(cs[-1], n=2, usable=False)  # trades in only 2 of its 5 minutes
        self.assertEqual([b[:2] for b in blocked(cs, cx, "08:55")], [("inside", "shape")])

    def test_3_shape_unusable_hammer_is_blocked_not_a_crash(self):
        cs, cx, now = hammer_case()
        cs = [dict(c, start=ticket.hhmm(ticket.mins(c["start"]) - 60), end=ticket.hhmm(ticket.mins(c["end"]) - 60),
                   vwap=None) for c in cs]
        cs[-1] = dict(cs[-1], n=1, usable=False)  # replay 2026-10-16: a thin pre-market hammer
        cx = dict(cx, yday=hammer_case()[0] * 3)
        self.assertEqual([b[:2] for b in blocked(cs, cx, "08:55")], [("hammer", "shape")])
        cs[2] = dict(cs[2], l=51.15)  # no 3 lower lows: the ATR branch of "end of decline" runs (it crashed here)
        self.assertEqual(blocked(cs, cx, "08:55"), [])

    def test_4_levels(self):
        cs, cx, now = inside_case()
        stale = {"setup": "inside", "p": len(cs) - 2, "k": 2, "trigger": 30.50, "stop": 29.99, "level": None, "note": ""}
        with mock.patch.object(deck, "inside", return_value=stale):  # a trigger under the last close
            self.assertEqual([b[:2] for b in blocked(cs, cx, now)], [("inside", "levels")])

    def test_5_size(self):
        cs, cx, now = inside_case()
        cs = [dict(c, o=c["o"] * 13, h=c["h"] * 13, l=c["l"] * 13, c=c["c"] * 13) for c in cs]  # ~$400, R ~ $13
        cs[-2:] = run("09:45", [(380.5, 410.0, 380.0, 409.0), (395.0, 400.0, 390.0, 398.0)], vwap=377.0)
        cx = dict(cx, atr=40.0, prev={"h": 384.8, "l": 370.5, "c": 382.2})
        self.assertEqual([b[:2] for b in blocked(cs, cx, now)], [("inside", "size")])  # R 30.02 > $25: 0 shares

    def test_6_cost(self):
        cs, cx, now = hammer_case()
        self.assertEqual(blocked(cs, dict(cx, spread=0.30), now)[0][:2], ("hammer", "cost"))
        self.assertEqual(blocked(cs, dict(cx, spread=None), now), [("hammer", "cost", "אין ציטוט")])

    def test_7_time(self):
        cs, cx, now = hammer_case()
        self.assertEqual(blocked(cs, dict(cx, time_exit=None), now)[0][:2], ("hammer", "time"))
        self.assertEqual(blocked(cs, cx, "11:35"), [("hammer", "time", "08:00-11:30, valid to 10:00")])  # after 11:30

    def test_7_time_left_under_both_validity_rules(self):
        cs, cx, now = hammer_case()  # a reversal: valid until the next candle closes (09:55 signal -> 10:00)
        self.assertEqual(tickets(cs, cx, now)["hammer"]["valid_until"], "10:00")
        self.assertEqual(blocked(cs, cx, "10:00"), [("hammer", "time", "08:00-11:30, valid to 10:00")])  # no time left
        cs, cx, now = inside_case()  # a compression: valid until the window's end
        self.assertEqual(tickets(cs, cx, "11:25")["inside"]["valid_until"], "11:30")
        self.assertEqual(blocked(cs, cx, "11:30"), [("inside", "time", "08:00-11:30, valid to 11:30")])

    def test_8_fixed_rule(self):
        cs, cx, now = hammer_case()
        with mock.patch.object(deck, "COST_MAX_R", 0.5):  # a rule changed in the moment, not the one decided
            self.assertEqual(blocked(cs, cx, now)[0][:2], ("hammer", "rule"))


class Validity(unittest.TestCase):
    """User decision 2026-10-22: reversal setups live until the next candle closes (c2-hammer), the others to 11:30."""

    def test_each_setup_class(self):
        want = {hammer_case: ("hammer", "10:00"), engulf_case: ("engulf", "10:05"), vwap_case: ("vwap_hammer", "10:10"),
                retest_case: ("retest", "10:10"), inside_case: ("inside", "11:30"), nr7_case: ("nr7", "11:30"),
                breakout_case: ("breakout", "11:30"), flag_case: ("flag", "11:30")}
        for case, (setup, end) in want.items():
            cs, cx, now = case()
            self.assertEqual(tickets(cs, cx, now)[setup]["valid_until"], end, setup)
        self.assertEqual(set(deck.NEXT_CANDLE), {"hammer", "engulf", "vwap_hammer", "vwap_engulf", "retest"})
        self.assertEqual((deck.FIRST, deck.LAST), ("08:00", "11:30"))


class Filters(unittest.TestCase):
    def test_shooting_star_on_resistance_skips_the_symbol(self):
        cs, cx, now = inside_case()
        cs[-1] = dict(cs[-1], o=30.35, h=30.70, l=30.31, c=30.32)  # still an inside bar, shaped like a shooting star
        self.assertTrue(deck.is_shooting_star(cs[-1]))
        self.assertIn("inside", tickets(cs, cx, now))  # no resistance at its high: the inside bar stands
        res = deck.evaluate("ACME", cs, dict(cx, prev={"h": 30.72, "l": 28.5, "c": 29.4}), now)
        self.assertEqual((res["filter"], res["tickets"]), ("shooting star on resistance", []))

    def test_doji_shapes(self):
        self.assertTrue(deck.is_doji(C("10:00", 20.0, 20.5, 19.5, 20.02)))  # body 0.02 <= 5% of 1.00
        self.assertFalse(deck.is_doji(C("10:00", 20.0, 20.5, 19.5, 20.10)))


class Candles(unittest.TestCase):
    def test_premarket_quality_and_vwap_from_the_open(self):
        def bars(start, specs, traded):
            out, t = [], ticket.mins(start)
            for o, h, l, c, v in specs:
                out.append((ticket.hhmm(t), o, h, l, c, v, (o + c) / 2))
                out += [(ticket.hhmm(t + k), c, c, c, c, 1 if k < traded else 0, c) for k in range(1, 5)]
                t += 5
            return out
        b = bars("09:20", [(10, 10.2, 9.9, 10.1, 100)], 3) + bars("09:25", [(10.1, 10.3, 10.0, 10.2, 100)], 2) \
            + bars("09:30", [(10.2, 10.4, 10.1, 10.3, 100), (10.3, 10.5, 10.2, 10.4, 300)], 1)
        cs = deck.candles(b, "09:40")
        self.assertEqual([(c["start"], c["usable"]) for c in cs],
                         [("09:20", True), ("09:25", False), ("09:30", True), ("09:35", True)])
        self.assertEqual([c["vwap"] for c in cs[:2]], [None, None])  # c5-vwap: VWAP starts at 09:30
        self.assertAlmostEqual(cs[2]["vwap"], 10.25)  # the 09:30 candle alone: pre-market minutes not in it
        self.assertEqual(len(deck.candles(b, "09:39")), 3)  # only completed candles

    def test_missing_slot_breaks_a_pattern(self):
        b = [("09:30", 10, 10.2, 9.9, 10.1, 100, 10.0), ("09:40", 10, 10.1, 10.0, 10.05, 100, 10.0)]
        cs = deck.candles(b, "09:45")
        self.assertEqual([c["usable"] for c in cs], [True, False, True])
        self.assertIsNone(deck.inside(cs[1:]))

    def test_vwap_setups_wait_for_the_open(self):
        cs, cx, now = vwap_case()
        early = [dict(c, start=ticket.hhmm(ticket.mins(c["start"]) - 90), end=ticket.hhmm(ticket.mins(c["end"]) - 90),
                      vwap=None) for c in cs]
        self.assertNotIn("vwap_hammer", tickets(early, cx, "08:35"))
        self.assertIsNone(deck.vwap_pullback(early))


class Bracket(unittest.TestCase):
    PAT = {"setup": "flag", "p": 0, "k": 1, "trigger": 21.91, "stop": 21.39, "level": None, "note": ""}

    def test_floor_and_cap(self):
        b = deck.bracket(dict(self.PAT, stop=21.88), 1.0)  # 0.03 < 10% ATR: widened to 0.10
        self.assertEqual((b["R"], b["stop_loss"], b["r_floored"]), (0.10, 21.81, True))
        b = deck.bracket(dict(self.PAT, stop=20.00), 1.0)  # 1.91 > 1 ATR: capped
        self.assertEqual((b["R"], b["stop_loss"], b["r_capped"]), (1.0, 20.91, True))
        b = deck.bracket(dict(self.PAT, trigger=100.01, stop=100.00), 0.5)  # 0.3% of the price beats 10% ATR
        self.assertEqual(b["R"], 0.30)

    def test_cash_cap_and_premarket_stop_limit(self):
        b = deck.bracket(dict(self.PAT, trigger=200.01, stop=199.71), 2.0)  # R 0.60 by the 0.3% floor
        self.assertEqual((b["R"], b["limit"]), (0.60, 200.11))
        self.assertEqual(b["shares"], 12)  # 25 / 0.60 = 41, but 2,500 / 200.11 = 12
        self.assertEqual(b["stop_limit"], round(b["stop_loss"] - 0.10, 2))

    def test_cost_at_the_threshold(self):
        b = deck.bracket(self.PAT, 1.0)  # 48 shares, R 0.52 (c5-a)
        fees = deck.cost_usd(b, 0.0)
        self.assertAlmostEqual(fees, 0.70 + 2 * 48 * (0.0030 + 0.0002 + 0.000003) + 0.000735 * 0.70
                               + 0.0000206 * 48 * 22.95 + 0.000195 * 48)
        edge = (deck.COST_MAX_R * 48 * 0.52 - fees) / 48  # the spread that costs exactly 0.3 of the risk
        self.assertLessEqual(deck.cost_usd(b, edge - 0.001) / (48 * 0.52), deck.COST_MAX_R)
        self.assertGreater(deck.cost_usd(b, edge + 0.001) / (48 * 0.52), deck.COST_MAX_R)
        self.assertAlmostEqual(deck.commission(1000, 0.30), 3.0)  # the 1%-of-value maximum


class Wyckoff(unittest.TestCase):
    def test_accumulation_and_spring(self):
        fall = [("d", 30 - i * 0.15, 30 - i * 0.15 + 0.2, 30 - i * 0.15 - 0.2, 30 - i * 0.15, 1) for i in range(60)]
        box = [("d", 21.0, 22.0, 20.5, 21.5, 1) for _ in range(20)]
        self.assertEqual(deck.wyckoff(fall + box), ["accumulation"])
        spring = box[:-1] + [("d", 20.8, 21.2, 20.2, 20.9, 1)]  # under the box's 20.50, closed back inside
        self.assertEqual(deck.wyckoff(fall + spring), ["accumulation", "spring"])
        self.assertEqual(deck.wyckoff(box), [])


class Spec(unittest.TestCase):
    def test_spec_names_every_parameter_and_the_rule_set(self):
        text = SPEC.read_text(encoding="utf-8")
        self.assertEqual(deck.rules_id(), deck.RULES_ID)
        self.assertIn(f"`{deck.RULES_ID}`", text)
        rows = {m.group(1) for m in re.finditer(r"^\| `([A-Z_0-9]+)`", text, re.M)}
        params = {k for k, v in deck.rules().items() if isinstance(v, (int, float))}
        self.assertEqual(params - rows, set())


if __name__ == "__main__":
    unittest.main()
