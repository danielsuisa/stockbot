"""Squeeze screener data layer: FINRA short interest, SEC shares, point-in-time rules."""
import datetime as dt
import unittest
from unittest import mock

from bot import shorts


def weekdays(start, n, skip=()):
    out, d = [], dt.date.fromisoformat(start)
    while len(out) < n:
        if d.weekday() < 5 and d.isoformat() not in skip:
            out.append(d.isoformat())
        d += dt.timedelta(1)
    return out


class Norm(unittest.TestCase):
    def test_class_suffix_matches_sec_and_yahoo(self):
        self.assertEqual(shorts.norm("BRK.B"), "BRK-B")
        self.assertEqual(shorts.norm(" gme "), "GME")


class ToSec(unittest.TestCase):
    def test_finra_class_share_without_separator_maps_to_sec_ticker(self):
        known = {"BRK-B", "AAPL", "MOG-A"}
        self.assertEqual(shorts.to_sec("BRKB", known), "BRK-B")
        self.assertEqual(shorts.to_sec("MOGA", known), "MOG-A")
        self.assertEqual(shorts.to_sec("AAPL", known), "AAPL")
        self.assertIsNone(shorts.to_sec("ABRPRD", known))  # a preferred: no SEC common ticker
        # unknown symbols stay under their FINRA name, so the coverage table still counts them
        self.assertEqual(shorts.remap({"BRKB": {"si": 1}, "ZZZZ": {"si": 2}}, known),
                         {"BRK-B": {"si": 1}, "ZZZZ": {"si": 2}})


class Slim(unittest.TestCase):
    def test_fields_and_nulls(self):
        r = {"currentShortPositionQuantity": 500, "daysToCoverQuantity": None, "changePercent": None,
             "averageDailyVolumeQuantity": 10, "marketClassCode": "SC", "settlementDate": "2026-09-15"}
        self.assertEqual(shorts.slim(r), {"si": 500, "dtc": 0.0, "chg": None, "adv": 10, "cls": "SC",
                                          "date": "2026-09-15"})
        self.assertAlmostEqual(shorts.slim({**r, "changePercent": 15.49})["chg"], 0.1549)


class Report(unittest.TestCase):
    def test_pages_until_short_page_and_normalizes(self):
        calls = []

        def post(body):
            calls.append(body)
            n = shorts.PAGE if body["offset"] == 0 else 3
            return [{"symbolCode": f"S{body['offset'] + k}", "currentShortPositionQuantity": 1,
                     "marketClassCode": "NNM", "settlementDate": "2026-09-15"} for k in range(n)]
        with mock.patch.object(shorts, "_post", post):
            out = shorts.report("2026-09-15")
        self.assertEqual(len(out), shorts.PAGE + 3)
        self.assertEqual([c["offset"] for c in calls], [0, shorts.PAGE])
        self.assertEqual(calls[0]["domainFilters"][0]["values"], list(shorts.CLASSES))

    def test_no_content_is_empty(self):
        with mock.patch.object(shorts.common, "fetch", return_value=b""):
            self.assertEqual(shorts._post({}), [])

    def test_settlement_dates_sorted_unique(self):
        rows = [{"settlementDate": d} for d in ("2026-09-15", "2018-01-12", "2026-09-15")]
        with mock.patch.object(shorts, "_post", return_value=rows):
            self.assertEqual(shorts.settlement_dates(), ["2018-01-12", "2026-09-15"])

    def test_symbol_rows_oldest_first(self):
        rows = [{"symbolCode": "BRKB", "settlementDate": d, "currentShortPositionQuantity": 5}
                for d in ("2026-09-15", "2026-08-29")]
        with mock.patch.object(shorts, "_post", return_value=rows) as post:
            got = shorts.symbol_rows("BRKB")
        self.assertEqual([r["date"] for r in got], ["2026-08-29", "2026-09-15"])
        self.assertEqual(post.call_args[0][0]["compareFilters"][0]["fieldValue"], "BRKB")

