"""Phase A.1 of the recommendation algorithm (spec 2026-10-10 revision 6, section 7 A.1): survivorship stock-months,
the rename rule, f and b. Counts only: no return of any ticket or stock is computed anywhere in the code under test."""
import unittest
from unittest import mock

from bot import probe_ensemble as pe, probe_ensemble_data as pd, probe_survivorship as ps, shorts
from tests.test_probe_ensemble import Bars, bars_for, make_zip, sub, tx


class Readers(unittest.TestCase):
    def test_transaction_prices(self):
        z = make_zip({
            "SUBMISSION": [sub("a", "4"), sub("b", "5", filed="06-JAN-2017"), sub("c", "4/A"), sub("d", "3"),
                           sub("e", "4", symbol="NONE")],
            "NONDERIV_TRANS": [tx("a", code="S", ad="D", price="12"), tx("a", code="P", price="0"),
                               tx("b", code="P", price="7", date="04-JAN-2017"), tx("c", price="30"),
                               tx("d", price="31"), tx("e", price="32")]})
        self.assertEqual(sorted(pd.transaction_prices(z)),
                         [("2017-01-05", "ACME", "2017-01-03", "a", 12.0),  # a $0 line is not a price
                          ("2017-01-06", "ACME", "2017-01-04", "b", 7.0)])  # 4/A, Form 3 and NONE dropped

    def test_transaction_prices_market_codes_only(self):
        # owner's decision 3: P, S and F carry a market price; M, X, C report the strike; any other code is ignored
        z = make_zip({"SUBMISSION": [sub("a", "4")],
                      "NONDERIV_TRANS": [tx("a", code="M", price="10"), tx("a", code="S", ad="D", price="50"),
                                         tx("a", code="X", price="11"), tx("a", code="C", price="12"),
                                         tx("a", code="F", ad="D", price="49"), tx("a", code="P", price="48"),
                                         tx("a", code="J", price="47")]})
        self.assertEqual(sorted(p[4] for p in pd.transaction_prices(z)), [48.0, 49.0, 50.0])

    def test_finra_spellings_and_row(self):
        self.assertEqual(ps.finra_spellings("BRK.B"), ("BRK-B", "BRKB"))
        self.assertEqual(ps.finra_spellings("A"), ("A", "A"))
        self.assertEqual(ps.finra_spellings("AAPL"), ("AAPL", "AAPL"))
        r = {"adv": 1}
        self.assertIs(ps.finra_row({"BRKB": r}, "BRK.B"), r)
        self.assertIs(ps.finra_row({"BRK-B": r}, "BRK.B"), r)
        self.assertIsNone(ps.finra_row({}, "AAPL"))

    def test_volume_check(self):
        reps = {"2018-01-12": {"A": {"adv": None}}, "2017-12-29": {"A": {"adv": 1000}, "B": {"adv": None}}}
        self.assertEqual(ps.volume_check(reps), {"reports": 2, "rows": 3, "rows with volume": 1,
                                                 "reports without any volume": ["2018-01-12"]})
        self.assertEqual(ps.volume_check({"2018-01-12": {}})["reports without any volume"], ["2018-01-12"])

    def test_finra_report_cached(self):
        import tempfile
        import pathlib
        row = {"symbolCode": "AAPL", "averageDailyVolumeQuantity": 5, "marketClassCode": "NNM",
               "settlementDate": "2018-01-12"}
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ps, "CACHE", pathlib.Path(d)), \
                mock.patch.object(shorts, "_post", return_value=[row]) as post:
            first = ps.finra_report("2018-01-12")
            second = ps.finra_report("2018-01-12")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(first, second)
        self.assertEqual(first["AAPL"]["adv"], 5)


