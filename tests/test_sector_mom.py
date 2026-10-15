"""The locked sector-momentum rule. No network."""
import datetime as dt
import unittest

from bot import sector_mom as sm


def _days(start, end, price_of):
    d = dt.date.fromisoformat(start)
    stop = dt.date.fromisoformat(end)
    rows, i = [], 0
    while d <= stop:
        if d.weekday() < 5:
            rows.append((d.isoformat(), price_of(i, d)))
            i += 1
        d += dt.timedelta(days=1)
    return rows


class Lock(unittest.TestCase):
    def test_the_nine_original_sectors_and_the_costs_are_fixed(self):
        self.assertEqual(sm.UNIVERSE, ("XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"))
        self.assertEqual(sm.EXCLUDED, ("XLC", "XLRE"))
        self.assertEqual((sm.LOOKBACK, sm.SKIP, sm.TOP_N, sm.SMA_DAYS), (6, 1, 2, 200))
        self.assertEqual((sm.SPREAD, sm.COMMISSION, sm.MIN_ORDER, sm.ACCOUNT), (0.0002, 0.0035, 0.35, 460.0))
        self.assertNotIn("XLC", sm.UNIVERSE)
        self.assertNotIn("XLRE", sm.UNIVERSE)

    def test_the_formation_window_skips_the_latest_month(self):
        ends = [f"2020-{m:02d}-28" for m in range(1, 8)]
        # Index 6 is July. t-1 is June, t-6 is January. July's price must not enter.
        rows = [(ends[i], 100 + i) for i in range(7)]
        self.assertAlmostEqual(sm.formation_return(rows, ends, 6), rows[5][1] / rows[0][1] - 1)
        jumped = rows[:-1] + [(ends[6], 999)]
        self.assertAlmostEqual(sm.formation_return(jumped, ends, 6), sm.formation_return(rows, ends, 6))

    def test_a_tie_takes_the_symbol_that_sorts_first(self):
        self.assertEqual(sm.rank_top({"XLK": 0.1, "XLE": 0.2, "XLF": 0.2, "XLV": 0.05}), ["XLE", "XLF"])

    def test_commission_minimum_and_a_two_basis_point_spread(self):
        self.assertEqual(sm.order_fee(2.3), 0.35)
        self.assertAlmostEqual(sm.order_fee(200), 0.70)
        cash, fee, spread = sm.trade_cost("buy", 2, 100)
        self.assertAlmostEqual(spread, 0.02)
        self.assertAlmostEqual(cash, -(200 + 0.02 + 0.35))
        sale, _fee, sale_spread = sm.trade_cost("sell", 2, 100)
        self.assertAlmostEqual(sale_spread, 0.02)
        self.assertAlmostEqual(sale, 200 - 0.02 - 0.35)
        self.assertAlmostEqual(sm.buy_shares(230, 100), (230 - 0.35) / (100 * 1.0001))

    def test_the_open_month_is_not_a_month_end(self):
        rows = [("2026-09-30", 1.0), ("2026-10-14", 1.0)]
        self.assertEqual(sm.completed_month_ends(rows, "2026-10-15"), ["2026-09-30"])


class Book(unittest.TestCase):
    def _prices(self, start, end, grow):
        days = _days(start, end, lambda i, _d: 1)
        out = {}
        for sym in sm.UNIVERSE:
            rate = grow.get(sym, 0.0)
            out[sym] = [(day, 20 * (1 + rate) ** i) for i, (day, _px) in enumerate(days)]
        return out, days

    def test_spy_below_its_average_holds_cash(self):
        spy = _days("2008-01-02", "2010-08-31", lambda i, _d: 300 - 0.15 * i)
        prices = {sym: [(day, 20.0) for day, _px in spy] for sym in sm.UNIVERSE}
        got = sm.simulate(prices, spy, "2010-09-15")
        self.assertTrue(got["complete"], got["reason"])
        oos_cash = [m for m in got["months"] if m["sample"] == "is" and m["regime"] == "cash"]
        self.assertTrue(oos_cash)
        self.assertTrue(all(m["names"] == "" for m in oos_cash))

    def test_the_two_fastest_sectors_are_held_in_fractional_shares(self):
        spy = _days("2008-01-02", "2010-08-31", lambda i, _d: 100 * (1.001 ** i))
        prices = {}
        for sym in sm.UNIVERSE:
            rate = {"XLK": 0.004, "XLV": 0.002}.get(sym, 0.0)
            prices[sym] = [(day, 20 * (1 + rate) ** i) for i, (day, _px) in enumerate(spy)]
        point = sm.asof(spy, "2009-12-31", limit=0)
        self.assertIsNotNone(point)
        average = sm.sma(spy, point[0])
        self.assertGreater(point[1], average)
        got = sm.simulate(prices, spy, "2010-09-15")
        self.assertTrue(got["complete"], got["reason"])
        held = [m for m in got["months"] if m["sample"] == "is" and m["regime"] == "invest"]
        self.assertTrue(held)
        self.assertTrue(all(m["names"] == "XLK,XLV" for m in held))
        self.assertGreater(got["final_equity"], 0)
        self.assertLess(got["fee"], got["final_equity"])

    def test_success_is_the_out_of_sample_mean_and_the_sharpe(self):
        good = {"mean": 0.01, "sharpe": 0.9, "spy_sharpe": 0.4}
        self.assertTrue(sm.goes(good, True)["go"])
        self.assertFalse(sm.goes({"mean": -0.01, "sharpe": 0.9, "spy_sharpe": 0.4}, True)["go"])
        self.assertFalse(sm.goes({"mean": 0.01, "sharpe": 0.4, "spy_sharpe": 0.9}, True)["go"])
        self.assertFalse(sm.goes(good, False)["go"])
        self.assertGreater(sm.sharpe([0.02, 0.01, 0.03, 0.02]), sm.sharpe([0.02, -0.01, 0.03, -0.02]))
