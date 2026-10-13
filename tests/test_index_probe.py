"""Index program phase 1 (spec 2026-10-12, Amendments 1 to 3: C1 sources, C3 acceptance): the probe's parsers and
checks on SYNTHETIC prices only. Network mocked; no return of any rule is computed."""
import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from unittest import mock

from bot import index_probe as ip


def ohlc(day, c, o=None):
    o = c if o is None else o
    return (day, o, max(o, c) + 1, min(o, c) - 1, c)


def h15_zip(obs):
    xml = '<kf:Series SERIES_NAME="RIFSGFSM03_N.B">' + "".join(
        f'<frb:Obs OBS_STATUS="{s}" OBS_VALUE="{v}" TIME_PERIOD="{d}" />' for d, s, v in obs) + "</kf:Series>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("H15_data.xml", '<x><kf:Series SERIES_NAME="OTHER"><frb:Obs OBS_STATUS="A" OBS_VALUE="9" '
                                   'TIME_PERIOD="2005-01-03" /></kf:Series>' + xml + "</x>")
    return buf.getvalue()


class Parsers(unittest.TestCase):
    def test_stooq_semicolon_and_comma(self):
        semi = b"Date;Open;High;Low;Close;Volume\n2005-02-28;2;3;1;2.5;10\n2005-02-25;1;2;0.5;1.5;10\n"
        want = [("2005-02-25", 1.0, 2.0, 0.5, 1.5), ("2005-02-28", 2.0, 3.0, 1.0, 2.5)]
        self.assertEqual(ip.parse_stooq(semi), want)
        self.assertEqual(ip.parse_stooq(semi.replace(b";", b",")), want)
        self.assertIsNone(ip.parse_stooq(b"Access denied"))
        self.assertIsNone(ip.parse_stooq(b""))

    def test_h15(self):
        rows = ip.parse_h15(h15_zip([("1993-12-01", "A", "3.10"), ("1993-12-24", "ND", "-9999"),
                                     ("1993-12-27", "A", "3.12")]))
        self.assertEqual(rows, [("1993-12-01", 3.10), ("1993-12-24", None), ("1993-12-27", 3.12)])
        text = ip.h15_csv(rows).decode()
        self.assertTrue(text.startswith("# source: Board of Governors of the Federal Reserve System"))
        self.assertIn("date,value\n1993-12-01,3.1\n1993-12-24,\n1993-12-27,3.12\n", text)
        got = ip.h15_check(rows)
        self.assertEqual((got["rows"], got["missing"], got["missing_days"]), (3, 1, ["1993-12-24"]))


class C31C32(unittest.TestCase):
    def test_integrity_with_tolerance(self):
        self.assertTrue(ip.integrity([ohlc("2020-01-02", 10.0), ohlc("2020-01-03", 11.0)])["pass"])
        tiny = [("2020-01-03", 10.0, 10.0, 9.0, 10.0 * (1 + 1e-12))]
        self.assertTrue(ip.integrity(tiny)["pass"])  # within 1e-9
        self.assertFalse(ip.integrity(tiny, rel=0.0)["pass"])
        self.assertFalse(ip.integrity([("2020-01-03", 10.0, 10.0, 9.0, 10.1)])["pass"])
        self.assertEqual(ip.integrity([("2020-01-03", 0.0, 1.0, 0.0, 0.5)])["non_positive"], 1)
        self.assertFalse(ip.integrity([ohlc("2020-01-03", 1.0), ohlc("2020-01-02", 1.0)])["pass"])  # unsorted

    def test_holidays_and_closures(self):
        days, closed = ip.nyse_days("2005-01-01", "2026-12-31")
        days = set(days)
        for d in ("2007-01-02", "2012-10-29", "2012-10-30", "2018-12-05", "2025-01-09",  # special closures
                  "2022-06-20", "2023-06-19", "2026-06-19",  # Juneteenth (2022 observed on Monday)
                  "2010-12-24", "2021-12-24", "2015-04-03", "2026-04-03", "2009-01-19", "2017-01-02"):
            self.assertNotIn(d, days, d)
        for d in ("2010-12-31", "2021-12-31", "2021-06-18", "2012-10-31"):  # New Year's Day on a Saturday is not
            self.assertIn(d, days, d)  # moved back; Juneteenth only from 2022
        self.assertEqual(closed["2012-10-29"], "Hurricane Sandy")

    def test_calendar_check(self):
        days, _ = ip.nyse_days("2012-10-22", "2012-11-09")
        self.assertTrue(ip.calendar_check(days, "2012-10-22", "2012-11-09")["pass"])
        bad = ip.calendar_check(days[:-1] + ["2012-10-29"], "2012-10-22", "2012-11-09")
        self.assertEqual(bad["in_file_not_calendar"], [["2012-10-29", "Hurricane Sandy"]])
        self.assertEqual(bad["in_calendar_not_file"], [["2012-11-09", None]])
        self.assertEqual(bad["unexplained"], 1)
        self.assertFalse(bad["pass"])


