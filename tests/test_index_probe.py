"""Index program phase 1 (spec 2026-10-12 sections 3, 8.1): the probe's parsers and checks on SYNTHETIC prices only.
Network mocked; no return of any rule is computed."""
import json
import os
import tempfile
import unittest
from unittest import mock

from bot import index_probe as ip


def rows(closes, opens=None, start=1):
    opens = opens or closes
    return [(f"2020-01-{start + k:02d}", o, max(o, c) + 1, min(o, c) - 1, c) for k, (o, c) in enumerate(zip(opens, closes))]


class Parsers(unittest.TestCase):
    def test_stooq_csv(self):
        body = b"Date,Open,High,Low,Close,Volume\n2020-01-03,2,3,1,2.5,10\n2020-01-02,1,2,0.5,1.5,10\n"
        self.assertEqual(ip.parse_ohlc_csv(body), [("2020-01-02", 1.0, 2.0, 0.5, 1.5), ("2020-01-03", 2.0, 3.0, 1.0, 2.5)])

    def test_stooq_html_wall_is_not_a_csv(self):
        self.assertIsNone(ip.parse_ohlc_csv(b"<!DOCTYPE html><html>verify your browser</html>"))
        self.assertIsNone(ip.parse_ohlc_csv(b""))
        self.assertIsNone(ip.parse_ohlc_csv(b"Date,Price\n2020-01-02,1\n"))  # no OHLC columns

    def test_fred(self):
        body = b"observation_date,DTB3\n1993-12-01,3.10\n1993-12-02,.\n1993-12-03,3.12\n"
        got = ip.parse_fred(body)
        self.assertEqual(got, [("1993-12-01", 3.10), ("1993-12-02", None), ("1993-12-03", 3.12)])
        self.assertEqual(ip.dtb3_missing(got)["missing"], 1)
        self.assertEqual(ip.dtb3_missing(got)["missing_first"], ["1993-12-02"])
        self.assertIsNone(ip.parse_fred(b"<html></html>"))

    def test_yahoo_adjusts_ohlc_by_adjclose(self):
        ts = 1577975400  # 2020-01-02 14:30 UTC
        js = {"chart": {"result": [{"timestamp": [ts], "events": {"dividends": {str(ts): {"amount": 1.5}}},
                                    "indicators": {"quote": [{"open": [100.0], "high": [110.0], "low": [90.0],
                                                              "close": [100.0]}],
                                                   "adjclose": [{"adjclose": [50.0]}]}}]}}
        got, divs = ip.parse_yahoo(json.dumps(js).encode())
        self.assertEqual(got, [("2020-01-02", 50.0, 55.0, 45.0, 50.0)])
        self.assertEqual(divs, {"2020-01-02": 1.5})
        self.assertIsNone(ip.parse_yahoo(b"{}"))


class Checks(unittest.TestCase):
    def test_integrity(self):
        good = rows([10, 11, 12])
        self.assertEqual(ip.integrity(good), {"rows": 3, "first": "2020-01-01", "last": "2020-01-03", "non_positive": 0,
                                              "outside_range": 0, "duplicate_or_unsorted": 0})
        bad = good + [("2020-01-04", 5.0, 6.0, 4.0, 7.0), ("2020-01-04", 0.0, 1.0, 0.0, 0.5)]
        got = ip.integrity(bad)
        self.assertEqual((got["outside_range"], got["non_positive"], got["duplicate_or_unsorted"]), (1, 1, 1))

    def test_agreement_by_hand(self):
        a = rows([100.0, 101.0, 102.0, 103.0])
        b = [(d, o / 2, h / 2, lo / 2, c / 2) for d, o, h, lo, c in a]  # back-adjusted by 0.5: the same changes
        got = ip.agreement(a, b, "2020-01-01", "2020-01-31")
        self.assertEqual((got["common"], got["n_only_a"], got["n_only_b"]), (4, 0, 0))
        self.assertEqual(got["change_within"], 1.0)
        self.assertEqual(got["oc_within"], 1.0)
        self.assertTrue(got["pass"])
        # one close moved by 0.1% in b: two changes (into and out of that day) differ by about 0.001 > 0.0005
        b2 = list(b)
        b2[2] = (b[2][0], b[2][1], b[2][2] * 1.01, b[2][3], b[2][4] * 1.001)
        got = ip.agreement(a, b2, "2020-01-01", "2020-01-31")
        self.assertAlmostEqual(got["change_within"], 1 / 3)  # of the three changes, only day 2's agrees
        self.assertAlmostEqual(got["oc_within"], 1.0)  # open/close moves by about 0.001 <= 0.0015
        self.assertFalse(got["pass"])
        self.assertEqual({d for _, d in got["largest_change_diff"][:2]}, {"2020-01-03", "2020-01-04"})

    def test_agreement_needs_the_same_days(self):
        a = rows([100.0, 101.0, 102.0])
        b = a[:1] + a[2:]
        got = ip.agreement(a, b, "2020-01-01", "2020-01-31")
        self.assertEqual((got["n_only_a"], got["only_a"]), (1, ["2020-01-02"]))
        self.assertFalse(got["pass"])

    def test_main_stores_no_yahoo_and_no_alpaca_data(self):
        stooq = b"Date,Open,High,Low,Close,Volume\n2016-01-04,1,2,0.5,1.5,1\n"
        fred = b"observation_date,DTB3\n2016-01-04,0.2\n"

        def fetch(url, timeout=90):
            if "stooq" in url:
                return 200, "text/csv", stooq
            if "fred" in url:
                return 200, "text/csv", fred
            return 200, "application/json", json.dumps({"chart": {"result": [{"timestamp": [1451917800],
                "indicators": {"quote": [{"open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5]}],
                               "adjclose": [{"adjclose": [1.5]}]}}]}}).encode()
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ip, "fetch", side_effect=fetch), \
                mock.patch.object(ip, "alpaca_adjusted", return_value=[("2016-01-04", 1.0, 2.0, 0.5, 1.5)]), \
                mock.patch("builtins.print"):
            self.assertEqual(ip.main([d]), 0)
            self.assertEqual(sorted(os.listdir(d)), ["fred_dtb3.csv", "probe.json", "stooq_bil.csv", "stooq_spy.csv"])
            rep = json.load(open(os.path.join(d, "probe.json"), encoding="utf-8"))
        self.assertFalse(rep["sources"]["yahoo_spy"]["stored"])
        self.assertFalse(rep["sources"]["alpaca_spy"]["stored"])
        self.assertIn("stooq_spy_vs_alpaca_2016_2026", rep["agreement"])


if __name__ == "__main__":
    unittest.main()
