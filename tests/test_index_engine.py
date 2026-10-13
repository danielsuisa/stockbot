"""Index program phase 2, task 3 (spec sections 5 and 6.2, E4): execution, accounting and costs, by hand-computed
ledgers on SYNTHETIC prices."""
import unittest

from bot import index_engine as ie

ZERO = ie.Costs(0.0, 0.0)
ALWAYS = [("0000-00-00", "open", True)]


def rows(*spec):
    """(day, open, close) -> (day, o, h, l, c)."""
    return [(d, o, max(o, c), min(o, c), c) for d, o, c in spec]


R = rows(("2020-01-02", 100, 100), ("2020-01-03", 100, 110), ("2020-01-06", 118, 120), ("2020-01-07", 115, 116))
NO_CASH = {r[0]: 0.0 for r in R}


def run(changes, scale=1.0, costs=ZERO, start="2020-01-03", end="2020-01-07", cash=None, rws=R):
    return ie.run_window(rws, cash or NO_CASH, changes, scale, costs, start, end)


class Accounting(unittest.TestCase):
    def test_entry_day_marked_from_execution_price(self):
        res = run([("2020-01-03", "open", True)])
        self.assertAlmostEqual(res["equity"][0], 10_000 * 110 / 100)  # bought at O = 100, marked at C = 110

    def test_exit_day_marked_to_execution_price(self):
        res = run([("2020-01-03", "open", True), ("2020-01-07", "open", False)])
        self.assertAlmostEqual(res["equity"][1], 12_000.0)  # 100 shares at the close of 120
        self.assertAlmostEqual(res["equity"][2], 11_500.0)  # sold at the open of 115: from 120 to 115
        self.assertEqual(res["tickets"], 4)

    def test_both_legs_pay_costs(self):
        res = run([("2020-01-03", "open", True)], costs=ie.Costs(0.35, 1.0), end="2020-01-03")
        cost = 2 * 0.35 + 2 * 0.0001 * 10_000  # two orders, 1 basis point on each leg's 10,000
        self.assertAlmostEqual(res["equity"][0], 11_000.0 - cost)
        self.assertEqual(res["tickets"], 2)
        self.assertAlmostEqual(res["costs"], cost)

    def test_cash_leg_credit(self):
        cash = dict(NO_CASH, **{"2020-01-03": 0.01, "2020-01-06": 0.02})
        res = run([("2020-01-06", "open", True)], cash=cash)
        self.assertAlmostEqual(res["equity"][0], 10_100.0)  # day 1: the cash at the close before earns 1%
        # day 2: 10,100 earns 2% before it moves at the open (the money in cash at the close of day 1), then all of
        # 10,302 goes into SPY at 118 and is marked at 120
        self.assertAlmostEqual(res["equity"][1], 10_302.0 * 120 / 118)

    def test_fresh_window_starts_in_cash(self):
        res = run(ALWAYS, costs=ie.Costs(0.35, 1.0), start="2020-01-06", end="2020-01-06")
        self.assertEqual(res["tickets"], 2)  # it takes the policy's position at the window's first open
        self.assertAlmostEqual(res["equity"][0], (10_000 * 120 / 118) - (0.70 + 2.0))

    def test_open_position_marked_at_window_end(self):
        res = run([("2020-01-03", "open", True)], end="2020-01-06")
        self.assertAlmostEqual(res["final"], 12_000.0)
        self.assertEqual(res["tickets"], 2)  # no exit is charged

    def test_last_day_signal_not_executed(self):
        res = run([("2020-01-06", "close", True)], end="2020-01-06")  # E4: a close execution on the last day
        self.assertEqual(res["tickets"], 0)
        res = run([("2020-01-06", "close", True)], end="2020-01-07")
        self.assertEqual(res["tickets"], 2)
        self.assertAlmostEqual(res["equity"][-1], 10_000 * 116 / 120)  # bought at C = 120 on 01-06

    def test_close_execution_marks_nothing_that_day(self):
        res = run([("2020-01-03", "close", True)])
        self.assertAlmostEqual(res["equity"][0], 10_000.0)  # bought at the close: the day's mark is unchanged
        self.assertAlmostEqual(res["equity"][1], 10_000 * 120 / 110)

    def test_scale_partial(self):
        res = run([("2020-01-03", "open", True)], scale=0.6, end="2020-01-03")
        self.assertAlmostEqual(res["equity"][0], 6_000 * 1.1 + 4_000)
        self.assertAlmostEqual(res["weights"][0], 6_600 / 10_600)


class Rebalance(unittest.TestCase):
    YR = rows(("2020-12-30", 100, 100), ("2020-12-31", 100, 150), ("2021-01-04", 150, 150))

    def test_yearly_rebalance_restores_s(self):
        res = run(ALWAYS, scale=0.6, start="2020-12-31", end="2021-01-04", rws=self.YR,
                  cash={r[0]: 0.0 for r in self.YR})
        # 12-31: 60 shares and 4,000 cash; the close of 150 makes 9,000 in SPY of 13,000: weight 0.692 > 0.65.
        # 01-04 open: trade 0.6 x 13,000 - 9,000 = -1,200 at 150: 52 shares, 5,200 cash
        self.assertEqual(res["tickets"], 4)
        self.assertAlmostEqual(res["weights"][1], 52 * 150 / 13_000)

    def test_inside_the_band_no_rebalance(self):
        yr = rows(("2020-12-30", 100, 100), ("2020-12-31", 100, 118), ("2021-01-04", 118, 118))
        res = run(ALWAYS, scale=0.6, start="2020-12-31", end="2021-01-04", rws=yr, cash={r[0]: 0.0 for r in yr})
        self.assertEqual(res["tickets"], 2)  # weight 7,080 / 11,080 = 0.639: within 0.05 of 0.60

    def test_no_rebalance_after_a_rule_event_at_that_open(self):
        res = run([("2020-12-31", "open", True), ("2021-01-04", "open", False)], scale=0.6, start="2020-12-31",
                  end="2021-01-04", rws=self.YR, cash={r[0]: 0.0 for r in self.YR})
        self.assertEqual(res["tickets"], 4)  # the entry and the exit, no rebalance
        self.assertAlmostEqual(res["weights"][1], 0.0)


if __name__ == "__main__":
    unittest.main()
