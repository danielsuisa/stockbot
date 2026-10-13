"""Index program phase 1 (spec 2026-10-12 sections 3, 8.1, Amendments 1 and 2): the probe's parsers and checks on
SYNTHETIC prices only. Network mocked; no return of any rule is computed."""
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


def tiingo_row(day, c, div=0.0, split=1.0, o=None):
    o = c if o is None else o
    return (day, o, max(o, c) + 1, min(o, c) - 1, c, div, split, o, max(o, c) + 1, min(o, c) - 1, c)


class Parsers(unittest.TestCase):
    def test_stooq_semicolon_and_comma(self):
        semi = b"Date;Open;High;Low;Close;Volume\n2005-02-28;2;3;1;2.5;10\n2005-02-25;1;2;0.5;1.5;10\n"
        want = [("2005-02-25", 1.0, 2.0, 0.5, 1.5), ("2005-02-28", 2.0, 3.0, 1.0, 2.5)]
        self.assertEqual(ip.parse_stooq(semi), want)
        self.assertEqual(ip.parse_stooq(semi.replace(b";", b",")), want)
        self.assertIsNone(ip.parse_stooq(b"Access denied"))
        self.assertIsNone(ip.parse_stooq(b""))

    def test_tiingo_key_only_in_the_header(self):
        body = json.dumps([{"date": "1993-01-29T00:00:00.000Z", "open": 43.97, "high": 43.97, "low": 43.75,
                            "close": 43.94, "divCash": 0, "splitFactor": 1, "adjOpen": 1, "adjHigh": 1, "adjLow": 1,
                            "adjClose": 1}]).encode()
        with mock.patch.object(ip, "fetch", return_value=(200, body)) as f:
            status, rows = ip.tiingo("SPY", "SECRETKEY")
        url, = f.call_args.args
        self.assertNotIn("SECRETKEY", url)
        self.assertEqual(f.call_args.kwargs["headers"]["Authorization"], "Token SECRETKEY")
        self.assertEqual(status, 200)
        self.assertEqual(rows[0][:7], ("1993-01-29", 43.97, 43.97, 43.75, 43.94, 0.0, 1.0))
        with mock.patch.object(ip, "fetch", return_value=(401, b"")):
            self.assertEqual(ip.tiingo("SPY", "x"), (401, None))

    def test_h15(self):
        xml = ('<x><kf:Series SERIES_NAME="OTHER"><frb:Obs OBS_STATUS="A" OBS_VALUE="9.9" TIME_PERIOD="1993-12-01" />'
               '</kf:Series><kf:Series SERIES_NAME="RIFSGFSM03_N.B">'
               '<frb:Obs OBS_STATUS="A" OBS_VALUE="3.10" TIME_PERIOD="1993-12-01" />'
               '<frb:Obs OBS_STATUS="ND" OBS_VALUE="-9999" TIME_PERIOD="1993-12-24" />'
               '<frb:Obs OBS_STATUS="A" OBS_VALUE="3.12" TIME_PERIOD="1993-12-27" /></kf:Series></x>')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("H15_data.xml", xml)
        rows = ip.parse_h15(buf.getvalue())
        self.assertEqual(rows, [("1993-12-01", 3.10), ("1993-12-24", None), ("1993-12-27", 3.12)])
        text = ip.h15_csv(rows).decode()
        self.assertTrue(text.startswith("# source: Board of Governors"))
        self.assertIn("date,value\n1993-12-01,3.1\n1993-12-24,\n1993-12-27,3.12\n", text)


