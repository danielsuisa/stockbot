"""Whole-share Faber and GEM execution. No network and no return verdict."""
import unittest

from bot.trend_exec import (
    CASH, actual_dividends, actual_prints, block_bootstrap, drawdown_is_smaller,
    faber_signal, fixed_commission, gem_bil_passes, schedule, simulate, trend_passes,
    whole_shares,
)


class Costs(unittest.TestCase):
    def test_commission_minimum_and_one_percent_cap(self):
        self.assertAlmostEqual(fixed_commission(5, 84), 1.0)
        self.assertAlmostEqual(fixed_commission(1, 50), 0.50)

    def test_whole_shares_use_the_open_that_is_passed_in(self):
        # $84 is the actual pre-split SPYM price, not the split-adjusted print near $21.
        count = whole_shares(460, 84)
        self.assertEqual(count, 5)
        self.assertEqual(whole_shares(460, 700), 0)

    def test_a_split_is_put_back_onto_the_historical_print(self):
        bars = {"2017-10-13": (29.94, 29.94), "2017-10-16": (30.01, 29.96)}
        actual = actual_prints(bars, {"2017-10-16": 4})
        self.assertAlmostEqual(actual["2017-10-13"][0], 29.94 * 4)
        self.assertAlmostEqual(actual["2017-10-16"][0], 30.01)
        divs = actual_dividends({"2017-09-15": 0.14025, "2017-12-15": 0.20}, {"2017-10-16": 4})
        self.assertAlmostEqual(divs["2017-09-15"], 0.14025 * 4)
        self.assertAlmostEqual(divs["2017-12-15"], 0.20)


class Orders(unittest.TestCase):
    def setUp(self):
        self.days = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08"]
        self.prices = {
            "SPYM": {day: (100.0, 101.0) for day in self.days},
            "VEU": {day: (40.0, 41.0) for day in self.days},
        }

    def test_the_fill_is_the_next_open_and_a_switch_waits_a_session(self):
        orders = schedule(CASH, "SPYM", self.days, self.prices, "2020-01-02")
        self.assertEqual(orders, [("buy", "2020-01-03", "SPYM")])
        switch = schedule("SPYM", "VEU", self.days, self.prices, "2020-01-06")
        self.assertEqual(switch, [
            ("sell", "2020-01-07", "SPYM"),
            ("buy", "2020-01-08", "VEU"),
        ])

    def test_the_new_fund_is_sized_from_its_own_open(self):
        result = simulate(
            self.prices, {}, {}, self.days,
            {"2020-01-02": "SPYM", "2020-01-06": "VEU"},
            "2020-01-02", "2020-01-08",
        )
        buys = [trade for trade in result["trades"] if trade["side"] == "buy"]
        self.assertEqual([trade["date"] for trade in buys], ["2020-01-03", "2020-01-08"])
        self.assertEqual(buys[0]["symbol"], "SPYM")
        self.assertEqual(buys[1]["symbol"], "VEU")
        self.assertEqual(buys[1]["open"], 40.0)
        self.assertEqual(buys[1]["shares"], whole_shares(buys[0]["cash"] + buys[0]["shares"] * 100 * 0.9999
                                                         - fixed_commission(buys[0]["shares"], 100), 40))
        self.assertNotEqual(buys[1]["shares"], buys[0]["shares"])

    def test_a_four_for_one_split_quadruples_the_shares(self):
        days = ["2020-01-02", "2020-01-03", "2020-01-06"]
        prices = {"SPYM": {
            "2020-01-02": (84.0, 84.0),
            "2020-01-03": (84.0, 84.0),
            "2020-01-06": (21.0, 21.0),
        }}
        result = simulate(prices, {"SPYM": {"2020-01-06": 4}}, {}, days,
                          {"2020-01-02": "SPYM"}, "2020-01-02", "2020-01-06")
        sold = [trade for trade in result["trades"] if trade.get("liquidation")][0]
        self.assertEqual(sold["shares"], whole_shares(460, 84) * 4)


class Rule(unittest.TestCase):
    def test_faber_uses_ten_month_ends_and_cash_on_a_tie(self):
        closes = [(f"2020-{month:02d}-28", 100) for month in range(1, 11)]
        closes[-1] = ("2020-10-28", 100)
        signals = faber_signal(closes, "SPYM")
        self.assertEqual(signals["2020-10-28"], CASH)
        closes[-1] = ("2020-10-28", 101)
        signals = faber_signal(closes, "SPYM")
        self.assertEqual(signals["2020-10-28"], "SPYM")

    def test_pass_needs_both_the_return_gap_and_the_bootstrap(self):
        self.assertTrue(trend_passes(0.09, 0.10, 0.80))
        self.assertFalse(trend_passes(0.08, 0.10, 0.95))
        self.assertFalse(trend_passes(0.10, 0.10, 0.79))
        self.assertTrue(gem_bil_passes(True, True))
        self.assertFalse(gem_bil_passes(True, False))

    def test_one_third_smaller_is_two_thirds_of_spy(self):
        self.assertTrue(drawdown_is_smaller(0.20, 0.30))
        self.assertFalse(drawdown_is_smaller(0.21, 0.30))

    def test_bootstrap_fraction_is_stable(self):
        strategy = [0.01, -0.02, 0.01] * 8
        spy = [0.02, -0.08, 0.02] * 8
        self.assertEqual(block_bootstrap(strategy, spy, draws=20, seed=20261015),
                         block_bootstrap(strategy, spy, draws=20, seed=20261015))
