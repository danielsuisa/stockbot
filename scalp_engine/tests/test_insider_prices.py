"""I1 price side (ADR 0009 sections 4-5): symbol normalisation and mapping, split adjustment, bad-price rule, the
universe pre-filter, pre-entry state and the entry date. Synthetic data only."""
import datetime as dt
import json
import unittest

import numpy as np
import polars as pl

from scalp_engine import insider_prices as P

UTC = dt.timezone.utc
D0 = dt.date(2024, 1, 2)


def days(n, start=D0):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


class Symbols(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(P.norm("brk-b"), "BRK.B")
        self.assertEqual(P.norm("BRK/B"), "BRK.B")
        self.assertEqual(P.norm("NASDAQ: ACME"), "ACME")
        self.assertEqual(P.norm(" acme "), "ACME")
        self.assertIsNone(P.norm(""))
        self.assertIsNone(P.norm(None))

    def filings(self, rows):
        return pl.DataFrame([{"issuer_cik": c, "issuer_symbol": s, "filing_date": d, "owner_seq": 0} for c, s, d in rows])

    def test_resolve(self):
        sessions = days(40)
        bar_idx = {"ACME": np.arange(0, 40, dtype=np.int32), "NEWCO": np.arange(20, 40, dtype=np.int32),
                   "OLD": np.arange(0, 10, dtype=np.int32)}
        f = self.filings([("1", "acme", sessions[5]), ("2", "OLD", sessions[2]), ("3", "XYZ", sessions[3]),
                          ("3", "NEWCO", sessions[25]), ("4", "DUP", sessions[1]), ("5", "DUP", sessions[3])])
        sm = P.SymbolMap(f, bar_idx, [("OLD", "NEWCO", sessions[20])])
        self.assertEqual(sm.resolve("1", "acme", sessions[30], sessions[31], 31), ("ACME", "filed", None))
        self.assertEqual(sm.resolve("2", "OLD", sessions[30], sessions[31], 31)[:2], ("NEWCO", "rename"))
        self.assertEqual(sm.resolve("3", "XYZ", sessions[30], sessions[31], 31)[:2], ("NEWCO", "recent_filed"))
        self.assertEqual(sm.resolve("4", "DUP", sessions[5], sessions[6], 6)[:2], (None, "ambiguous"))
        self.assertEqual(sm.resolve("9", "NOPE", sessions[30], sessions[31], 31)[:2], (None, "unmapped"))
        self.assertFalse(sm.has_bar_before("OLD", 31))  # last bar 21 sessions before entry
        self.assertTrue(sm.has_bar_before("NEWCO", 31))


class BarsPlan(unittest.TestCase):
    def test_plan_never_overlaps_p1_dates(self):
        """I1 fetched 2026-10-01..16 again because P1_LAST was 2026-09-30 (I1 addendum)."""
        self.assertEqual(P.P1_LAST, dt.date(2026, 10, 16))
        plan = P.bars_plan(["A", "B", "C"], ["A", "B"])
        for part, symbols, start, end in plan:
            overlaps = not (end < P.P1_FIRST or start > P.P1_LAST)
            if overlaps:  # inside P1's dates only the symbols P1 lacks may be fetched
                self.assertEqual(symbols, ["C"], part)
        self.assertEqual([p[0] for p in plan], ["sip-1d-i1a", "sip-1d-i1b"])  # nothing left after P1's last session


class Adjustments(unittest.TestCase):
    def test_factors_and_adjust(self):
        acts = pl.DataFrame({"action_type": ["reverse_splits", "forward_splits", "stock_dividends", "cash_dividends"],
                             "event_at": [dt.datetime(2024, 1, 10, 5, tzinfo=UTC)] * 4,
                             "raw": [json.dumps({"symbol": "R", "old_rate": 20, "new_rate": 1}),
                                     json.dumps({"symbol": "F", "old_rate": 1, "new_rate": 4}),
                                     json.dumps({"symbol": "S", "rate": 1.25}), json.dumps({"symbol": "C", "rate": 0.5})]})
        f = P.price_factors(acts)
        self.assertEqual(f["R"], [(dt.date(2024, 1, 10), 20.0)])
        self.assertEqual(f["F"], [(dt.date(2024, 1, 10), 0.25)])
        self.assertAlmostEqual(f["S"][0][1], 0.8)
        self.assertNotIn("C", f)  # cash dividends do not change the share basis
        self.assertAlmostEqual(P.adjust_to(0.5, dt.date(2024, 1, 9), dt.date(2024, 1, 10), f["R"]), 10.0)
        self.assertAlmostEqual(P.adjust_to(0.5, dt.date(2024, 1, 10), dt.date(2024, 1, 11), f["R"]), 0.5)  # already on basis

    def test_bad_bars(self):
        d = days(10)
        clean = [(x, 10.0, 10.5, 9.5, 10.0, 1000) for x in d]
        self.assertEqual(P.bad_bars(clean), {})
        rows = list(clean)
        rows[2] = (d[2], 10.0, 9.0, 9.5, 10.0, 1000)  # high < low
        self.assertEqual(P.bad_bars(rows), {d[2]: "malformed"})
        rows = list(clean)
        rows[4] = (d[4], 10.0, 30.0, 9.5, 29.0, 1000)  # one-day spike that reverts
        self.assertEqual(P.bad_bars(rows), {d[4]: "spike"})
        rows = [(x, 10.0, 10.5, 9.5, 10.0, 1000) if i < 5 else (x, 5.0, 5.2, 4.8, 5.0, 1000) for i, x in enumerate(d)]
        out = P.bad_bars(rows)  # a 2:1 move with no recorded split
        self.assertEqual(set(out.values()), {"missed_split"})
        self.assertEqual(min(out), d[5])
        self.assertEqual(P.bad_bars(rows, [(d[5], 0.5)]), {})  # the split is recorded: nothing flagged


class PreFilter(unittest.TestCase):
    def frame(self, rows):
        return pl.DataFrame(rows, schema={"symbol": pl.Utf8, "d": pl.Date, "close": pl.Float64, "volume": pl.Int64},
                            orient="row")

    def test_prefilter_dv_and_price(self):
        s = days(30)
        rows = [("LIQ", x, 10.0, 20000) for x in s]                          # $200K a day
        rows += [("THIN", x, 10.0, 20000) for i, x in enumerate(s) if i % 3 == 1]  # 7 of 20 sessions -> median 0
        rows += [("PENNY", x, 0.5, 10_000_000) for x in s]                   # $5M a day but under $1
        rows += [("RS", x, 0.5, 10_000_000) for x in s]                      # under $1, 1:20 reverse split next session
        plan = P.prefilter_plan(self.frame(rows), s, {"RS": [(s[25], 20.0)]})
        self.assertNotIn(s[18], plan)  # fewer than 20 sessions of history
        self.assertEqual(plan[s[19]], ["LIQ"])
        self.assertIn("RS", plan[s[24]])  # adjusted for the split effective the next session
        self.assertNotIn("RS", plan[s[25]])
        self.assertNotIn("PENNY", plan[s[29]])
        self.assertNotIn("THIN", plan[s[29]])

    def test_pre_entry(self):
        s = days(25)
        b = self.frame([("A", x, 1.0 + i, 1000) for i, x in enumerate(s) if i != 22])
        pre = P.PreEntry(b, s)
        self.assertEqual(pre.last_close("A", s[22]), (22.0, s[21]))  # no bar on s[22]
        self.assertIsNone(pre.dv20("A", s[18]))
        self.assertEqual(pre.dv20("A", s[24]), sorted([(1.0 + i) * 1000 for i in range(5, 25) if i != 22] + [0.0])[9:11][0] / 2
                         + sorted([(1.0 + i) * 1000 for i in range(5, 25) if i != 22] + [0.0])[10] / 2)

    def test_entry_date(self):
        cal = [{"trading_date": d, "open_at": dt.datetime.combine(d, dt.time(14, 30), UTC)} for d in days(5)]
        self.assertEqual(P.entry_date(dt.datetime(2024, 1, 2, 14, 30, tzinfo=UTC), cal), days(5)[1])  # not strictly after
        self.assertEqual(P.entry_date(dt.datetime(2024, 1, 2, 14, 29, tzinfo=UTC), cal), days(5)[0])
        self.assertIsNone(P.entry_date(dt.datetime(2024, 2, 1, tzinfo=UTC), cal))


if __name__ == "__main__":
    unittest.main()