class B1B2(unittest.TestCase):
    def test_fingerprint_per_year(self):
        rows = [tiingo_row("1999-12-31", 10.0), tiingo_row("2000-01-03", 11.0)]
        fp = ip.fingerprint(rows)
        self.assertEqual(set(fp), {"1999", "2000"})
        import hashlib
        self.assertEqual(fp["1999"], hashlib.sha256(b"1999-12-31,10.0,11.0,9.0,10.0,0.0,1.0\n").hexdigest())
        changed = [rows[0], tiingo_row("2000-01-03", 11.0, div=0.1)]
        self.assertEqual(ip.fingerprint_diff(ip.fingerprint(changed), fp), ["2000"])
        self.assertEqual(ip.fingerprint_diff(fp, {"1999": fp["1999"]}), ["2000"])

    def test_adjust_by_hand(self):
        rows = [tiingo_row("2020-01-01", 100.0), tiingo_row("2020-01-02", 100.0, div=1.0),
                tiingo_row("2020-01-03", 50.0, div=2.0), tiingo_row("2020-01-06", 60.0)]
        adj = ip.adjust(rows)
        # day 3: factor 1 - 2 / 100 (raw close of day 2) applies to days 1 and 2; day 2: 1 - 1 / 100 to day 1
        self.assertAlmostEqual(adj[0][4], 100 * 0.99 * 0.98)
        self.assertAlmostEqual(adj[1][4], 100 * 0.98)
        self.assertEqual(adj[2][4], 50.0)
        self.assertEqual(adj[3][4], 60.0)
        self.assertAlmostEqual(adj[0][1], rows[0][1] * 0.99 * 0.98)  # every price of the day, not only the close

    def test_split_stops_the_run(self):
        with self.assertRaises(ip.SplitFound) as e:
            ip.adjust([tiingo_row("2020-01-01", 10.0), tiingo_row("2020-01-02", 5.0, split=2.0)], symbol="BIL")
        self.assertEqual(str(e.exception), "BIL 2020-01-02")  # the stop names the symbol and the day


class Checks(unittest.TestCase):
    def test_integrity_with_tolerance(self):
        good = [ohlc("2020-01-01", 10.0), ohlc("2020-01-02", 11.0)]
        self.assertEqual(ip.integrity(good)["outside_range"], 0)
        tiny = [("2020-01-03", 10.0, 10.0, 9.0, 10.0 * (1 + 1e-12))]
        self.assertEqual(ip.integrity(tiny)["outside_range"], 1)
        self.assertEqual(ip.integrity(tiny, rel=1e-9)["outside_range"], 0)
        self.assertEqual(ip.integrity([("2020-01-03", 0.0, 1.0, 0.0, 0.5)])["non_positive"], 1)

    def test_dividends_per_year(self):
        rows = [tiingo_row(f"2001-{m:02d}-15", 10.0, div=0.1) for m in (3, 6, 9, 12)]
        rows += [tiingo_row("2002-03-15", 10.0, div=0.1), tiingo_row("2002-06-14", 10.0)]
        got = ip.dividends_per_year(rows, "2001-01-01", "2002-12-31")
        self.assertEqual(got["per_year"], {"2001": 4, "2002": 1})
        self.assertEqual(got["not_4"], {"2002": 1})

    def test_agreement_by_hand(self):
        a = [ohlc("2020-01-01", 100.0), ohlc("2020-01-02", 101.0), ohlc("2020-01-03", 102.0), ohlc("2020-01-06", 103.0)]
        b = [(d, o / 2, h / 2, lo / 2, c / 2) for d, o, h, lo, c in a]  # scaled: the same changes and ratios
        got = ip.agreement(a, b, "2020-01-01", "2020-12-31")
        self.assertEqual((got["days"], got["change_within"], got["oc_within"], got["pass"]), (4, 1.0, 1.0, True))
        b[2] = (b[2][0], b[2][1], b[2][2] * 1.01, b[2][3], b[2][4] * 1.001)  # one close 0.1% off
        got = ip.agreement(a, b, "2020-01-01", "2020-12-31")
        self.assertAlmostEqual(got["change_within"], 1 / 3)  # the changes into and out of that day differ
        self.assertEqual(got["oc_within"], 1.0)  # about 0.001 <= 0.0015
        self.assertFalse(got["pass"])
        self.assertEqual({d for d, _ in got["largest_change"][:2]}, {"2020-01-03", "2020-01-06"})

    def test_day_diff(self):
        got = ip.day_diff(["2020-01-01", "2020-01-02"], ["2020-01-02", "2020-01-03"], "2020-01-01", "2020-01-31")
        self.assertEqual((got["only_a"], got["only_b"]), (["2020-01-01"], ["2020-01-03"]))

    def test_stooq_adjustment_detected(self):
        raw = [ohlc("2020-01-01", 100.0), ohlc("2020-01-02", 99.0), ohlc("2020-01-03", 99.5)]
        divs = [tiingo_row("2020-01-01", 100.0), tiingo_row("2020-01-02", 99.0, div=1.0), tiingo_row("2020-01-03", 99.5)]
        adj = ip.adjust(divs)  # day 1 becomes 99: the ex-day change is 0 after adjustment
        self.assertTrue(ip.stooq_adjusted(adj, raw, adj, ["2020-01-02"])["adjusted"])
        self.assertFalse(ip.stooq_adjusted(raw, raw, adj, ["2020-01-02"])["adjusted"])


