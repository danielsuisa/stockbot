"""Order-ticket rule engine: 5-minute bars, VWAP, confirmed swings, ATR, halt gaps."""
import unittest

from bot import ticket


def m(t, o=1.0, h=None, l=None, c=None, v=100, vw=None):
    c = o if c is None else c
    return (t, o, max(o, c) if h is None else h, min(o, c) if l is None else l, c, v, c if vw is None else vw)


def b5(*highs_lows):
    """5-minute bars from (high, low) pairs."""
    return [{"start": f"{10 + i // 12:02d}:{i % 12 * 5:02d}", "end": "", "o": lo, "h": hi, "l": lo, "c": hi, "v": 1,
             "vw": hi} for i, (hi, lo) in enumerate(highs_lows)]


class Blocks(unittest.TestCase):
    def test_to5_aggregates_existing_minutes_only(self):
        bars = [m("09:30", 2.0, h=2.2, l=1.9, c=2.1, v=100, vw=2.0), m("09:31", 2.1, h=2.5, c=2.4, v=300, vw=2.4),
                m("09:34", 2.4, l=1.8, c=2.3, v=100, vw=2.0), m("09:41", 3.0)]
        got = ticket.to5(bars)
        self.assertEqual([(b["start"], b["end"]) for b in got], [("09:30", "09:35"), ("09:40", "09:45")])
        b = got[0]
        self.assertEqual((b["o"], b["h"], b["l"], b["c"], b["v"]), (2.0, 2.5, 1.8, 2.3, 500))
        self.assertAlmostEqual(b["vw"], (2.0 * 100 + 2.4 * 300 + 2.0 * 100) / 500)

    def test_vwap_window(self):
        bars = [m("09:29", vw=100.0, v=1000), m("09:30", vw=2.0, v=100), m("09:31", vw=4.0, v=300),
                m("09:32", vw=50.0, v=1000)]
        self.assertAlmostEqual(ticket.vwap(bars, "09:30", "09:32"), (2.0 * 100 + 4.0 * 300) / 400)
        self.assertIsNone(ticket.vwap(bars, "10:00", "10:05"))

    def test_swing_confirmed_two_bars_later(self):
        bars = b5((1, 0.5), (2, 0.6), (3, 0.7), (5, 0.8), (4, 0.7), (3, 0.6), (2, 0.5))
        s = ticket.swings(bars)
        self.assertIn({"i": 3, "kind": "high", "price": 5, "known_at": 5}, s)
        self.assertIsNone(ticket.last_swing_high(s, 4))
        self.assertEqual(ticket.last_swing_high(s, 5), 5)

    def test_swing_needs_strictly_higher(self):
        bars = b5((1, 0.5), (2, 0.6), (5, 0.7), (5, 0.8), (4, 0.7), (3, 0.6))
        self.assertEqual([x for x in ticket.swings(bars) if x["kind"] == "high"], [])

    def test_higher_low_needs_a_rising_low(self):
        # swing lows: 1.0 at i=2 (known 4), 1.5 at i=6 (known 8), 1.2 at i=10 (known 12)
        lows = [3, 2, 1.0, 2, 3, 2, 1.5, 2, 3, 2, 1.2, 2, 3]
        s = ticket.swings(b5(*[(lo + 1, lo) for lo in lows]))
        self.assertEqual([x["price"] for x in s if x["kind"] == "low"], [1.0, 1.5, 1.2])
        self.assertIsNone(ticket.last_higher_low(s, 7))  # one swing low: nothing to be higher than
        self.assertEqual(ticket.last_higher_low(s, 8), 1.5)
        self.assertEqual(ticket.last_higher_low(s, 12), 1.5)  # 1.2 is a lower low: the last higher low stays 1.5

    def test_atr14_uses_days_before(self):
        days = [(f"2024-01-{d:02d}", 10, 11, 9, 10, 1) for d in range(1, 16)]  # range 2 each day
        days.append(("2024-01-16", 10, 30, 1, 10, 1))  # the day itself: ignored
        self.assertEqual(ticket.atr14(days, "2024-01-16"), 2)
        gap = days[:14] + [("2024-01-15", 14, 15, 13, 14, 1)]  # gap up: true range 15 - 10 = 5
        self.assertAlmostEqual(ticket.atr14(gap, "2024-01-16"), (13 * 2 + 5) / 14)
        self.assertIsNone(ticket.atr14(days[:13], "2024-01-16"))

    def test_halt_gap_in_session_only(self):
        bars = [m(f"10:{x:02d}") for x in range(0, 3)] + [m(f"10:{x:02d}") for x in range(8, 30)]
        self.assertTrue(ticket.halted(bars, "10:15", "09:30", "16:00"))
        self.assertFalse(ticket.halted(bars, "10:30", "09:30", "16:00"))
        pre = [m(f"06:{x:02d}") for x in (0, 1, 2, 8, 9)]
        self.assertFalse(ticket.halted(pre, "06:15", "09:30", "16:00"))

    def test_halt_edges(self):
        # 4 missing minutes (10:03-10:06) is trading, 5 (10:03-10:07) is a halt
        open_ = [m(f"09:{x:02d}") for x in range(30, 60)]
        four = open_ + [m(f"10:{x:02d}") for x in (0, 1, 2, 7, 8, 9)]
        five = open_ + [m(f"10:{x:02d}") for x in (0, 1, 2, 8, 9)]
        self.assertFalse(ticket.halted(four, "10:10", "09:30", "16:00"))
        self.assertTrue(ticket.halted(five, "10:10", "09:30", "16:00"))
        # a halt still running at `at`: no bars since 10:04
        now = open_ + [m(f"10:{x:02d}") for x in range(0, 5)]
        self.assertTrue(ticket.halted(now, "10:10", "09:30", "16:00"))
        # a late first trade after the open counts like a halt (a delayed open)
        self.assertTrue(ticket.halted([m("09:40"), m("09:41")], "09:42", "09:30", "16:00"))