class StockMonths(unittest.TestCase):
    END = "2018-01-31"

    def test_month_ends(self):
        self.assertEqual(ps.month_ends("2018-01", "2018-02"), [("2018-01", "2018-01-31"), ("2018-02", "2018-02-28")])
        self.assertEqual(ps.month_ends("2024-02", "2024-02"), [("2024-02", "2024-02-29")])
        self.assertEqual(len(ps.month_ends("2018-01", "2026-03")), 99)

    def test_latest_before(self):
        items = [("2018-01-02", "x"), ("2018-01-31", "y"), ("2018-02-01", "z")]
        self.assertEqual(ps.latest_before(items, "2018-01-31"), ("2018-01-31", "y"))
        self.assertIsNone(ps.latest_before(items, "2018-01-01"))

    def test_universe_like_conditions(self):
        filed = {"AAA": ["2017-02-01"], "BBB": ["2017-01-30"], "CCC": ["2017-06-01"], "DDD": ["2017-06-01"],
                 "EEE": ["2017-06-01"], "FFF": ["2017-06-01"]}
        prices = {s: [("2018-01-10", "2018-01-09", "a", 20.0)] for s in filed}
        prices["CCC"] = [("2017-05-01", "2017-04-28", "a", 50.0), ("2018-01-10", "2018-01-09", "b", 4.99)]
        finra = {"AAA": {"adv": 600_000}, "BBB": {"adv": 9e9}, "CCC": {"adv": 9e9}, "DDD": {"adv": 499_000},
                 "FFF": {"adv": None}}
        stats = {}
        got = ps.universe_like(self.END, filed, prices, finra, stats)
        self.assertEqual(got, {"AAA": {"V": 600_000, "P": 20.0}})  # $12M; DDD is $9.98M; EEE is not in FINRA
        self.assertEqual(stats, {"FINRA row without volume": 1, "not in the FINRA report": 1})

    def test_universe_like_edges(self):
        prices = {"AAA": [("2018-01-31", "2018-01-30", "a", 5.0)]}  # filed on the month's end: counts
        finra = {"AAA": {"adv": 2_000_000}}  # $5 x 2,000,000 = $10M exactly
        for d in ("2017-01-31", "2018-01-31"):  # 365 days before the month's end, and the month's end
            self.assertIn("AAA", ps.universe_like(self.END, {"AAA": [d]}, prices, finra))
        old = {"AAA": [("2017-01-30", "2017-01-30", "a", 50.0)]}  # the only price is outside the 365 days
        self.assertEqual(ps.universe_like(self.END, {"AAA": ["2017-06-01"]}, old, finra), {})
        self.assertEqual(ps.universe_like(self.END, {"AAA": ["2017-06-01"]}, prices, None), {})  # no report

    def test_latest_price_order(self):
        finra = {"AAA": {"adv": 10_000_000}}
        same_day = {"AAA": sorted([("2018-01-10", "2018-01-08", "z", 2.0), ("2018-01-10", "2018-01-09", "a", 6.0)])}
        self.assertIn("AAA", ps.universe_like(self.END, {"AAA": ["2017-06-01"]}, same_day, finra))  # later trade
        same_trade = {"AAA": sorted([("2018-01-10", "2018-01-09", "b", 6.0), ("2018-01-10", "2018-01-09", "a", 2.0)])}
        self.assertIn("AAA", ps.universe_like(self.END, {"AAA": ["2017-06-01"]}, same_trade, finra))  # larger acc

    def test_cik_and_rename(self):
        by_sym = {"OLD": [("2018-11-01", "1")], "SYM": [("2017-03-01", "3"), ("2018-02-01", "4")]}
        self.assertEqual(ps.cik_at(by_sym, "SYM", "2018-06-30"), "4")
        self.assertEqual(ps.cik_at(by_sym, "SYM", "2018-01-31"), "3")
        self.assertIsNone(ps.cik_at(by_sym, "OLD", "2018-06-30"))
        bars = {"NEW": {"2019-03"}, "X": {"2019-03"}, "Y": {"2019-03"}}
        ciks = {"1": {"OLD", "NEW"}, "2": {"OLD2", "X", "Y"}, "5": {"OLD3", "NOBARS"}}
        self.assertEqual(ps.recovered("OLD", "2019-03", "1", ciks, bars), "NEW")
        self.assertIsNone(ps.recovered("OLD", "2019-04", "1", ciks, bars))  # no NEW bar that month
        self.assertIsNone(ps.recovered("OLD2", "2019-03", "2", ciks, bars))  # two other symbols with bars
        self.assertIsNone(ps.recovered("OLD3", "2019-03", "5", ciks, bars))
        self.assertIsNone(ps.recovered("OLD", "2019-03", None, ciks, bars))

    def test_counts_and_decision(self):
        per_month = {"2018-01": {"like": {"A", "B", "C"}, "with bars": {"A"}, "recovered": {"B": "B2"}, "duplicates": 1},
                     "2019-05": {"like": {"A"}, "with bars": {"A"}, "recovered": {}, "duplicates": 0}}
        c = ps.survivorship_counts(per_month)
        self.assertEqual(c["per_year"]["2018"], {"universe-like": 3, "with bars": 1, "without bars": 2,
                                                 "recovered by the rename rule": 1,
                                                 "recovered, duplicating a stock-month with bars": 1})
        self.assertEqual(c["overall"]["universe-like"], 4)
        cell = lambda w, r, dup=0: {"universe-like": 100, "with bars": 100 - w, "without bars": w,  # noqa: E731
                                    "recovered by the rename rule": r,
                                    "recovered, duplicating a stock-month with bars": dup}
        d = ps.decide(cell(20, 5))  # 5 < 20 / 3: the rule is not used, nothing counts as recovered
        self.assertEqual((d["rename rule used"], d["f (rule as decided)"], d["f (literal, recovered always subtracted)"],
                          d["b_R"], d["b within 0.03 R"]), (False, 0.20, 0.15, 0.20, False))
        d = ps.decide(cell(20, 8))
        self.assertEqual((d["rename rule used"], d["f (rule as decided)"]), (True, 0.12))
        self.assertTrue(ps.decide(cell(3, 1))["rename rule used"])  # exactly a third
        d = ps.decide(cell(3, 0))
        self.assertEqual((d["b_R"], d["b within 0.03 R"]), (0.03, True))  # b at the limit
        self.assertEqual(ps.B_MAX, 0.03)
        # owner's decision 4: 10 duplicates leave both sides: 90 stock-months, 10 without bars, 0 recovered
        d = ps.decide(cell(20, 10, dup=10))
        self.assertEqual((d["rename rule used"], d["f (rule as decided)"], d["f (literal, recovered always subtracted)"]),
                         (False, 10 / 90, 10 / 90))