class UsableFrom(unittest.TestCase):
    def test_eighth_session_after_settlement(self):
        cal = weekdays("2026-09-01", 30)
        self.assertEqual(shorts.usable_from("2026-09-15", cal), "2026-09-25")

    def test_holiday_inside_lag_pushes_later(self):
        cal = weekdays("2026-09-01", 30, skip={"2026-09-21"})
        self.assertEqual(shorts.usable_from("2026-09-15", cal), "2026-09-28")

    def test_settlement_on_non_session_counts_from_next_session(self):
        cal = weekdays("2026-09-01", 30, skip={"2026-09-15"})
        self.assertEqual(shorts.usable_from("2026-09-15", cal), "2026-09-28")

    def test_calendar_too_short(self):
        self.assertIsNone(shorts.usable_from("2026-09-15", weekdays("2026-09-01", 12)))


class Shares(unittest.TestCase):
    FRAMES = [{1: ("2025-01-10", 1_000_000, "a1")}, {1: ("2025-04-10", 1_200_000, "a2"), 2: ("2023-01-01", 500_000, "a3")},
              {1: ("2025-04-10", 1_200_000, "a2")}]
    FILED = {"a1": "2025-02-05", "a2": "2025-04-20", "a3": "2023-02-01"}
    IDX = shorts.shares_index(FRAMES, FILED)

    def test_index_collapses_duplicates(self):
        self.assertEqual(self.IDX[1], [("2025-02-06", "2025-01-10", 1_000_000), ("2025-04-21", "2025-04-10", 1_200_000)])

    def test_usable_the_day_after_filing(self):
        self.assertIsNone(shorts.shares_at(self.IDX, 1, "2025-02-05"))
        self.assertEqual(shorts.shares_at(self.IDX, 1, "2025-02-06"), 1_000_000)
        self.assertEqual(shorts.shares_at(self.IDX, 1, "2025-04-20"), 1_000_000)
        self.assertEqual(shorts.shares_at(self.IDX, 1, "2025-04-21"), 1_200_000)

    def test_unknown_filing_waits_the_fallback(self):
        idx = shorts.shares_index([{5: ("2025-01-10", 1_000_000, "zz")}], {})
        self.assertIsNone(shorts.shares_at(idx, 5, "2025-05-29"))
        self.assertEqual(shorts.shares_at(idx, 5, "2025-05-30"), 1_000_000)  # 2025-01-10 + 140 days

    def test_live_index_without_filing_dates_uses_the_fact_date(self):
        idx = shorts.shares_index([{5: ("2026-06-30", 2_000_000, None)}])
        self.assertEqual(shorts.shares_at(idx, 5, "2026-06-30"), 2_000_000)

    def test_implausibly_small_share_count_is_unusable(self):
        idx = shorts.shares_index([{7: ("2025-01-10", 100, "x")}, {8: ("2025-01-10", shorts.MIN_SHARES, "y")}])
        self.assertIsNone(shorts.shares_at(idx, 7, "2025-03-01"))  # SEC figure of 100 shares (LAES, 2023)
        self.assertEqual(shorts.shares_at(idx, 8, "2025-03-01"), shorts.MIN_SHARES)

    def test_stale_and_unknown(self):
        self.assertIsNone(shorts.shares_at(self.IDX, 2, "2024-06-01"))
        self.assertIsNone(shorts.shares_at(self.IDX, 3, "2025-06-01"))

    def test_frame_parses_sec(self):
        body = {"data": [{"cik": 1750, "end": "2026-06-30", "val": 39892472, "accn": "0001104659-26-085459"},
                         {"cik": 9, "end": "2026-06-30", "val": 0, "accn": "x"}]}
        with mock.patch.object(shorts.common, "get_json", return_value=body):
            self.assertEqual(shorts.frame(2026, 2), {1750: ("2026-06-30", 39892472, "0001104659-26-085459")})
        with mock.patch.object(shorts.common, "get_json", return_value=None):
            self.assertEqual(shorts.frame(2027, 1), {})

    def test_filing_dates_from_edgar_full_index(self):
        idx = (b"Description: XBRL Index\n\nCIK|Company Name|Form Type|Date Filed|Filename\n" + b"-" * 80 + b"\n"
               b"1000045|OLD MARKET CAPITAL Corp|10-Q|2024-11-13|edgar/data/1000045/0000950170-24-126356.txt\n")
        with mock.patch.object(shorts.common, "fetch", return_value=idx):
            self.assertEqual(shorts.filings(2024, 4), {"0000950170-24-126356": "2024-11-13"})
        with mock.patch.object(shorts.common, "fetch", return_value=None):
            self.assertEqual(shorts.filings(2027, 1), {})


if __name__ == "__main__":
    unittest.main()
