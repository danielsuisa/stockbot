"""IBKR Pro Fixed commission, the sample-size gate, and the T9 and T10 mechanics."""
import statistics
import unittest

from bot.faber_spy import faber_signals, month_ends, run_faber
from bot.queue_rules import (
    UNKNOWN, fixed_fee, queue_verdict, required_n, sample_unknown,
)
from bot.tom_exec import (
    buy_hold, held_sessions, quick_screen, run_tom_path, tom_windows, window_fee,
)


class FeeTests(unittest.TestCase):
    def test_spy_pays_the_dollar_and_a_penny_pays_the_cap(self):
        self.assertEqual(fixed_fee(1, 460), 1.0)
        self.assertAlmostEqual(fixed_fee(460 / 460, 460) / 460, 1 / 460)
        self.assertEqual(fixed_fee(4.6, 100), 1.0)
        # $50 at $0.50 is 100 shares. The $1 floor is above 1% of $50, so the cap binds.
        self.assertAlmostEqual(fixed_fee(100, 0.50), 0.50)
        # $100 at $0.40 is 250 shares. Per-share commission is $1.25, and the cap is $1.
        self.assertAlmostEqual(fixed_fee(250, 0.40), 1.0)
        self.assertEqual(fixed_fee(0, 10), 0.0)
        self.assertEqual(fixed_fee(10, 0), 0.0)


class SampleTests(unittest.TestCase):
    def test_the_formula_uses_the_sample_standard_deviation(self):
        xs = [0.01, 0.03, -0.01]
        n, mu, sigma, count = required_n(xs)
        self.assertEqual(count, 3)
        self.assertAlmostEqual(mu, statistics.mean(xs))
        self.assertAlmostEqual(sigma, statistics.stdev(xs))
        self.assertAlmostEqual(n, (1.645 * sigma / mu) ** 2)

    def test_a_negative_mean_stays_in_the_formula(self):
        n, mu, _sigma, _count = required_n([-0.02, -0.04])
        self.assertLess(mu, 0)
        self.assertGreater(n, 0)

    def test_zero_mean_and_a_short_sample_do_not_clear(self):
        self.assertEqual(required_n([0.01, -0.01])[0], None)
        self.assertEqual(required_n([0.02])[0], None)
        self.assertEqual(required_n([])[:3], (None, None, None))
        self.assertTrue(sample_unknown(96, None))
        self.assertTrue(sample_unknown(9, 10))
        self.assertFalse(sample_unknown(10, 10))
        self.assertEqual(queue_verdict(9, 10, False), UNKNOWN)
        self.assertEqual(queue_verdict(10, 10, False), "NO-GO")
        self.assertEqual(queue_verdict(10, 10, True), "GO")


class TomTests(unittest.TestCase):
    def test_the_window_is_the_last_day_and_the_first_three(self):
        sessions = [
            "2014-12-29", "2014-12-30", "2014-12-31",
            "2015-01-02", "2015-01-05", "2015-01-06", "2015-01-07",
            "2015-01-08",
        ]
        windows = tom_windows(sessions)
        december = [row for row in windows if row["month"] == "2014-12"][0]
        self.assertEqual(december["entry"], "2014-12-30")
        self.assertEqual(december["last"], "2014-12-31")
        self.assertEqual(december["exit"], "2015-01-06")
        held = held_sessions(sessions, [december])
        self.assertEqual(held, ["2014-12-31", "2015-01-02", "2015-01-05", "2015-01-06"])

    def test_a_short_next_month_is_skipped(self):
        sessions = ["2015-01-02", "2015-01-05", "2015-02-02", "2015-02-03"]
        self.assertEqual(tom_windows(sessions), [])

    def test_the_quick_screen_taxes_a_winning_year_and_not_a_losing_one(self):
        windows = [
            {"exit": "2015-01-06"}, {"exit": "2015-02-04"},
            {"exit": "2016-01-06"},
        ]
        gross = [0.02, 0.02, -0.05]
        fees = [2.0, 2.0, 2.0]
        screen = quick_screen(windows, gross, fees)
        self.assertAlmostEqual(screen["commission_annual"], (2.0 / 460) * 12)
        self.assertAlmostEqual(screen["gross_annual"], (0.02 + 0.02 - 0.05) / 3 * 12)
        self.assertAlmostEqual(screen["year_nets"][0]["tax"], 0.25 * screen["year_nets"][0]["net"])
        self.assertEqual(screen["year_nets"][1]["tax"], 0.0)
        self.assertAlmostEqual(window_fee(100, 110), 2.0)

    def test_the_path_earns_the_window_and_cash_earns_nothing(self):
        days = ["2014-12-30", "2014-12-31", "2015-01-02", "2015-01-05", "2015-01-06", "2015-01-07"]
        prices = {
            "2014-12-30": 100.0, "2014-12-31": 110.0, "2015-01-02": 110.0,
            "2015-01-05": 110.0, "2015-01-06": 121.0, "2015-01-07": 121.0,
        }
        windows = tom_windows(days)
        book = run_tom_path(days, held_sessions(days, windows), prices)
        self.assertGreater(book["terminal"], 460)
        self.assertGreater(book["fees"], 0)
        # Cash after the exit does not earn the last flat session.
        self.assertEqual(book["curve"][-1][1], book["curve"][-2][1])


class FaberTests(unittest.TestCase):
    def test_ten_months_and_a_tie_is_cash(self):
        ends = [f"2006-{month:02d}-28" for month in range(1, 11)]
        flat = {day: 100.0 for day in ends}
        self.assertEqual(month_ends(ends), ends)
        self.assertEqual(faber_signals(ends, flat), [(ends[-1], False)])
        up = dict(flat)
        up[ends[-1]] = 110.0
        self.assertEqual(faber_signals(ends, up), [(ends[-1], True)])
        self.assertEqual(faber_signals(ends[:9], flat), [])

    def test_a_round_trip_at_the_same_price_costs_two_dollars(self):
        days = ["2006-12-29", "2007-01-03", "2007-01-31"]
        prices = {day: 100.0 for day in days}
        book = run_faber(days, {"2006-12-29": True, "2007-01-31": False}, prices)
        self.assertAlmostEqual(book["terminal"], 458.0, places=6)
        self.assertEqual(book["switches"], 2)
        self.assertEqual(book["realizations"][0][0], "2007-01-31")
        held = run_faber(days, {"2006-12-29": True}, prices)
        self.assertAlmostEqual(held["terminal"], 458.0, places=6)
        self.assertEqual(held["switches"], 1)
        spy = buy_hold(100.0, 100.0)
        self.assertAlmostEqual(spy["terminal"], 458.0, places=6)
        self.assertEqual(spy["tax"], 0.0)


if __name__ == "__main__":
    unittest.main()