# ---------- the two rule sets ----------
def day(bars5, start="09:30", drop=(), vw=None):
    """5-minute bars (h, l, c, v) from `start`, each as 5 one-minute bars: the first opens at the previous close
    (clipped into the bar) and carries the high and low, the other four sit at the close; vw {bar index: vw}."""
    out, prev, t = [], None, ticket.mins(start)
    for i, (h, l, c, v) in enumerate(bars5):
        o = min(max(c if prev is None else prev, l), h)
        w = (vw or {}).get(i)
        for j in range(5):
            mm = ticket.hhmm(t + j)
            if mm not in drop:
                out.append((mm, o, h, l, c, v / 5, w or c) if j == 0 else (mm, c, c, c, c, v / 5, w or c))
        prev, t = c, t + 5
    return out


BREAK = (10.20, 9.98, 10.10, 5000)
# opening range high 10.00 (09:40 bar); swing lows 9.50 (09:50 bar) and 9.85 (10:05 bar, a higher low, known at the
# 10:15 close); a swing high 9.98 below the range high; the 10:20 bar breaks out and closes at 10:25
BASE = [(9.80, 9.40, 9.70, 1000), (9.90, 9.60, 9.85, 1000), (10.00, 9.80, 9.90, 1000), (9.95, 9.70, 9.75, 1000),
        (9.90, 9.50, 9.60, 1000), (9.95, 9.90, 9.92, 1000), (9.98, 9.92, 9.95, 1000), (9.95, 9.85, 9.90, 1000),
        (9.97, 9.90, 9.95, 1000), (9.99, 9.95, 9.97, 1000), BREAK]
FILL = (9.99, 9.95, 9.97, 1000)
# pre-market: flat highs 5.00, swing lows 4.80 then 4.86 (higher), the 04:55 bar breaks out to 5.30
PRE = [(5.00, lo, 4.95, 1000) for lo in (4.90, 4.85, 4.80, 4.85, 4.90, 4.88, 4.86, 4.88, 4.90, 4.92, 4.95)] + \
      [(5.30, 4.96, 5.20, 5000)]


def ctx(from_="09:30", **kw):
    return {"day": "2024-03-01", "open": "09:30", "close": "16:00", "atr": 2.0, "open_rel_vol": 1.5, "from": from_,
            **kw}


def sig(rules, session, bars, spread=0.02, **kw):
    return ticket.signal(rules, session, bars, ctx(**kw), lambda at: spread)


def with_bar(i, bar, base=BASE):
    return base[:i] + [bar] + base[i + 1:]


