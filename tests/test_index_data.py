"""Index program phase 2, task 1 (spec section 3, A2, C1, D1): reading the files with their sha256 and the cash leg.
SYNTHETIC files only."""
import hashlib
import json
import os
import tempfile
import unittest

from bot import index_data as idata


def write(d, name, body):
    path = os.path.join(d, name)
    with open(path, "wb") as fh:
        fh.write(body)
    return path


def manifest(d, **files):
    m = {name: {"sha256": hashlib.sha256(body).hexdigest()} for name, body in files.items()}
    return write(d, "manifest.json", json.dumps(m).encode())


STOOQ = (b"Date;Open;High;Low;Close;Volume\n2026-09-29;1;2;0.5;1.5;1\n2026-09-30;1;2;0.5;1.5;1\n"
         b"2026-10-01;1;2;0.5;1.5;1\n")
H15 = b"# source: Board\ndate,value\n2026-09-28,4.0\n2026-09-29,\n2026-09-30,4.1\n"


class Files(unittest.TestCase):
    def test_sha256_mismatch_stops(self):
        with tempfile.TemporaryDirectory() as d:
            m = manifest(d, **{"spy_us_d.csv": STOOQ, "h15-3m-tbill.csv": H15})
            p = write(d, "spy_us_d.csv", STOOQ + b"x")
            with self.assertRaises(idata.DataMismatch):
                idata.load_prices(p, m)
            h = write(d, "h15-3m-tbill.csv", H15.replace(b"4.1", b"4.2"))
            with self.assertRaises(idata.DataMismatch):
                idata.load_h15(h, m)

    def test_rows_after_last_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            m = manifest(d, **{"spy_us_d.csv": STOOQ})
            rows = idata.load_prices(write(d, "spy_us_d.csv", STOOQ), m)
        self.assertEqual([r[0] for r in rows], ["2026-09-29", "2026-09-30"])  # C1: 2026-10-01 is ignored

    def test_h15_file(self):
        with tempfile.TemporaryDirectory() as d:
            m = manifest(d, **{"h15-3m-tbill.csv": H15})
            got = idata.load_h15(write(d, "h15-3m-tbill.csv", H15), m)
        self.assertEqual(got, {"2026-09-28": 4.0, "2026-09-29": None, "2026-09-30": 4.1})


class CashLeg(unittest.TestCase):
    def test_cash_return_by_hand(self):
        days = ["2020-01-03", "2020-01-06"]  # Friday to Monday: d = 3
        got = idata.cash_returns(days, {"2020-01-03": 4.0, "2020-01-06": 9.9})
        self.assertAlmostEqual(got["2020-01-06"], 0.04 * 3 / 360 - 0.0014 * 3 / 365)
        self.assertEqual(got["2020-01-03"], 0.0)  # the file's first day has no previous trading day

    def test_missing_h15_takes_previous(self):
        days = ["2020-10-09", "2020-10-12", "2020-10-13"]  # 2020-10-12: a bond holiday, stocks traded
        got = idata.cash_returns(days, {"2020-10-09": 0.10, "2020-10-12": None, "2020-10-13": 0.11})
        self.assertAlmostEqual(got["2020-10-13"], 0.0010 * 1 / 360 - 0.0014 * 1 / 365)  # 2020-10-09's value

    def test_d1_gap(self):
        days = ["2011-02-16", "2011-02-18"]  # 2011-02-17 is not a trading day of the program (D1)
        got = idata.cash_returns(days, {"2011-02-16": 0.12, "2011-02-17": 0.13})
        self.assertAlmostEqual(got["2011-02-18"], 0.0012 * 2 / 360 - 0.0014 * 2 / 365)  # d = 2; DTB3 of 02-16


if __name__ == "__main__":
    unittest.main()
