"""Phase A of the recommendation algorithm (spec 2026-10-10, section 7 A): readers, universe and firing counters.
No return of any ticket or stock is computed anywhere in the code under test."""
import io
import unittest
import zipfile

from bot import probe_ensemble_data as pd


def tsv(rows):
    head = list(rows[0])
    return "\n".join(["\t".join(head)] + ["\t".join(str(r.get(k, "")) for k in head) for r in rows]) + "\n"


def make_zip(tables):
    """{table name: [row dicts]} -> an in-memory ZipFile shaped like SEC's Form 3/4/5 data set."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, rows in tables.items():
            z.writestr(f"{name}.tsv", tsv(rows))
    buf.seek(0)
    return zipfile.ZipFile(buf)


def sub(acc, form, symbol="ACME", cik="100", filed="05-JAN-2017", box=""):
    return {"ACCESSION_NUMBER": acc, "FILING_DATE": filed, "DOCUMENT_TYPE": form, "ISSUERCIK": cik,
            "ISSUERTRADINGSYMBOL": symbol, "AFF10B5ONE": box}


def tx(acc, code="P", ad="A", shares="1000", price="10", date="03-JAN-2017", fn=""):
    return {"ACCESSION_NUMBER": acc, "TRANS_CODE": code, "TRANS_ACQUIRED_DISP_CD": ad, "TRANS_SHARES": shares,
            "TRANS_PRICEPERSHARE": price, "TRANS_DATE": date, "TRANS_SHARES_FN": fn}


def owner(acc, cik, rel="Director"):
    return {"ACCESSION_NUMBER": acc, "RPTOWNERCIK": cik, "RPTOWNER_RELATIONSHIP": rel}


def note(acc, fid, text):
    return {"ACCESSION_NUMBER": acc, "FOOTNOTE_ID": fid, "FOOTNOTE_TXT": text}


class InsiderReaders(unittest.TestCase):
    def test_normalise_symbols(self):
        self.assertEqual(pd.normalise_symbol("brk-b"), "BRK.B")
        self.assertEqual(pd.normalise_symbol(" AAPL, AAPLW"), "AAPL")
        self.assertEqual(pd.normalise_symbol("goog;googl"), "GOOG")
        for bad in ("NONE", "N/A", "", None, "TOOLONG", "12AB"):
            self.assertIsNone(pd.normalise_symbol(bad), bad)

    def test_symbol_filings_all_forms(self):
        z = make_zip({"SUBMISSION": [sub("a", "3"), sub("b", "4"), sub("c", "5"), sub("d", "4/A"), sub("e", "10-K"),
                                     sub("f", "4", symbol="NONE")]})
        rows = pd.symbol_filings(z)
        self.assertEqual(sorted(rows), [("2017-01-05", "100", "ACME")] * 4)  # 10-K and the unusable symbol dropped
        by_date, ciks = pd.index(rows + [("2017-01-02", "200", "ACME")])
        self.assertEqual(by_date["ACME"], ["2017-01-02"] + ["2017-01-05"] * 4)
        self.assertEqual(ciks["ACME"], {"100", "200"})

    def test_purchases_plan_and_role(self):
        z = make_zip({
            "SUBMISSION": [sub("a", "4"), sub("b", "4"), sub("c", "4"), sub("d", "4", box="1"), sub("e", "4/A")],
            "NONDERIV_TRANS": [tx("a"), tx("b", fn="F1"), tx("c"), tx("d"), tx("e"), tx("a", code="S", ad="D")],
            "REPORTINGOWNER": [owner("a", "1"), owner("b", "2"), owner("c", "3", rel="TenPercentOwner"),
                               owner("d", "4"), owner("e", "5")],
            "FOOTNOTES": [note("b", "F1", "Purchased under a Rule 10b5-1 trading plan.")]})
        got = pd.purchases(z)
        self.assertEqual([(p["owner"], p["trade_date"], p["shares"], p["price"], p["value"]) for p in got],
                         [("1", "2017-01-03", 1000.0, 10.0, 10000.0)])  # plan, 10% owner, box, 4/A, sale excluded
        self.assertEqual((got[0]["filed"], got[0]["issuer"], got[0]["symbol"]), ("2017-01-05", "100", "ACME"))

    def test_a_denied_plan_footnote_still_counts(self):
        z = make_zip({"SUBMISSION": [sub("a", "4")], "NONDERIV_TRANS": [tx("a", fn="F1")],
                      "REPORTINGOWNER": [owner("a", "1", rel="Officer")],
                      "FOOTNOTES": [note("a", "F1", "Not made pursuant to a Rule 10b5-1 plan.")]})
        self.assertEqual(len(pd.purchases(z)), 1)


def cols(rows):
    """Row dicts -> EDGAR's columnar shape."""
    return {k: [r[k] for r in rows] for k in rows[0]}


def filing(acc, form, items, accepted):
    return {"accessionNumber": acc, "form": form, "items": items, "acceptanceDateTime": accepted,
            "filingDate": accepted[:10]}