class Original(unittest.TestCase):
    def test_original_regular_ticket_values(self):
        t = sig("ORIGINAL", "REGULAR", day(BASE))
        self.assertTrue(t["ok"], t)
        self.assertEqual((t["at"], t["level"], t["entry_type"], t["entry"], t["limit"], t["stop"], t["R"], t["shares"]),
                         ("10:25", 10.00, "LMT", 10.05, 10.05, 9.75, 0.30, 333))
        self.assertEqual(t["legs"], [{"qty": 166, "target": 10.65, "exit_at": None},
                                     {"qty": 167, "target": None, "exit_at": None}])
        self.assertEqual((t["valid_until"], t["spread"], t["rules"], t["session"]), ("16:00", 0.02, "ORIGINAL", "REGULAR"))

    def test_original_min_stop_distance(self):
        tight = BASE[:5] + [(10.00, 9.99, 10.00, 1000), (10.00, 9.995, 10.00, 1000), (10.00, 9.98, 9.99, 1000),
                            (10.00, 9.99, 10.00, 1000), (10.00, 9.995, 10.00, 1000), (10.20, 9.99, 10.10, 5000)]
        t = sig("ORIGINAL", "REGULAR", day(tight))
        self.assertEqual((t["stop"], t["R"], t["shares"]), (9.85, 0.20, 500))  # 9.98 - 0.10 = 9.88 is too close

    def test_original_each_check_fails_alone(self):
        cases = {"spread": (day(BASE), 0.06),
                 "trigger": (day(with_bar(10, (10.20, 9.98, 10.00, 5000))), 0.02),
                 "volume": (day(with_bar(10, (10.20, 9.98, 10.10, 1990))), 0.02),
                 "vwap": (day(BASE, vw={0: 30.0}), 0.02),
                 "halt": (day(BASE, drop=("10:11", "10:12", "10:13", "10:14", "10:15")), 0.02),
                 "no higher low": (day(with_bar(7, (9.95, 9.45, 9.90, 1000))), 0.02),
                 "size below minimum": (day(with_bar(7, (9.95, 0.10, 9.90, 1000), with_bar(4, (9.90, 0.05, 9.60, 1000)))),
                                        0.02)}
        for reason, (bars, spread) in cases.items():
            with self.subTest(reason):
                t = sig("ORIGINAL", "REGULAR", bars, spread)
                self.assertEqual((t["ok"], t["reason"]), (False, reason))
        self.assertEqual(sig("ORIGINAL", "REGULAR", day(BASE), 0.05)["ok"], True)  # the spread bound is inclusive
        self.assertEqual(sig("ORIGINAL", "REGULAR", day(BASE), None)["reason"], "spread")  # no quote

    def test_spread_is_read_only_at_a_signal(self):
        asked = []
        t = ticket.signal("ORIGINAL", "REGULAR", day(BASE), ctx(), lambda at: asked.append(at) or 0.02)
        self.assertTrue(t["ok"])
        self.assertEqual(asked, ["10:25"])

    def test_original_premarket_level_excludes_trigger_bar(self):
        t = sig("ORIGINAL", "PREMARKET", day(PRE, start="04:00"), from_="04:00")
        self.assertTrue(t["ok"], t)
        self.assertEqual((t["at"], t["level"], t["entry"], t["stop"], t["R"], t["shares"]),
                         ("05:00", 5.00, 5.05, 4.76, 0.29, 344))
        self.assertEqual(t["legs"][0], {"qty": 172, "target": 5.63, "exit_at": None})

    def test_original_no_entry_after_cutoff_and_early_close(self):
        at_noon = BASE[:10] + [FILL] * 19 + [BREAK]  # the breakout bar closes at 12:00
        after = BASE[:10] + [FILL] * 20 + [BREAK]  # ... at 12:05
        self.assertEqual(sig("ORIGINAL", "REGULAR", day(at_noon), close="13:00")["at"], "12:00")
        self.assertFalse(sig("ORIGINAL", "REGULAR", day(after), close="13:00")["ok"])
        self.assertEqual(sig("ORIGINAL", "REGULAR", day(after))["at"], "12:05")
        self.assertEqual(sig("ORIGINAL", "REGULAR", day(BASE[:10] + [FILL] * 55 + [BREAK]))["at"], "15:00")
        late = BASE[:10] + [FILL] * 56 + [BREAK]  # closes at 15:05: after the 15:00 cutoff
        self.assertFalse(sig("ORIGINAL", "REGULAR", day(late))["ok"])

    def test_eligibility_minute_blocks_earlier_signals(self):
        t = sig("ORIGINAL", "REGULAR", day(BASE), from_="11:00")
        self.assertEqual((t["ok"], t["reason"]), (False, "no signal"))
        self.assertTrue(sig("ORIGINAL", "REGULAR", day(BASE), from_="10:24")["ok"])  # eligible once 10:24 closed
        self.assertFalse(sig("ORIGINAL", "REGULAR", day(BASE), from_="10:25")["ok"])
        self.assertFalse(sig("IMPROVED", "REGULAR", improved_day(), from_="11:00")["ok"])


