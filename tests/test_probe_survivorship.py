"""Phase A.1 of the recommendation algorithm (spec 2026-10-10 revision 6, section 7 A.1): survivorship stock-months,
the rename rule, f and b. Counts only: no return of any ticket or stock is computed anywhere in the code under test."""
import unittest
from unittest import mock

from bot import probe_ensemble_data as pd, probe_survivorship as ps, shorts
from tests.test_probe_ensemble import make_zip, sub, tx


class Readers(unittest.TestCase):
    def test_transaction_prices(self):
        z = make_zip({
            "SUBMISSION": [sub("a", "4"), sub("b", "5", filed="06-JAN-2017"), sub("c", "4/A"), sub("d", "3"),
                           sub("e", "4", symbol="NONE")],
            "NONDERIV_TRANS": [tx("a", code="S", ad="D", price="12"), tx("a", code="A", price="0"),
                               tx("b", code="G", ad="D", price="7", date="04-JAN-2017"), tx("c", price="30"),
                               tx("d", price="31"), tx("e", price="32")]})
        self.assertEqual(sorted(pd.transaction_prices(z)),
                         [("2017-01-05", "ACME", "2017-01-03", "a", 12.0),  # the $0 grant is not a price
                          ("2017-01-06", "ACME", "2017-01-04", "b", 7.0)])  # 4/A, Form 3 and NONE dropped

    def test_finra_spellings_and_row(self):
        self.assertEqual(ps.finra_spellings("BRK.B"), ("BRK-B", "BRKB"))
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


if __name__ == "__main__":
    unittest.main()
