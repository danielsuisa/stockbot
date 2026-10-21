"""The retired v1 setups (bot/intraday_v1.py): frozen, because bot/intraday_count.py replays them for its 2024 count."""
import unittest

from bot import intraday, intraday_v1 as v1
from test_intraday_screener import ATR, PREV, c, minutes


class Shapes(unittest.TestCase):
    def test_hammer(self):
        self.assertTrue(v1.is_hammer(c("10:00", 20.0, 20.1, 19.5, 20.05, 1)))  # long lower wick, close near high
        self.assertFalse(v1.is_hammer(c("10:00", 20.0, 20.5, 19.9, 20.05, 1)))  # upper wick: not a hammer

    def test_engulfing(self):
        prev, b = c("10:00", 20.2, 20.25, 19.9, 20.0, 1), c("10:05", 19.95, 20.4, 19.9, 20.3, 1)
        self.assertTrue(v1.is_bull_engulfing(prev, b))
        self.assertFalse(v1.is_bull_engulfing(b, prev))


class Setups(unittest.TestCase):
    def test_orb5_first_close_above_range(self):
        b5 = [c("09:30", 20, 20.5, 19.8, 20.3, 100), c("09:35", 20.3, 20.45, 20.1, 20.4, 90),
              c("09:40", 20.4, 20.8, 20.35, 20.7, 200)]  # breaks 20.5 on 2x volume
        s = v1.orb(b5, 5)
        self.assertEqual(s["setup"], "orb5")
        self.assertEqual(s["trigger_high"], 20.8)
        self.assertIsNone(s["stop_ref"])  # the 10% ATR stop
        b5.append(c("09:45", 20.7, 21.0, 20.6, 20.9, 200))
        self.assertIsNone(v1.orb(b5, 5))  # the second candle above the range is a chase

    def test_orb_needs_volume_and_vwap(self):
        b5 = [c("09:30", 20, 20.5, 19.8, 20.3, 100), c("09:35", 20.3, 20.45, 20.1, 20.4, 90),
              c("09:40", 20.4, 20.8, 20.35, 20.7, 50)]
        self.assertIsNone(v1.orb(b5, 5))
        b5[-1]["v"], b5[-1]["vwap"] = 300, 20.9
        self.assertIsNone(v1.orb(b5, 5))

    def test_vwap_hammer(self):
        b5 = [c("09:30", 20, 20.5, 19.9, 20.4, 100, 20.1), c("09:35", 20.4, 20.6, 20.3, 20.5, 90, 20.2),
              c("09:40", 20.5, 20.55, 20.3, 20.45, 80, 20.3), c("09:45", 20.45, 20.5, 20.0, 20.47, 120, 20.33)]
        s = v1.vwap_pullback(b5)
        self.assertEqual(s["setup"], "vwap_hammer")
        self.assertEqual((s["trigger_high"], s["stop_ref"]), (20.5, 20.0))

    def test_bull_flag(self):
        b5 = [c("09:30", 20, 20.2, 19.9, 20.1, 100), c("09:35", 20.1, 20.9, 20.05, 20.8, 300),  # impulse +0.8 ATR
              c("09:40", 20.8, 20.85, 20.6, 20.65, 80), c("09:45", 20.65, 20.7, 20.55, 20.6, 60)]  # small, quiet
        s = v1.bull_flag(b5, ATR)
        self.assertEqual(s["setup"], "flag")
        self.assertEqual((s["trigger_high"], s["stop_ref"]), (20.9, 20.55))
        deep = b5[:2] + [c("09:40", 20.8, 20.85, 20.2, 20.3, 80), c("09:45", 20.3, 20.4, 20.2, 20.35, 60)]
        self.assertIsNone(v1.bull_flag(deep, ATR))  # gave back more than half

    def test_red_to_green(self):
        b5 = [c("09:30", 19.7, 19.8, 19.5, 19.6, 100), c("09:35", 19.6, 19.9, 19.55, 19.85, 90),
              c("09:40", 19.85, 20.2, 19.8, 20.1, 250)]
        s = v1.red_to_green(b5, PREV)
        self.assertEqual(s["setup"], "red_to_green")
        self.assertEqual(s["stop_ref"], 19.5)
        self.assertIsNone(v1.red_to_green([c("09:30", 20.1, 20.3, 20, 20.2, 1)] + b5[1:], PREV))

    def test_candles_only_completed(self):
        bars = minutes("09:30", [(20, 20.5, 19.8, 20.3, 100), (20.3, 20.45, 20.1, 20.4, 90)])
        self.assertEqual([b["end"] for b in v1.candles(bars, "09:37")], ["09:35"])
        self.assertEqual(len(v1.candles(bars, "09:40")), 2)
        self.assertIn("vwap", v1.candles(bars, "09:40")[0])

    def test_the_count_tool_still_sees_the_v1_names(self):
        self.assertIs(intraday.candles, v1.candles)
        self.assertIs(intraday.setups, v1.setups)
        self.assertEqual((intraday.LATE_FROM, intraday.STEP, intraday.TICK), ("15:00", 5, 0.01))


if __name__ == "__main__":
    unittest.main()