def improved_day():
    """Opening range high 10.00 at 09:32; quiet until the 09:40 minute breaks out (opens 9.98, high 10.10)."""
    rows = [("09:30", 9.80, 9.90, 9.75, 9.85), ("09:31", 9.85, 9.95, 9.80, 9.90), ("09:32", 9.90, 10.00, 9.85, 9.95),
            ("09:33", 9.95, 9.97, 9.90, 9.92), ("09:34", 9.92, 9.96, 9.90, 9.95)]
    rows += [(f"09:{x}", 9.95, 9.98, 9.93, 9.96) for x in range(35, 40)]
    rows += [("09:40", 9.98, 10.10, 9.97, 10.05), ("09:41", 10.05, 10.12, 10.00, 10.10)]
    return [(t, o, h, l, c, 2000, c) for t, o, h, l, c in rows]


class Improved(unittest.TestCase):
    def test_improved_regular_ticket_values(self):
        t = sig("IMPROVED", "REGULAR", improved_day(), 0.02)
        self.assertTrue(t["ok"], t)
        self.assertEqual((t["at"], t["level"], t["entry_type"], t["entry"], t["limit"], t["stop"], t["R"], t["shares"]),
                         ("09:40", 10.00, "STP_LMT", 10.00, 10.20, 9.80, 0.20, 454))  # floor(100 / (0.20 + 0.02))
        self.assertEqual(t["legs"], [{"qty": 227, "target": 10.40, "exit_at": "15:55"},
                                     {"qty": 227, "target": None, "exit_at": "15:55"}])
        self.assertEqual(t["valid_until"], "10:35")
        self.assertEqual(sig("IMPROVED", "REGULAR", improved_day(), 0.02, close="13:00")["legs"][1]["exit_at"], "12:55")

    def test_improved_opening_volume_and_relative_spread(self):
        bars = improved_day()
        self.assertEqual(sig("IMPROVED", "REGULAR", bars, open_rel_vol=0.99)["reason"], "opening volume")
        self.assertEqual(sig("IMPROVED", "REGULAR", bars, open_rel_vol=None)["reason"], "opening volume")
        self.assertTrue(sig("IMPROVED", "REGULAR", bars, open_rel_vol=1.0)["ok"])
        self.assertEqual(sig("IMPROVED", "REGULAR", bars, atr=None)["reason"], "no atr")
        # 1 % of the price binds when 0.1 x D is wider (atr 20 -> D 2.00, 0.1 x D = 0.20; 1 % of 10.00 = 0.10)
        self.assertEqual(sig("IMPROVED", "REGULAR", bars, 0.11, atr=20.0)["reason"], "spread")
        self.assertEqual(sig("IMPROVED", "REGULAR", bars, 0.10, atr=20.0)["shares"], 47)  # floor(100 / 2.10)
        # 0.1 x D binds when tighter (atr 2 -> 0.02)
        self.assertEqual(sig("IMPROVED", "REGULAR", bars, 0.021)["reason"], "spread")
        self.assertEqual(sig("IMPROVED", "REGULAR", bars, None)["reason"], "spread")

    def test_improved_checks_at_the_trigger(self):
        bars = improved_day()
        expensive = [b if b[0] >= "09:35" else b[:6] + (30.0,) for b in bars]  # VWAP far above the price
        self.assertEqual(sig("IMPROVED", "REGULAR", expensive)["reason"], "vwap")
        self.assertEqual(sig("IMPROVED", "REGULAR", bars, atr=2000.0)["reason"], "size below minimum")
        quiet = [b for b in bars if b[0] < "09:40"]
        self.assertEqual(sig("IMPROVED", "REGULAR", quiet)["reason"], "trigger")
        late = bars[:10] + [(f"{h:02d}:{x:02d}", 9.95, 9.98, 9.93, 9.96, 2000, 9.96) for h in (9, 10)
                            for x in range(0, 60) if "09:40" <= f"{h:02d}:{x:02d}" < "10:35"] + \
            [("10:35", 9.98, 10.10, 9.97, 10.05, 2000, 10.05)]
        self.assertEqual(sig("IMPROVED", "REGULAR", late)["reason"], "trigger")  # the order expired at 10:35

    def test_improved_premarket_level_is_the_high_before_the_trigger(self):
        bars = [(f"04:{x:02d}", 4.95, 5.00 if x == 30 else 4.98, 4.90, 4.95, 1000, 4.95) for x in range(60)]
        bars.append(("05:00", 4.99, 5.10, 4.98, 5.05, 1000, 5.05))
        t = sig("IMPROVED", "PREMARKET", bars, 0.01, atr=1.0, open_rel_vol=None, from_="04:00")
        self.assertTrue(t["ok"], t)
        self.assertEqual((t["at"], t["level"], t["limit"], t["stop"], t["shares"], t["valid_until"]),
                         ("05:00", 5.00, 5.10, 4.90, 909, "09:25"))


if __name__ == "__main__":
    unittest.main()