class Submissions(unittest.TestCase):
    SESSIONS = ["2024-02-01", "2024-02-02", "2024-02-05", "2024-07-01", "2024-07-02"]

    def zip_path(self, d):
        import json
        import os
        p = os.path.join(d, "submissions.zip")
        main = {"cik": "320193", "filings": {"recent": cols([
            filing("a", "8-K", "2.02,9.01", "2024-02-01T21:30:00.000Z"), filing("b", "8-K", "5.02", "2024-02-01T12:00:00.000Z"),
            filing("c", "10-Q", "", "2024-02-02T12:00:00.000Z")]), "files": [{"name": "CIK0000320193-submissions-001.json"}]}}
        page = cols([filing("d", "8-K/A", "2.02", "2024-07-01T14:00:00.000Z")])
        other = {"cik": "999", "filings": {"recent": cols([filing("e", "8-K", "2.02", "2024-02-01T12:00:00.000Z")])}}
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("CIK0000320193.json", json.dumps(main))
            z.writestr("CIK0000320193-submissions-001.json", json.dumps(page))
            z.writestr("CIK0000000999.json", json.dumps(other))
        return p

    def test_items_and_pages(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            got = pd.earnings_releases(self.zip_path(d), {"320193"}, "America/New_York")
        self.assertEqual(got, {"320193": ["2024-02-01T21:30", "2024-07-01T14:00"]})  # 5.02 and 10-Q out, CIK 999 not asked

    def test_acceptance_tz(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            got = pd.earnings_releases(self.zip_path(d), {"0000320193"}, "UTC")
        self.assertEqual(got["320193"], ["2024-02-01T16:30", "2024-07-01T10:00"])  # winter UTC-5, summer UTC-4

    def test_reaction_session_after_close_et(self):
        s = self.SESSIONS
        self.assertEqual(pd.reaction_session("2024-02-01T15:59", s), "2024-02-01")
        self.assertEqual(pd.reaction_session("2024-02-01T16:00", s), "2024-02-02")
        self.assertEqual(pd.reaction_session("2024-02-03T10:00", s), "2024-02-05")  # Saturday -> Monday
        self.assertEqual(pd.reaction_session("2024-02-02T18:00", s), "2024-02-05")
        self.assertIsNone(pd.reaction_session("2024-07-02T17:00", s))  # past the last session


def weekdays(start, n):
    import datetime as dt
    d, out = dt.date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(1)
    return out


class Bars(unittest.TestCase):
    S = weekdays("2016-01-04", 400)

    def test_bad_price_breaks(self):
        d = self.S[:6]
        self.assertEqual(pd.segments(d, [10, 39.9, 159.6, 31.9, 31.9, 127.6], self.S), [0, 0, 1, 1, 1, 2])
        # x3.99 same, x4.0 new, /5 kept (a fall is real), x4.0 new
        gap10 = [self.S[0], self.S[11]]  # 10 sessions without a bar between them
        gap11 = [self.S[0], self.S[12]]
        self.assertEqual(pd.segments(gap10, [10, 10], self.S), [0, 0])
        self.assertEqual(pd.segments(gap11, [10, 10], self.S), [0, 1])

    def test_history_restarts_after_break(self):
        closes = [10.0] * 280 + [40.0] * 20
        seg = pd.segments(self.S[:300], closes, self.S)
        self.assertEqual(pd.earlier_in_segment(seg, 279), 279)
        self.assertEqual(pd.earlier_in_segment(seg, 290), 10)  # counted from the break at bar 280

    def test_join_raw_and_split(self):
        from unittest import mock
        d = self.S[:3]
        raw = {"AAA": [(d[0], 1, 2, 0.5, 10, 100), (d[1], 1, 2, 0.5, 10, 100), (d[2], 1, 2, 0.5, 10, 100)]}
        adj = {"AAA": {d[0]: (1, 1.0, 1000), d[2]: (1, 1.0, 1000)}}  # d[1] missing from the split-adjusted feed
        with mock.patch.object(pd.alpaca, "daily", return_value=raw), mock.patch.object(pd, "split_bars", return_value=adj):
            s = pd.load_series(["AAA"], d[0], d[2], self.S)["AAA"]
        self.assertEqual(s["dates"], [d[0], d[2]])
        self.assertEqual((s["raw_close"], s["close"], s["volume"], s["raw_volume"]), ([10, 10], [1.0, 1.0], [1000, 1000], [100, 100]))
        self.assertEqual((s["raw_high"], s["raw_low"], s["segment"]), ([2, 2], [0.5, 0.5], [0, 0]))


    def test_split_cache_is_keyed_by_symbols(self):
        import pathlib
        import tempfile
        from unittest import mock
        bar = lambda: {"t": "2016-01-04T05:00:00Z", "o": 1, "c": 2, "v": 3}  # noqa: E731
        with tempfile.TemporaryDirectory() as d, mock.patch.object(pd.alpaca, "CACHE", pathlib.Path(d)):
            with mock.patch.object(pd.alpaca, "_get", side_effect=lambda u, q: {"bars": {s: [bar()] for s in q["symbols"].split(",")}}) as g:
                self.assertEqual(set(pd.split_bars(["AAA"], "2016-01-01", "2016-01-05")), {"AAA"})
                self.assertEqual(set(pd.split_bars(["BBB"], "2016-01-01", "2016-01-05")), {"BBB"})  # not AAA's file
                pd.split_bars(["AAA"], "2016-01-01", "2016-01-05")
                self.assertEqual(g.call_count, 2)  # the third call is a cache hit
                self.assertEqual(g.call_args.args[1]["adjustment"], "split")


if __name__ == "__main__":
    unittest.main()
