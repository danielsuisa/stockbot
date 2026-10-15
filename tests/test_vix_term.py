"""The locked VIX term-structure rule. No network."""
import datetime as dt
import unittest

from bot import vix_term as vx


def _days(start, n):
    day = dt.date.fromisoformat(start)
    out = []
    while len(out) < n:
        out.append(day.isoformat())
        day += dt.timedelta(days=1)
    return out


def _panel(days, spy, vix, sgov_from=None):
    """Flat BIL at 50 and flat SGOV at 100. `spy` maps a day to (open, close). `vix` maps a day to a close."""
    prices = {"SPY": {}, "BIL": {}, "SGOV": {}}
    vx_close, vix3m = {}, {}
    for day in days:
        prices["SPY"][day] = spy[day]
        prices["BIL"][day] = (50.0, 50.0)
        if sgov_from is None or day >= sgov_from:
            prices["SGOV"][day] = (100.0, 100.0)
        vx_close[day] = vix[day]
        vix3m[day] = 15.0
    return prices, vx_close, vix3m


class Lock(unittest.TestCase):
    def test_the_rule_and_the_costs_are_fixed(self):
        self.assertEqual((vx.IS_START, vx.OOS_START, vx.SLEEVE_CUT), ("2008-01-01", "2020-01-01", "2020-01-01"))
        self.assertEqual(vx.DROP_MAX, 0.01)
        self.assertEqual(vx.VERDICT_DAYS, 1)
        self.assertEqual((vx.SPREAD, vx.ACCOUNT), (0.0002, 460.0))
        self.assertIn("does not place or transmit an order", vx.__doc__)

    def test_vix_below_vix3m_is_spy_and_the_fill_is_the_next_open(self):
        days = ["2020-06-01", "2020-06-02", "2020-06-03", "2020-06-04"]
        spy = {day: (100.0, 100.0) for day in days}
        spy["2020-06-03"] = (100.0, 110.0)
        spy["2020-06-04"] = (110.0, 110.0)
        prices, vix, vix3m = _panel(days, spy, {
            "2020-06-01": 20.0, "2020-06-02": 10.0, "2020-06-03": 10.0, "2020-06-04": 10.0,
        })
        got = vx.simulate(prices, vix, vix3m, "2020-06-05")
        self.assertTrue(got["complete"], got["reason"])
        one = [row["symbol"] for row in got["books"]["1"]["rows"]]
        two = [row["symbol"] for row in got["books"]["2"]["rows"]]
        self.assertEqual(one, ["SGOV", "SPY", "SPY"])
        self.assertEqual(two, ["SGOV", "SPY"])
        self.assertFalse(got["books"]["1"]["rows"][0]["switch"])
        self.assertTrue(got["books"]["1"]["rows"][1]["switch"])
        # Today's close is not today's order. Raising VIX on the 3rd does not change that day's hold.
        vix["2020-06-03"] = 30.0
        again = vx.simulate(prices, vix, vix3m, "2020-06-05")
        self.assertEqual([row["symbol"] for row in again["books"]["1"]["rows"]], ["SGOV", "SPY", "SGOV"])

    def test_equal_closes_hold_the_sleeve_and_bil_becomes_sgov(self):
        days = ["2019-12-30", "2019-12-31", "2020-01-02", "2020-05-22", "2020-05-26"]
        spy = {day: (100.0, 100.0) for day in days}
        prices, vix, vix3m = _panel(days, spy, {day: 15.0 for day in days}, sgov_from="2020-05-26")
        got = vx.simulate(prices, vix, vix3m, "2020-05-27")
        symbols = [row["symbol"] for row in got["books"]["1"]["rows"]]
        self.assertEqual(symbols, ["BIL", "BIL", "BIL", "SGOV"])
        self.assertEqual(got["books"]["1"]["per_year"]["2019"], 0)
        self.assertEqual(got["books"]["1"]["per_year"]["2020"], 1)
        self.assertAlmostEqual(got["books"]["1"]["switches_per_year"], 0.5)
        self.assertLess(got["books"]["1"]["final_equity"], vx.ACCOUNT)

    def test_an_in_sample_peak_does_not_set_the_out_of_sample_drawdown(self):
        days = ["2019-12-30", "2019-12-31", "2020-01-02", "2020-01-03"]
        spy = {
            "2019-12-30": (100.0, 100.0),
            "2019-12-31": (100.0, 200.0),
            "2020-01-02": (150.0, 150.0),
            "2020-01-03": (150.0, 150.0),
        }
        prices, vix, vix3m = _panel(days, spy, {day: 10.0 for day in days}, sgov_from="2020-01-02")
        got = vx.simulate(prices, vix, vix3m, "2020-01-04")
        book = got["books"]["1"]
        self.assertEqual(book["oos"]["maxdd"], 0.0)
        oos_rets = [row["ret"] for row in book["rows"] if row["sample"] == "oos"]
        self.assertAlmostEqual(oos_rets[0], -0.25)
        self.assertFalse(vx.goes(book["oos"], True)["go"])

    def test_a_hole_above_one_percent_is_incomplete(self):
        days = _days("2020-06-01", 100)
        spy = {day: (100.0, 100.0) for day in days}
        prices, vix, vix3m = _panel(days, spy, {day: 10.0 for day in days})
        for day in days[10:12]:
            del vix[day]
        got = vx.simulate(prices, vix, vix3m, "2020-12-31")
        self.assertFalse(got["complete"])
        self.assertGreater(got["drop_rate"], 0.01)
        self.assertFalse(got["verdict"]["go"])

    def test_the_run_date_is_not_a_session(self):
        days = ["2026-10-13", "2026-10-14", "2026-10-15"]
        spy = {day: (100.0, 100.0) for day in days}
        spy["2026-10-15"] = (100.0, 999.0)
        prices, vix, vix3m = _panel(days, spy, {day: 10.0 for day in days})
        got = vx.simulate(prices, vix, vix3m, "2026-10-15")
        self.assertEqual(got["end"], "2026-10-14")
        self.assertNotIn("2026-10-15", [row["day"] for row in got["books"]["1"]["rows"]])

    def test_the_overlap_starts_when_vix3m_exists(self):
        days = ["2008-01-02", "2008-01-03", "2008-01-04", "2020-01-04", "2020-01-05"]
        spy = {day: (100.0, 100.0) for day in days}
        prices, vix, vix3m = _panel(days, spy, {day: 10.0 for day in days}, sgov_from="2020-01-04")
        for day in ("2008-01-02", "2008-01-03"):
            del vix3m[day]
        got = vx.simulate(prices, vix, vix3m, "2020-01-06")
        self.assertEqual(got["window_start"], "2008-01-04")
        self.assertIn("2008-01-04", got["gap"])
        self.assertTrue(got["complete"], got["reason"])

    def test_sharpe_uses_the_sample_deviation_and_252(self):
        self.assertIsNone(vx.sharpe([0.01]))
        got = vx.sharpe([0.01, 0.03])
        mean = 0.02
        sd = ((0.01 - mean) ** 2 + (0.03 - mean) ** 2) ** 0.5
        self.assertAlmostEqual(got, (mean / sd) * (252 ** 0.5))

    def test_both_out_of_sample_checks_are_required(self):
        good = {"sharpe": 1.0, "spy_sharpe": 0.5, "maxdd": -0.10, "spy_maxdd": -0.20}
        self.assertTrue(vx.goes(good, True)["go"])
        tied = dict(good, sharpe=0.5)
        self.assertFalse(vx.goes(tied, True)["go"])
        deeper = dict(good, maxdd=-0.20)
        self.assertFalse(vx.goes(deeper, True)["go"])
        self.assertFalse(vx.goes(good, False)["go"])

    def test_the_final_mark_is_the_open_gain_and_the_parsers_read_the_files(self):
        days = ["2020-06-01", "2020-06-02", "2020-06-03"]
        spy = {day: (100.0, 110.0) for day in days}
        prices, vix, vix3m = _panel(days, spy, {day: 10.0 for day in days})
        got = vx.simulate(prices, vix, vix3m, "2020-06-04")
        book = got["books"]["1"]
        self.assertAlmostEqual(book["final_mark"][1], book["final_equity"] - vx.ACCOUNT)
        text = "DATE,OPEN,HIGH,LOW,CLOSE\n01/02/2008,22,23,21,22.5\n"
        self.assertEqual(vx.parse_cboe(text), {"2008-01-02": 22.5})
        ts = int(dt.datetime(2020, 1, 2, 14, 30, tzinfo=dt.timezone.utc).timestamp())
        payload = {"chart": {"result": [{
            "timestamp": [ts],
            "indicators": {
                "quote": [{"open": [100.0], "close": [110.0]}],
                "adjclose": [{"adjclose": [88.0]}],
            },
        }]}}
        self.assertEqual(vx.parse_yahoo(payload), {"2020-01-02": (80.0, 88.0)})
