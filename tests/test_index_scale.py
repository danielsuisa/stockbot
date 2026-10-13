"""Index program phase 2, task 4 (spec 6.2, 6.3, A4, C2, E4): growth, drawdown, calendar years and the scale search
on SYNTHETIC prices."""
import unittest

from bot import index_engine as ie

ALWAYS = [("0000-00-00", "open", True)]


class Measures(unittest.TestCase):
    def test_growth_by_hand(self):
        self.assertAlmostEqual(ie.growth_rate(10_000, 12_100, "2020-01-01", "2021-12-31"),
                               1.21 ** (365.25 / 730) - 1)
        self.assertAlmostEqual(ie.growth_rate(10_000, 12_100, "2020-01-01", "2022-01-01"), 1.21 ** (365.25 / 731) - 1)

    def test_drawdown_dates(self):
        days = [f"2020-01-{k:02d}" for k in range(1, 7)]
        depth, peak, trough = ie.max_drawdown(days, [100, 120, 90, 110, 80, 130])
        self.assertAlmostEqual(depth, 40 / 120)
        self.assertEqual((peak, trough), ("2020-01-02", "2020-01-05"))
        self.assertEqual(ie.max_drawdown(days[:3], [1, 2, 3]), (0.0, None, None))

    def test_drawdown_counts_a_fall_on_the_first_day(self):
        """6.3 (review finding 1): the account holds e0 at the close before the window, so a fall on the first day
        is a drawdown from it."""
        days = ["2020-01-02", "2020-01-03"]
        self.assertEqual(ie.max_drawdown(days, [5_000.0, 6_000.0], e0=10_000.0), (0.5, None, "2020-01-02"))

    def test_calendar_years(self):
        days = ["2020-06-01", "2020-12-31", "2021-03-01", "2021-12-31"]
        got = ie.calendar_year_returns(10_000, days, [10_500, 11_000, 9_000, 12_100])
        self.assertAlmostEqual(got["2020"], 0.10)
        self.assertAlmostEqual(got["2021"], 0.10)


class Scale(unittest.TestCase):
    def test_scale_search_stops_above_16pct(self):
        mdds = [s * 0.43 for s in ie.STEPS]  # 0.37 x 0.43 = 0.1591, 0.38 x 0.43 = 0.1634
        got = ie.scale_from(mdds)
        self.assertEqual((got["fitted"], got["scale"], got["excluded"]), (0.37, 0.37, False))

    def test_search_runs_to_1_and_the_cap_applies_after(self):
        got = ie.scale_from([0.10] * 100)
        self.assertEqual((got["fitted"], got["scale"]), (1.0, 0.60))  # A4: fitted 1.00, then the crash cap

    def test_excluded_at_0_01(self):
        got = ie.scale_from([0.17] * 100)
        self.assertEqual((got["fitted"], got["scale"], got["excluded"]), (None, None, True))

    def test_other_budgets_from_the_same_drawdowns(self):
        mdds = [s * 0.43 for s in ie.STEPS]
        self.assertEqual(ie.scale_from(mdds, budget=0.15)["fitted"], 0.34)  # 0.34 x 0.43 = 0.1462
        self.assertEqual(ie.scale_from(mdds, budget=0.17)["fitted"], 0.39)  # 0.39 x 0.43 = 0.1677

    def test_fit_scale_on_a_synthetic_fall(self):
        days = [f"2006-01-{k:02d}" for k in (2, 3, 4, 5, 6, 9)]  # the close before, then five trading days
        closes = [100.0, 100.0, 80.0, 60.0, 60.0, 70.0]  # a 40% fall of SPY between two closes in the window
        rows = [(d, c, c, c, c) for d, c in zip(days, closes)]
        cash = {d: 0.0 for d in days}
        got = ie.fit_scale(rows, cash, ALWAYS, "2006-01-03", "2006-01-09")
        # with s in SPY the account falls by s x 40% (cash flat, no costs at opens equal to closes but $0.70 +
        # 2 bp at the entry): the last s at or below 16% is 0.39 (0.40 x 0.40 = 16.0% plus costs is above 16%)
        self.assertEqual(got["fitted"], 0.39)
        self.assertEqual(len(got["mdds"]), 100)

    def test_scale_fixed_at_base_and_10000(self):
        days = ["2006-01-02", "2006-01-03", "2006-01-04"]
        rows = [(d, c, c, c, c) for d, c in zip(days, (100.0, 100.0, 90.0))]
        cash = {d: 0.0 for d in days}
        a = ie.fit_scale(rows, cash, ALWAYS, "2006-01-03", "2006-01-04")
        self.assertEqual(a["mdds"][9], ie.max_drawdown(*(lambda r: (r["days"], r["equity"]))(
            ie.run_window(rows, cash, ALWAYS, 0.10, ie.BASE, "2006-01-03", "2006-01-04")), 10_000.0)[0])


if __name__ == "__main__":
    unittest.main()
