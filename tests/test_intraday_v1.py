"""The frozen pre-deck detectors (bot/intraday_v1.py). The published signal count still replays these."""
import unittest

from bot import intraday_v1 as v1
from test_intraday_screener import ATR, PREV, c


class Frozen(unittest.TestCase):
    def test_hammer_and_engulfing_shapes(self):
        self.assertTrue(v1.is_hammer(c("10:00", 20.0, 20.1, 19.5, 20.05, 1)))
        self.assertFalse(v1.is_hammer(c("10:00", 20.0, 20.5, 19.9, 20.05, 1)))
        prev, b = c("10:00", 20.2, 20.25, 19.9, 20.0, 1), c("10:05", 19.95, 20.4, 19.9, 20.3, 1)
        self.assertTrue(v1.is_bull_engulfing(prev, b))
        self.assertFalse(v1.is_bull_engulfing(b, prev))

    def test_orb_vwap_flag_and_red_to_green(self):
        b5 = [c("09:30", 20, 20.5, 19.8, 20.3, 100), c("09:35", 20.3, 20.45, 20.1, 20.4, 90),
              c("09:40", 20.4, 20.8, 20.35, 20.7, 200)]
        self.assertEqual(v1.orb(b5, 5)["setup"], "orb5")
        b5.append(c("09:45", 20.7, 21.0, 20.6, 20.9, 200))
        self.assertIsNone(v1.orb(b5, 5))
        flag = [c("09:30", 20, 20.2, 19.9, 20.1, 100), c("09:35", 20.1, 20.9, 20.05, 20.8, 300),
                c("09:40", 20.8, 20.85, 20.6, 20.65, 80), c("09:45", 20.65, 20.7, 20.55, 20.6, 60)]
        self.assertEqual(v1.bull_flag(flag, ATR)["setup"], "flag")
        red = [c("09:30", 19.7, 19.8, 19.5, 19.6, 100), c("09:35", 19.6, 19.9, 19.55, 19.85, 90),
               c("09:40", 19.85, 20.2, 19.8, 20.1, 250)]
        self.assertEqual(v1.red_to_green(red, PREV)["setup"], "red_to_green")
        self.assertIsNone(v1.red_to_green([c("09:30", 20.1, 20.3, 20, 20.2, 1)] + red[1:], PREV))


if __name__ == "__main__":
    unittest.main()
