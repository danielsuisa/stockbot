"""The SPY gate benchmark is the adjusted-close total return, not the raw close."""
import unittest

from bot.trend_backtest import total_return_buy_hold
from bot.trend_exec import ACCOUNT


class TotalReturnBenchmarkTests(unittest.TestCase):
    def test_entry_is_the_next_adjusted_open(self):
        sessions = ["2014-01-31", "2014-02-03", "2014-02-04"]
        # Raw close is unchanged. The adjusted close includes a dividend.
        actual = {
            "2014-01-31": (100.0, 100.0),
            "2014-02-03": (110.0, 110.0),
            "2014-02-04": (121.0, 121.0),
        }
        adj = {"2014-01-31": 80.0, "2014-02-03": 88.0, "2014-02-04": 100.0}
        book = total_return_buy_hold(adj, actual, sessions, "2014-01-31", "2014-02-04")
        # Buy the adjusted open on 2014-02-03, which equals that day's adjusted close
        # because the raw open equals the raw close. Then earn the next day's total return.
        self.assertAlmostEqual(book["final"], ACCOUNT * (100.0 / 88.0))
        self.assertEqual(book["entry"], "2014-02-03")
        self.assertNotAlmostEqual(book["final"], ACCOUNT * (121.0 / 110.0))


if __name__ == "__main__":
    unittest.main()
