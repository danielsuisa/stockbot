"""Screener fundamentals (known values, offline): TTM with Q4 = FY - 9M, annual-only filers, quarter and TTM
year-over-year, CAGR with the mean-YoY fallback, the growth streak; the frames merge keeps the best-ranked tag."""
import unittest

from bot.screener.metrics import fundamental as F
from bot.screener.stores import fundamentals as FS

# Calendar FY2024 (Q1-Q3 from 10-Qs, Q4 only in the 10-K) and Q1-Q3 2025.
REV = {"q": [["2024-01-01", "2024-03-31", 100], ["2024-04-01", "2024-06-30", 110], ["2024-07-01", "2024-09-30", 120],
             ["2025-01-01", "2025-03-31", 130], ["2025-04-01", "2025-06-30", 140], ["2025-07-01", "2025-09-30", 150]],
       "a": [["2024-01-01", "2024-12-31", 460], ["2023-01-01", "2023-12-31", 400], ["2022-01-01", "2022-12-31", 350],
             ["2021-01-01", "2021-12-31", 300], ["2020-01-01", "2020-12-31", 250], ["2019-01-01", "2019-12-31", 200]]}


class TTM(unittest.TestCase):
    def test_q4_is_fy_minus_nine_months(self):
        qs = F.quarters(REV)
        q4 = [q for q in qs if q[1] == "2024-12-31"]
        self.assertEqual(q4, [("2024-10-01", "2024-12-31", 130.0)])  # 460 - (100 + 110 + 120)

    def test_ttm_is_the_last_four_quarters(self):
        # Q4 2024 (derived 130) + 130 + 140 + 150 = 550, ending 2025-09-30
        self.assertEqual(F.ttm(REV), (550.0, "2025-09-30"))
        self.assertEqual(F.ttm_at(REV, "2024-12-31"), (460.0, "2024-12-31"))  # 100 + 110 + 120 + 130
        self.assertEqual(F.ttm_at(REV, "2024-09-30"), (None, None))  # needs Q4 2023: unknown
        self.assertEqual(F.ttm_at(REV, "2023-12-31"), (400.0, "2023-12-31"))  # the fiscal year itself

    def test_annual_only_filer(self):
        f = {"a": [["2024-01-01", "2024-12-31", 90.0], ["2023-01-01", "2023-12-31", 80.0]]}
        self.assertEqual(F.ttm(f), (90.0, "2024-12-31"))
        self.assertAlmostEqual(F.ttm_yoy(f), 90 / 80 - 1)

    def test_annual_newer_than_quarters_wins(self):
        f = {"q": [["2024-01-01", "2024-03-31", 1], ["2024-04-01", "2024-06-30", 1], ["2024-07-01", "2024-09-30", 1],
                   ["2023-10-01", "2023-12-31", 1]],
             "a": [["2024-01-01", "2024-12-31", 10.0]]}
        # the derived Q4 2024 = 10 - 3 = 7 makes 4 consecutive quarters ending 2024-12-31: 1 + 1 + 1 + 7
        self.assertEqual(F.ttm(f), (10.0, "2024-12-31"))

    def test_gaps_and_empty(self):
        self.assertEqual(F.ttm({}), (None, None))
        self.assertEqual(F.ttm(None), (None, None))
        gap = {"q": [["2024-01-01", "2024-03-31", 1], ["2024-07-01", "2024-09-30", 1], ["2024-10-01", "2024-12-31", 1],
                     ["2025-01-01", "2025-03-31", 1]]}
        self.assertEqual(F.ttm(gap), (None, None))  # Q2 2024 missing and no annual to derive it


