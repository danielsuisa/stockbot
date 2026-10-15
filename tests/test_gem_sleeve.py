"""The GEM sleeve rule. No prices and no return."""
import unittest

from bot.gem import gem_sleeve


class Sleeve(unittest.TestCase):
    def test_spy_must_beat_bil_before_equities_are_held(self):
        self.assertEqual(gem_sleeve(0.10, 0.04, 0.12, "AGG"), "VEU")
        self.assertEqual(gem_sleeve(0.10, 0.04, 0.08, "AGG"), "SPY")
        self.assertEqual(gem_sleeve(0.03, 0.04, 0.20, "AGG"), "AGG")

    def test_a_tie_is_not_a_win(self):
        self.assertEqual(gem_sleeve(0.05, 0.05, 0.09, "AGG"), "AGG")
        self.assertEqual(gem_sleeve(0.08, 0.04, 0.08, "AGG"), "SPY")

    def test_gem_bil_changes_only_the_defensive_asset(self):
        self.assertEqual(gem_sleeve(0.03, 0.04, 0.20, "BIL"), "BIL")
        self.assertEqual(gem_sleeve(0.10, 0.04, 0.12, "BIL"), "VEU")

    def test_september_2026_month_end_selects_veu_for_both_books(self):
        spy = 762.6300048828 / 659.0695190430 - 1
        bil = 91.3769989014 / 88.1495819092 - 1
        veu = 82.9700012207 / 69.5550613403 - 1
        self.assertGreater(spy, bil)
        self.assertGreater(veu, spy)
        self.assertEqual(gem_sleeve(spy, bil, veu, "AGG"), "VEU")
        self.assertEqual(gem_sleeve(spy, bil, veu, "BIL"), "VEU")