class Calendar(unittest.TestCase):
    def test_holidays_and_closures(self):
        days, closed = ip.nyse_days("1993-01-01", "2005-12-31")
        days = set(days)
        for d in ("2001-09-11", "2001-09-14", "1994-04-27", "2004-06-11", "1998-04-10", "1998-01-19", "1998-07-03",
                  "1999-12-24", "1994-12-26", "1995-01-02", "1993-11-25", "2000-05-29"):
            self.assertNotIn(d, days, d)
        for d in ("1997-01-20", "1999-12-31", "2001-09-17", "2004-06-14"):  # MLK not a holiday before 1998; New
            self.assertIn(d, days, d)  # Year's Day on a Saturday is not moved to Friday
        self.assertEqual(closed["1998-04-10"], "Good Friday")

    def test_calendar_check(self):
        days, _ = ip.nyse_days("2001-09-04", "2001-09-21")
        ok = ip.calendar_check(days, "2001-09-04", "2001-09-21")
        self.assertEqual(ok["unexplained"], 0)
        bad = ip.calendar_check(days[:-1] + ["2001-09-12"], "2001-09-04", "2001-09-21")
        self.assertEqual(bad["in_file_not_calendar"], [["2001-09-12", "September 11 attacks"]])
        self.assertEqual(bad["in_calendar_not_file"], [["2001-09-21", None]])
        self.assertEqual(bad["unexplained"], 1)  # the missing day; the extra day is a documented closure (listed)

    def test_information_lists(self):
        adj = [ohlc("1999-01-04", 100.0), ohlc("1999-01-05", 106.0), ohlc("1999-01-06", 104.0)]
        self.assertEqual(ip.big_moves(adj, "1999-01-01", "1999-12-31"), [["1999-01-05", 0.06]])
        raw = [ohlc("1999-01-04", 100.0), ("1999-01-05", 100.0, 107, 99, 106.0), ("1999-01-06", 105.0, 107, 103, 104.0)]
        self.assertEqual(ip.open_equals_prev_close(raw, "1999-01-01", "1999-12-31"), {"1999": 0.5})


class Run(unittest.TestCase):
    def test_run_writes_no_tiingo_price(self):
        spy = [tiingo_row(d, 1234.5678 + k, div=0.4321 if k == 2 else 0.0) for k, d in
               enumerate(("2005-02-25", "2005-02-28", "2005-03-01", "2005-03-02"))]
        stooq = b"Date;Open;High;Low;Close\n" + b"".join(
            f"{r[0]};{r[1]};{r[2]};{r[3]};{r[4]}\n".encode() for r in spy)
        h15 = io.BytesIO()
        with zipfile.ZipFile(h15, "w") as z:
            z.writestr("H15_data.xml", '<kf:Series SERIES_NAME="RIFSGFSM03_N.B"><frb:Obs OBS_STATUS="A" '
                                       'OBS_VALUE="2.5" TIME_PERIOD="2005-02-25" /></kf:Series>')
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(ip, "tiingo", side_effect=lambda s, t, start=None, end=None: (200, spy)), \
                mock.patch.object(ip, "fetch", return_value=(200, h15.getvalue())), \
                mock.patch.object(ip, "alpaca_adjusted", return_value=[r[:5] for r in spy]), \
                mock.patch.object(ip, "FIRST", "2005-02-25"), mock.patch.object(ip, "SINGLE_LAST", "2005-02-24"):
            path = os.path.join(d, "spy_us_d.csv")
            with open(path, "wb") as fh:
                fh.write(stooq)
            out = os.path.join(d, "out")
            with mock.patch("builtins.print"):
                code = ip.main(["--stooq", path, "--out", out])
            names = sorted(os.listdir(out))
            text = "".join(open(os.path.join(out, n), encoding="utf-8").read() for n in names)
        self.assertEqual(names, ["h15-3m-tbill.csv", "manifest.json", "probe.json", "tiingo-fingerprint.json"])
        self.assertNotIn("1234.5678", text)  # no Tiingo price leaves the run
        self.assertNotIn("0.4321", text)
        self.assertIn(code, (0, 4))  # synthetic: the checks run end to end


if __name__ == "__main__":
    unittest.main()
