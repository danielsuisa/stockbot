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


if __name__ == "__main__":
    unittest.main()
