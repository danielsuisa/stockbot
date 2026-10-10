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


if __name__ == "__main__":
    unittest.main()