class C33C34(unittest.TestCase):
    def series(self):
        """Alpaca raw and adjusted: a dividend of 1 on day 3 (raw falls 1 more than the adjusted series)."""
        raw = [ohlc("2020-01-02", 100.0), ohlc("2020-01-03", 101.0), ohlc("2020-01-06", 100.0), ohlc("2020-01-07", 102.0)]
        f = 1 - 1 / 101  # the factor before the ex-day
        adj = [(d, o * f, h * f, lo * f, c * f) if d < "2020-01-06" else (d, o, h, lo, c) for d, o, h, lo, c in raw]
        return raw, adj

    def test_dividend_check_by_hand(self):
        raw, adj = self.series()
        got = ip.dividend_check(adj, raw, adj, "2020-01-01", "2020-12-31")  # a file equal to the adjusted bars
        self.assertEqual((got["ex_dividend_days"], got["ex_days"], got["nearer_share"]), (1, ["2020-01-06"], 1.0))
        self.assertEqual(got["span_relative_difference"], 0.0)
        self.assertTrue(got["pass"])
        bad = ip.dividend_check(raw, raw, adj, "2020-01-01", "2020-12-31")  # a raw file: nearer the raw change
        self.assertEqual(bad["nearer_share"], 0.0)
        self.assertFalse(bad["pass"])

    def test_gross_errors_by_hand(self):
        _, adj = self.series()
        scaled = [(d, o * 3, h * 3, lo * 3, c * 3) for d, o, h, lo, c in adj]  # another scale: the same changes
        got = ip.gross_errors(scaled, adj, "2020-01-01", "2020-12-31")
        self.assertEqual((got["common"], got["change_within"], got["oc_within"], got["pass"]), (4, 1.0, 1.0, True))
        off = list(scaled)
        off[2] = (off[2][0], off[2][1], off[2][2] * 1.1, off[2][3], off[2][4] * 1.01)  # one close 1% off
        got = ip.gross_errors(off, adj, "2020-01-01", "2020-12-31")
        self.assertAlmostEqual(got["change_within"], 1 / 3, places=6)  # into and out of that day: about 0.01
        self.assertAlmostEqual(got["oc_within"], 0.75)  # that day's open/close moves by about 0.0099 > 0.005
        self.assertFalse(got["pass"])
        self.assertEqual(got["largest_oc"][0][0], "2020-01-06")
        missing = ip.gross_errors(scaled[:-1], adj, "2020-01-01", "2020-12-31")
        self.assertEqual(missing["only_alpaca"], ["2020-01-07"])
        self.assertFalse(missing["pass"])

    def test_information_lists(self):
        rows = [ohlc("2008-10-10", 100.0), ohlc("2008-10-13", 112.0, o=100.0), ohlc("2008-10-14", 111.0, o=110.0)]
        self.assertEqual(ip.big_moves(rows), [["2008-10-13", 0.12]])
        self.assertEqual(ip.open_equals_prev_close(rows), {"2008": 0.5})


class Run(unittest.TestCase):
    def test_run_end_to_end_and_sha256_stop(self):
        days, _ = ip.nyse_days("2016-01-04", "2016-01-29")
        rows = [ohlc(d, 200.0 + k) for k, d in enumerate(days)]
        body = b"Date;Open;High;Low;Close;Volume\n" + b"".join(
            f"{d};{o};{h};{lo};{c};1\n".encode() for d, o, h, lo, c in rows)
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(ip, "STOOQ_SHA256", hashlib.sha256(body).hexdigest()), \
                mock.patch.object(ip, "FIRST", "2016-01-04"), mock.patch.object(ip, "LAST", "2016-01-29"), \
                mock.patch.object(ip, "fetch", return_value=(200, h15_zip([("2016-01-04", "A", "0.2")]))), \
                mock.patch.object(ip, "alpaca_bars", return_value=rows), mock.patch("builtins.print"):
            path = os.path.join(d, "spy_us_d.csv")
            with open(path, "wb") as fh:
                fh.write(body)
            out = os.path.join(d, "out")
            code = ip.main(["--stooq", path, "--out", out])
            names = sorted(os.listdir(out))
            rep = json.load(open(os.path.join(out, "probe.json"), encoding="utf-8"))
            written = "".join(open(os.path.join(out, n), encoding="utf-8").read() for n in names)
        self.assertEqual(names, ["h15-3m-tbill.csv", "manifest.json", "probe.json"])
        self.assertTrue(rep["checks"]["1_integrity"]["pass"])
        self.assertTrue(rep["checks"]["2_calendar"]["pass"])
        self.assertIn("4_gross_errors", rep["checks"])
        self.assertIn(code, (0, 4))  # synthetic: no ex-dividend day, so check 3 cannot pass
        self.assertNotIn("201.0;", written)  # no price of the file leaves the run
        with tempfile.TemporaryDirectory() as d, mock.patch("builtins.print"):
            path = os.path.join(d, "spy_us_d.csv")
            with open(path, "wb") as fh:
                fh.write(body + b"x")
            self.assertEqual(ip.main(["--stooq", path, "--out", os.path.join(d, "out")]), 2)  # sha256 differs


if __name__ == "__main__":
    unittest.main()
