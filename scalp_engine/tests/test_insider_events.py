"""I1 event clock and journal parity (ADR 0009 section 2, ADR 0008 E5), on synthetic filings."""
import datetime as dt
import unittest

import polars as pl

from scalp_engine import insider_events as E
from scalp_engine import insider_rule as R

UTC = dt.timezone.utc


def filing(acc, who, t, value=40000.0, date=None, cik="1", form="4", original=None):
    d = (date or t.date()).isoformat()
    doc = R.make_doc(form, cik, [(who, f"P{who}", "Director", "")],
                     [{"trans_date": dt.date.fromisoformat(d), "trans_code": "P", "acquired_disposed": "A",
                       "shares": value / 10, "price": 10.0, "shares_after": 1e6, "direct_indirect": "D", "nature": "",
                       "fn_ids": []}], original=original)
    return (t, acc, cik, t.date().isoformat(), doc, "ACME")


class Clock(unittest.TestCase):
    def test_events_and_first_run(self):
        t0 = dt.datetime(2026, 9, 21, 20, tzinfo=UTC)
        docs = [filing("a", "1", t0), filing("b", "2", t0 + dt.timedelta(hours=1)), filing("c", "3", t0 + dt.timedelta(hours=2)),
                filing("d", "4", dt.datetime(2026, 9, 25, 20, tzinfo=UTC))]
        events, alerts, stats = E.run_clock(docs, journal_start=dt.datetime(2026, 9, 24, 20, 30, tzinfo=UTC))
        self.assertEqual([(e["trigger"], e["completing"]) for e in events], [("cluster", "c"), ("cluster", "d")])
        self.assertEqual(events[0]["accessions"], ["a", "b", "c"])
        self.assertTrue(alerts[0]["first_run"])  # the live bot's first run alerts the existing cluster once
        self.assertEqual(alerts[0]["accs"], ["a", "b", "c"])
        self.assertEqual((alerts[1]["first_run"], alerts[1]["accs"]), (False, ["a", "b", "c", "d"]))
        self.assertEqual(stats["added"], 4)

    def test_parity_matching(self):
        t0 = dt.datetime(2026, 9, 25, 14, tzinfo=UTC)
        docs = [filing("a", "1", t0), filing("b", "2", t0), filing("c", "3", t0 + dt.timedelta(minutes=1)),
                filing("x", "9", t0, value=600000.0, cik="2")]
        events, alerts, _ = E.run_clock(docs, journal_start=dt.datetime(2026, 9, 24, 20, 30, tzinfo=UTC))
        journal = [{"id": "2026-09-26-ACME", "date": "2026-09-26", "ticker": "ACME", "cik": 1, "kind": "cluster",
                    "accs": ["a", "b", "c"]},
                   {"id": "2026-09-26-ZZZ", "date": "2026-09-26", "ticker": "ZZZ", "cik": 3, "kind": "big",
                    "accs": ["q"]}]
        sic = pl.DataFrame({"cik": ["1", "2", "3"], "annual_form": ["10-K", "20-F", "10-K"], "tickers": [["ACME"], ["F"], ["Z"]]})
        res = E.parity(docs, alerts, sic, entries=journal, live_state=False)
        self.assertEqual(res["counts"]["matched"], 1)
        self.assertEqual(res["counts"]["explained"], 1)  # accession q is not in the inputs
        self.assertEqual(res["counts"]["unexplained"], 0)
        self.assertEqual(res["counts"]["reimplementation_only"], 1)  # cik 2: a foreign private issuer, never journaled
        self.assertIn("foreign", res["reimplementation_only"][0]["cause"])


if __name__ == "__main__":
    unittest.main()