class Growth(unittest.TestCase):
    def test_quarter_yoy(self):
        self.assertAlmostEqual(F.quarter_yoy(REV), 150 / 120 - 1)

    def test_ttm_yoy(self):
        two = {"q": [["2023-01-01", "2023-03-31", 80], ["2023-04-01", "2023-06-30", 90],
                     ["2023-07-01", "2023-09-30", 95]] + REV["q"], "a": REV["a"]}
        # now: 130 (Q4'24 = 460 - 330) + 130 + 140 + 150 = 550; a year earlier: Q4'23 (400 - 265 = 135) + 100 + 110 + 120
        self.assertAlmostEqual(F.ttm_yoy(two), 550 / 465 - 1)
        self.assertIsNone(F.ttm_yoy(REV))  # the twelve months to 2024-09-30 need Q4 2023

    def test_cagr_and_fallback(self):
        self.assertAlmostEqual(F.cagr(REV, 5), (460 / 200) ** (1 / 5) - 1)
        self.assertAlmostEqual(F.cagr(REV, 3), (460 / 300) ** (1 / 3) - 1)
        neg = {"a": [["2021-01-01", "2021-12-31", -10], ["2022-01-01", "2022-12-31", 5],
                     ["2023-01-01", "2023-12-31", 10], ["2024-01-01", "2024-12-31", 15]]}
        # base <= 0 -> mean of the YoY changes with a positive base: (10/5 - 1 + 15/10 - 1) / 2 = 0.75
        self.assertAlmostEqual(F.cagr(neg, 3), 0.75)
        self.assertIsNone(F.cagr(REV, 9))

    def test_streak(self):
        self.assertEqual(F.streak(REV), 5)
        self.assertEqual(F.streak({"a": [["2023-01-01", "2023-12-31", 2], ["2024-01-01", "2024-12-31", 1]]}), 0)

    def test_ratio(self):
        self.assertIsNone(F.ratio(1, 0))
        self.assertIsNone(F.ratio(1, -2))
        self.assertEqual(F.ratio(1, -2, positive_den=False), -0.5)
        self.assertIsNone(F.growth(1, 0))


class FramesMerge(unittest.TestCase):
    def test_the_first_fallback_tag_wins_per_period(self):
        facts = {}
        FS.merge(facts, "revenue", "D", "CY2025Q2", [{"cik": 7, "start": "2025-04-01", "end": "2025-06-30", "val": 9}], 1)
        FS.merge(facts, "revenue", "D", "CY2025Q2", [{"cik": 7, "start": "2025-04-01", "end": "2025-06-30", "val": 5}], 0)
        FS.merge(facts, "revenue", "D", "CY2025Q2", [{"cik": 7, "start": "2025-04-01", "end": "2025-06-30", "val": 3}], 2)
        self.assertEqual(FS.strip(facts)["7"]["revenue"], {"q": [["2025-04-01", "2025-06-30", 5]]})
        FS.merge(facts, "assets", "I", "CY2025Q2I", [{"cik": 7, "end": "2025-06-30", "val": 100}], 0)
        FS.merge(facts, "revenue", "D", "CY2024", [{"cik": 7, "start": "2024-01-01", "end": "2024-12-31", "val": 40}], 0)
        got = FS.strip(facts)["7"]
        self.assertEqual(got["assets"], {"i": [["2025-06-30", 100]]})
        self.assertEqual(got["revenue"]["a"], [["2024-01-01", "2024-12-31", 40]])

    def test_periods(self):
        import datetime as dt
        p = FS.periods(dt.date(2026, 10, 21), "D")
        self.assertEqual(p[:2], ["CY2026Q3", "CY2026Q2"])
        self.assertIn("CY2025", p)
        self.assertEqual(FS.periods(dt.date(2026, 10, 21), "I", light=True), ["CY2026Q3I", "CY2026Q2I"])



class AltShares(unittest.TestCase):
    """D-054: without a dei cover count, the balance-sheet count, then weighted-average basic shares."""

    def test_fallback_order_and_age(self):
        from bot.screener.stores import fundamentals as FUND
        data = {"shares": {}, "shares_alt": {"7": [["2026-03-31", 52e6, 0], ["2026-06-30", 55e6, 0],
                                                    ["2026-06-30", 70e6, 1]],
                                              "8": [["2026-06-30", 91e6, 1]], "9": [["2025-12-31", 10e6, 0]]}}
        self.assertEqual(FUND.shares_at(data, 7, "2026-10-21"), (55e6, "2026-06-30"))
        self.assertEqual(FUND.shares_at(data, 7, "2026-05-01"), (52e6, "2026-03-31"))  # point in time
        self.assertEqual(FUND.shares_at(data, 8, "2026-10-21"), (91e6, "2026-06-30"))
        self.assertEqual(FUND.shares_at(data, 9, "2026-10-21"), (None, None))  # older than 200 days
        data["shares"]["7"] = [["2026-08-10", "2026-08-01", 54e6]]
        self.assertEqual(FUND.shares_at(data, 7, "2026-10-21")[0], 54e6)  # the dei cover count wins


if __name__ == "__main__":
    unittest.main()