class Additions(unittest.TestCase):
    def test_f_per_year_both_readings(self):
        per_year = {"2018": {"universe-like": 110, "with bars": 90, "without bars": 20, "recovered by the rename rule": 8,
                             "recovered, duplicating a stock-month with bars": 10},
                    "2019": {"universe-like": 0, "with bars": 0, "without bars": 0, "recovered by the rename rule": 0,
                             "recovered, duplicating a stock-month with bars": 0}}
        # 2018 without duplicates: 100 stock-months, 10 without bars, recovered 8 - 10 -> counted as 0 (never below)
        got = ps.f_per_year(per_year, used=True)
        self.assertEqual(got["2018"], {"f (rule as decided)": 0.10, "f (literal, recovered always subtracted)": 0.10})
        self.assertEqual(got["2019"], {"f (rule as decided)": None, "f (literal, recovered always subtracted)": None})
        per_year["2018"]["recovered, duplicating a stock-month with bars"] = 2  # 108 stock-months, 18 without, 6 rec
        got = ps.f_per_year(per_year, used=False)["2018"]
        self.assertEqual(got, {"f (rule as decided)": 18 / 108, "f (literal, recovered always subtracted)": 12 / 108})

    def test_rename_bars(self):
        # a clean symbol change: the old symbol's last filing is before the new one's first
        by_sym = {"OLD": [("2018-01-10", "1"), ("2019-05-01", "1")], "NEW": [("2019-07-01", "1"), ("2020-01-01", "1")],
                  "X1": [("2018-01-10", "2")], "X2": [("2019-01-10", "2")],
                  "C1": [("2018-01-10", "3"), ("2020-01-01", "3")], "C2": [("2018-02-10", "3")]}  # CIK 3 overlaps
        of_cik = {"1": {"OLD", "NEW"}, "2": {"X1", "X2"}, "3": {"C1", "C2"}}
        bars = {"OLD": {"2019-03"}, "NEW": {"2019-03", "2019-08"}, "X2": {"2019-02"}}
        self.assertEqual(ps.rename_bars(by_sym, of_cik, bars), {
            "symbol changes (CIKs with two symbols filed one after the other)": 2,
            "old symbol has bars before the change": 1, "new symbol has bars before the change": 1,
            "both": 1, "neither": 1})


