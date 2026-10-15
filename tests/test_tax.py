"""The after-tax line. Losses offset inside the calendar year. No network."""
import unittest

from bot.tax import TAX, after_tax_terminal, annual_tax, spy_hold_tax


class Tax(unittest.TestCase):
    def test_the_rate_is_twenty_five_percent(self):
        self.assertEqual(TAX, 0.25)

    def test_a_loss_offsets_a_gain_inside_the_year_and_does_not_carry(self):
        same = annual_tax([("2020-03-01", 100.0), ("2020-11-01", -40.0)])
        self.assertEqual(same, [{"year": "2020", "net": 60.0, "tax": 15.0}])
        split = annual_tax([("2020-03-01", 100.0), ("2021-02-01", -40.0)])
        self.assertEqual(split, [
            {"year": "2020", "net": 100.0, "tax": 25.0},
            {"year": "2021", "net": -40.0, "tax": 0.0},
        ])
        after, tax, _years = after_tax_terminal(520.0, [("2020-03-01", 100.0), ("2021-02-01", -40.0)])
        self.assertEqual(tax, 25.0)
        self.assertEqual(after, 495.0)

    def test_a_losing_year_pays_nothing(self):
        self.assertEqual(annual_tax([("2022-06-01", -10.0)])[0]["tax"], 0.0)

    def test_spy_is_taxed_once_at_the_end(self):
        after, tax = spy_hold_tax(460.0, 560.0)
        self.assertEqual((tax, after), (25.0, 535.0))
        after, tax = spy_hold_tax(460.0, 400.0)
        self.assertEqual((tax, after), (0.0, 400.0))
