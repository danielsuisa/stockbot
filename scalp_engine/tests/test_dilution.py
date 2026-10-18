"""dilution_filing@2.0.0 (ADR 0004): fail closed on a missing CIK, 424B2 excluded, availability respected."""
import datetime as dt
import unittest

import polars as pl

from scalp_engine import dilution

UTC = dt.timezone.utc


def utc(*a):
    return dt.datetime(*a, tzinfo=UTC)


class DilutionV2(unittest.TestCase):
    def setUp(self):
        self.t = utc(2025, 6, 2, 14, 0)
        self.filings = pl.DataFrame({
            "cik": ["0001", "0002", "0003", "0004", "0005"],
            "event_at": [self.t - dt.timedelta(days=3), self.t - dt.timedelta(days=3), self.t - dt.timedelta(days=31),
                         self.t - dt.timedelta(seconds=30), self.t - dt.timedelta(days=1)],
            "form": ["424B5", "424B2", "S-3", "S-1", "S-3ASR"]},
            schema={"cik": pl.Utf8, "event_at": pl.Datetime("us", "UTC"), "form": pl.Utf8})
        self.cik_of = {"A": "0001", "B": "0002", "C": "0003", "D": "0004", "E": "0005"}

    def flags(self):
        out = dilution.dilution_v2(self.filings, self.cik_of, ["A", "B", "C", "D", "E", "NOCIK"], [self.t])
        return {r["symbol"]: (r["dilution_v2"], r["pass_dilution_v2"]) for r in out.iter_rows(named=True)}

    def test_rules(self):
        f = self.flags()
        self.assertEqual(f["A"], ("true", False))  # 424B5 within 30 days
        self.assertEqual(f["B"], ("false", True))  # 424B2 no longer counts
        self.assertEqual(f["C"], ("false", True))  # older than 30 days
        self.assertEqual(f["D"], ("false", True))  # accepted 30 s ago: not available yet (60 s delay)
        self.assertEqual(f["E"], ("true", False))  # S-3ASR
        self.assertEqual(f["NOCIK"], ("unknown", False))  # fail closed

    def test_forms(self):
        for form in ("S-1", "S-1/A", "S-3", "S-3/A", "S-3ASR", "424B1", "424B3", "424B4", "424B5", "424B7", "424B8"):
            self.assertTrue(dilution._FORMS.match(form), form)
        for form in ("424B2", "S-8", "S-4", "424H", "F-3"):
            self.assertFalse(dilution._FORMS.match(form), form)


if __name__ == "__main__":
    unittest.main()
