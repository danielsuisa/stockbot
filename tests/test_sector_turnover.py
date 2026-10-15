"""Turnover counting. No prices and no return."""
import unittest

from bot.sector_mom import single_fund_replacements
from bot.sector_turnover import GATE, closes, per_year


class Replacements(unittest.TestCase):
    def test_one_name_leaving_is_one_replacement(self):
        self.assertEqual(single_fund_replacements(("XLE", "XLF"), ("XLE", "XLK")), 1)

    def test_order_does_not_matter_and_a_hold_is_zero(self):
        self.assertEqual(single_fund_replacements(("XLK", "XLV"), ("XLV", "XLK")), 0)

    def test_cash_on_either_side_counts_both_names(self):
        self.assertEqual(single_fund_replacements(("XLE", "XLF"), ()), 2)
        self.assertEqual(single_fund_replacements((), ("XLE", "XLF")), 2)
        self.assertEqual(single_fund_replacements((), ()), 0)

    def test_the_rate_uses_the_span_of_the_steps(self):
        steps = [
            {"end": "2007-01-31", "replacements": 1},
            {"end": "2008-01-31", "replacements": 2},
        ]
        stats = per_year(steps)
        self.assertEqual(stats["total"], 3)
        self.assertAlmostEqual(stats["years"], 365 / 365.25)
        self.assertGreater(stats["per_year"], GATE)

    def test_above_two_a_year_closes_the_book(self):
        steps = [
            {"end": "2007-01-31", "replacements": 0},
            {"end": "2008-01-31", "replacements": 3},
        ]
        self.assertTrue(closes(steps))
        quiet = [
            {"end": "2007-01-31", "replacements": 0},
            {"end": "2008-01-31", "replacements": 1},
        ]
        self.assertFalse(closes(quiet))