class Run(unittest.TestCase):
    """The run on a mocked data set: A has bars; OLD has none and its CIK's other symbol NEW has bars (recovered); B
    has none and nothing to recover it. Months 2017-03 .. 2017-05."""
    S = Bars.S

    def run_mocked(self, checks=False, adv=1_000_000, new_like=False, raw_extra=None):
        S = self.S
        ser = {"A": bars_for(400, 10.0, 2_000_000), "NEW": bars_for(400, 10.0, 2_000_000)}
        raw = {s: [(d, 10.0, 10.1, 9.9, 10.0, 2_000_000) for d in S] for s in ser}  # raw Alpaca bars (decision 5)
        raw.update(raw_extra or {})
        rows = [("2017-02-01", "100", "A"), ("2017-02-01", "1", "OLD"), ("2016-06-01", "1", "NEW"),
                ("2017-02-01", "200", "B")]
        like = ("A", "OLD", "B") + (("NEW",) if new_like else ())
        prices = {s: [("2017-02-01", "2017-01-31", "a", 20.0)] for s in like}
        settles = ["2017-02-28", "2017-03-31", "2017-04-28", "2017-05-31"]
        reports = {d: {s: {"adv": adv} for s in like} for d in settles}

        def daily(symbols, start, end):
            return {s: raw.get(s) for s in symbols}

        def releases(zip_path, ciks, start, end, forms=("8-K",)):
            return {"100": [("x", "2017-03-01")]} if "8-K/A" in forms else {}  # the only release is an 8-K/A
        with (mock.patch.object(pe, "_calendar", return_value=S),
              mock.patch.object(ps, "load_filings", return_value=(rows, prices, {}, ["2015Q1", "2026Q1"])),
              mock.patch.object(shorts, "settlement_dates", return_value=settles),
              mock.patch.object(ps, "finra_report", side_effect=lambda d: reports[d]),
              mock.patch.object(pd, "download", return_value="x.zip"),
              mock.patch.object(pd, "earnings_releases", side_effect=releases),
              mock.patch.object(pd, "load_series", return_value=ser),
              mock.patch.object(ps.alpaca, "daily", side_effect=daily),
              mock.patch.object(ps, "MONTHS", ("2017-03", "2017-05")),
              mock.patch.object(pe, "SIGNAL_START", S[261]), mock.patch.object(pe, "SIGNAL_END", S[-1])):
            return ps.run(checks)

    def test_bars_come_from_raw_alpaca(self):
        # decision 5: a stock-month has bars when the raw Alpaca bars hold a session in the month (B: April only)
        out = self.run_mocked(raw_extra={"B": [("2017-04-03", 10.0, 10.1, 9.9, 10.0, 1)]})
        self.assertEqual(out["stock_months"]["per_year"]["2017"]["with bars"], 4)

    def test_report_keys_only(self):
        import json
        import re
        out = self.run_mocked()
        self.assertEqual(tuple(out), ps.REPORT_KEYS)
        self.assertIn("2017", out["f_per_year"])
        self.assertIn("symbol changes (CIKs with two symbols filed one after the other)", out["rename_bars"])
        self.assertIsNone(re.search(r"(?i)(return|returns|mean|median|win)", json.dumps(out)), out)
        o = out["stock_months"]["overall"]
        self.assertEqual((o["universe-like"], o["with bars"], o["without bars"], o["recovered by the rename rule"]),
                         (9, 3, 6, 3))
        self.assertEqual(out["rename_rule"]["rename rule used"], True)  # 3 of 6: at least a third
        self.assertAlmostEqual(out["allowance"]["b_R"], 3 / 9)
        self.assertFalse(out["allowance"]["b within 0.03 R"])

    def test_rename_year_with_both_symbols_filed(self):
        # OLD (no bars) and NEW (bars) both filed and universe-like: OLD is NEW counted twice, not a missing company
        out = self.run_mocked(new_like=True)
        o = out["stock_months"]["overall"]
        self.assertEqual((o["universe-like"], o["with bars"], o["without bars"], o["recovered by the rename rule"],
                          o["recovered, duplicating a stock-month with bars"]), (12, 6, 6, 3, 3))
        self.assertEqual(out["stock_months"]["per_year"]["2017"]["recovered, duplicating a stock-month with bars"], 3)
        a = out["allowance"]  # without duplicates: 9 stock-months, 3 without bars (B), none recovered
        self.assertEqual((a["f (rule as decided)"], a["f (literal, recovered always subtracted)"]), (3 / 9, 3 / 9))

    def test_coverage_with_8k_a_differs_by_the_amendment_only(self):
        out = self.run_mocked()
        # stock-years: A and NEW in 2017 (both filed with bars); the only release (CIK 100 -> A) is an 8-K/A
        self.assertEqual(out["item_202_coverage_8k_only"]["overall"], {"stock_years": 2, "covered": 0, "share": 0.0})
        self.assertEqual(out["item_202_coverage_with_8k_a"]["overall"], {"stock_years": 2, "covered": 1, "share": 0.5})
        self.assertEqual(out["s2_decision"]["decision"], "8-K dropped")  # decided on 8-K only

    def test_stops_without_volume(self):
        out = self.run_mocked(adv=None)
        self.assertEqual(out["stopped"], "FINRA's reports carry no volume field")
        self.assertEqual(set(out), {"finra_volume_check", "stopped"})
        with mock.patch.object(ps, "run", return_value=out):
            self.assertEqual(ps.main([]), 1)

    def test_checks_keys(self):
        out = self.run_mocked(checks=True)
        self.assertEqual(tuple(out), ps.CHECK_KEYS)
        self.assertIsNone(out["rename_check"]["FB"]["recovered as"])  # FB is not in the mocked data: no crash


if __name__ == "__main__":
    unittest.main()
