"""Squeeze screener data layer: FINRA short interest, SEC shares, point-in-time rules."""
import datetime as dt
import json
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
    IDX = shorts.shares_index([{1: ("2025-01-10", 100)}, {1: ("2025-04-10", 120), 2: ("2023-01-01", 50)},
                               {1: ("2025-04-10", 120)}])

    def test_index_collapses_duplicates(self):
        self.assertEqual(self.IDX[1], [("2025-01-10", 100), ("2025-04-10", 120)])

    def test_lag_and_latest(self):
        self.assertIsNone(shorts.shares_at(self.IDX, 1, "2025-01-24"))
        self.assertEqual(shorts.shares_at(self.IDX, 1, "2025-01-25"), 100)
        self.assertEqual(shorts.shares_at(self.IDX, 1, "2025-04-24"), 100)
        self.assertEqual(shorts.shares_at(self.IDX, 1, "2025-04-25"), 120)

    def test_stale_and_unknown(self):
        self.assertIsNone(shorts.shares_at(self.IDX, 2, "2024-06-01"))
        self.assertIsNone(shorts.shares_at(self.IDX, 3, "2025-06-01"))

    def test_frame_parses_sec(self):
        body = {"data": [{"cik": 1750, "end": "2026-06-30", "val": 39892472}, {"cik": 9, "end": "2026-06-30", "val": 0}]}
        with mock.patch.object(shorts.common, "get_json", return_value=body):
            self.assertEqual(shorts.frame(2026, 2), {1750: ("2026-06-30", 39892472)})
        with mock.patch.object(shorts.common, "get_json", return_value=None):
            self.assertEqual(shorts.frame(2027, 1), {})


if __name__ == "__main__":
    unittest.main()
