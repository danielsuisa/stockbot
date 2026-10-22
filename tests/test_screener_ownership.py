"""Screener ownership store (offline, known values): SEC's quarterly Form 3/4/5 data set read from a synthetic ZIP,
line-level holdings (a partial Form 4 keeps the other lines), amendments supersede the original in any read order,
a joint filing counts once, accession dedupe between the bulk quarters and the daily index, point-in-time holdings."""
import io
import unittest
import zipfile
from unittest import mock

from bot.screener.stores import ownership as OWN

SUB = ["ACCESSION_NUMBER", "FILING_DATE", "PERIOD_OF_REPORT", "DATE_OF_ORIG_SUB", "NO_SECURITIES_OWNED",
       "DOCUMENT_TYPE", "ISSUERCIK", "ISSUERNAME", "ISSUERTRADINGSYMBOL"]
OWNER = ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP"]
TRANS = ["ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "SECURITY_TITLE", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES",
         "SHRS_OWND_FOLWNG_TRANS", "DIRECT_INDIRECT_OWNERSHIP", "NATURE_OF_OWNERSHIP"]
HOLD = ["ACCESSION_NUMBER", "NONDERIV_HOLDING_SK", "SECURITY_TITLE", "SHRS_OWND_FOLWNG_TRANS",
        "DIRECT_INDIRECT_OWNERSHIP", "NATURE_OF_OWNERSHIP"]


def tsv(cols, rows):
    return "\t".join(cols) + "\n" + "".join("\t".join(str(r.get(c, "")) for c in cols) + "\n" for r in rows)


def quarter(subs, owners, trans, holds=()):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SUBMISSION.tsv", tsv(SUB, subs))
        z.writestr("REPORTINGOWNER.tsv", tsv(OWNER, owners))
        z.writestr("NONDERIV_TRANS.tsv", tsv(TRANS, trans))
        z.writestr("NONDERIV_HOLDING.tsv", tsv(HOLD, holds))
    buf.seek(0)
    return buf


def sub(acc, filed, period, form="4", cik="0000000100", none=""):
    return {"ACCESSION_NUMBER": acc, "FILING_DATE": filed, "PERIOD_OF_REPORT": period, "DOCUMENT_TYPE": form,
            "ISSUERCIK": cik, "ISSUERTRADINGSYMBOL": "abc", "NO_SECURITIES_OWNED": none}


def tr(acc, sk, shares, di="D", nature="", title="Common Stock"):
    return {"ACCESSION_NUMBER": acc, "NONDERIV_TRANS_SK": sk, "SECURITY_TITLE": title, "SHRS_OWND_FOLWNG_TRANS": shares,
            "DIRECT_INDIRECT_OWNERSHIP": di, "NATURE_OF_OWNERSHIP": nature}


class Bulk(unittest.TestCase):
    def test_quarter_lines_roles_and_document_order(self):
        z = quarter([sub("A-1", "02-SEP-2026", "29-AUG-2026"), sub("X-9", "02-SEP-2026", "29-AUG-2026", cik="999")],
                    [{"ACCESSION_NUMBER": "A-1", "RPTOWNERCIK": "0000000007", "RPTOWNER_RELATIONSHIP": "Director,Officer"},
                     {"ACCESSION_NUMBER": "X-9", "RPTOWNERCIK": "8", "RPTOWNER_RELATIONSHIP": "Officer"}],
                    [tr("A-1", 11, 460_000.0), tr("A-1", 10, 465_000.0),  # SK order = document order
                     tr("A-1", 12, 2_000_000.0, "I", "By the Smith Family Trust"),
                     tr("A-1", 13, 50.0, title="Series A Preferred Stock")])
        docs = OWN.read_quarter(z, {"100"})
        self.assertEqual(len(docs), 1)  # the issuer outside the universe is skipped
        d = docs[0]
        self.assertEqual((d["issuer"], d["period"], d["filed"], d["owners"]), ("100", "2026-08-29", "2026-09-02",
                                                                               [("7", "do")]))
        self.assertEqual(d["lines"], {"common|D|": 460_000.0, "common|I|smith family trust": 2_000_000.0})
        self.assertEqual(d["shares"], 2_460_000.0)

    def test_form3_without_securities_records_zero(self):
        z = quarter([sub("A-2", "02-SEP-2026", "01-SEP-2026", form="3", none="1")],
                    [{"ACCESSION_NUMBER": "A-2", "RPTOWNERCIK": "7", "RPTOWNER_RELATIONSHIP": "Officer"}], [])
        self.assertEqual(OWN.read_quarter(z, {"100"})[0]["lines"], {"common|D|": 0.0})


def doc(period, lines, owners=(("7", "o"),), acc=None):
    return {"issuer": "100", "symbol": "ABC", "period": period, "owners": list(owners), "lines": lines,
            "shares": sum(lines.values()), "buys": 0, "sales": 0, "acc": acc}


class Store(unittest.TestCase):
    def test_partial_form4_keeps_the_other_lines(self):
        s = {}
        OWN.apply(s, doc("2026-01-10", {"common|D|": 1e6, "common|I|trust": 5e6}), "2026-01-12")
        OWN.apply(s, doc("2026-03-02", {"common|D|": 0.8e6}), "2026-03-03")  # a direct sale only
        self.assertEqual(OWN.holdings(s, "100", None, "o"), (5.8e6, 1))
        self.assertEqual(OWN.holdings(s, "100", "2026-02-01", "o"), (6e6, 1))  # point in time
        self.assertEqual(OWN.holdings(s, "100", "2025-12-31", "o"), (None, 0))

    def test_amendment_supersedes_the_original_in_any_order(self):
        orig = doc("2026-05-01", {"common|D|": 1_000_000.0})
        amend = doc("2026-05-01", {"common|D|": 100_000.0})  # 4/A: a misplaced zero
        for order in ((orig, "2026-05-03", amend, "2026-06-10"), (amend, "2026-06-10", orig, "2026-05-03")):
            s = {}
            OWN.apply(s, order[0], order[1])
            OWN.apply(s, order[2], order[3])
            self.assertEqual(OWN.holdings(s, "100", None, "o"), (100_000.0, 1))

    def test_joint_filing_counts_once(self):
        s = {}
        group = (("21", "t"), ("22", "t"), ("23", "t"))  # a fund, its adviser and its manager report one holding
        OWN.apply(s, doc("2026-04-01", {"common|I|": 9e6}, owners=group), "2026-04-02")
        OWN.apply(s, doc("2026-04-20", {"common|I|": 8e6}, owners=group[::-1]), "2026-04-21")
        self.assertEqual(OWN.holdings(s, "100", None, "t"), (8e6, 1))
        self.assertEqual(list(s["issuers"]["100"]), ["21+22+23"])

    def test_a_holder_filing_jointly_and_alone_counts_once(self):
        s = {}  # ZYME's EcoR1 (OBSERVED): two CIKs file jointly on some forms and each alone on others
        OWN.apply(s, doc("2025-01-16", {"common|I|see note": 15.7e6}, owners=(("1587114", "dt"),)), "2025-01-21")
        OWN.apply(s, doc("2025-02-17", {"common|I|see note": 15.7e6}, owners=(("1454385", "dt"),)), "2025-02-18")
        OWN.apply(s, doc("2025-06-26", {"common|I|see note": 23.0e6},
                         owners=(("1454385", "dt"), ("1587114", "dt"))), "2025-06-27")
        OWN.apply(s, doc("2026-01-10", {"common|D|": 1e6}, owners=(("7", "o"),)), "2026-01-12")
        self.assertEqual(OWN.holdings(s, "100", None, "dot"), (24.0e6, 2))
        self.assertEqual(OWN.holdings(s, "100", "2025-03-01", "dot"), (15.7e6, 1))  # the latest group filing then
        self.assertEqual(OWN.owner_holdings(s, "100")["1587114"], 23.0e6)

    def test_daily_index_skips_filings_the_bulk_quarters_read(self):
        z = quarter([sub("0000001-26-000001", "30-SEP-2026", "28-SEP-2026")],
                    [{"ACCESSION_NUMBER": "0000001-26-000001", "RPTOWNERCIK": "7", "RPTOWNER_RELATIONSHIP": "Officer"}],
                    [tr("0000001-26-000001", 1, 300.0)])
        rows = [("4", "100", "edgar/data/100/0000001-26-000001.txt"), ("4", "100", "edgar/data/100/0000001-26-000002.txt")]
        fetched = []
        xml = ("<ownershipDocument><documentType>4</documentType><periodOfReport>2026-10-01</periodOfReport>"
               "<issuer><issuerCik>0000000100</issuerCik><issuerTradingSymbol>ABC</issuerTradingSymbol></issuer>"
               "<reportingOwner><reportingOwnerId><rptOwnerCik>0000000007</rptOwnerCik></reportingOwnerId>"
               "<reportingOwnerRelationship><isOfficer>1</isOfficer></reportingOwnerRelationship></reportingOwner>"
               "<nonDerivativeTable><nonDerivativeTransaction><securityTitle><value>Common Stock</value></securityTitle>"
               "<postTransactionAmounts><sharesOwnedFollowingTransaction><value>250</value>"
               "</sharesOwnedFollowingTransaction></postTransactionAmounts><ownershipNature>"
               "<directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>"
               "</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>")

        def fetch(url):
            if url.endswith(".zip"):
                return z.getvalue()
            fetched.append(url)
            return xml.encode()
        with mock.patch.object(OWN.stores, "load", return_value=None), \
                mock.patch.object(OWN.stores, "save", return_value={}), \
                mock.patch.object(OWN.common, "fetch", side_effect=fetch), \
                mock.patch.object(OWN, "index_rows", return_value=rows):
            store, _, stats = OWN.update({"100"}, ["2026-10-01", "2026-10-02"], "2026-10-02",
                                         links={"2026q3": "https://www.sec.gov/x/2026q3_form345.zip"})
        self.assertEqual(stats["bulk"]["quarters"], ["2026q3"])
        self.assertEqual(fetched, ["https://www.sec.gov/Archives/edgar/data/100/0000001-26-000002.txt"])
        self.assertEqual(OWN.holdings(store, "100", None, "o"), (250.0, 1))
        self.assertEqual(OWN.holdings(store, "100", "2026-09-30", "o"), (300.0, 1))
        self.assertEqual(stats["days"], 2)

    def test_bulk_is_idempotent(self):
        s = {"quarters": ["2026q3"]}
        with mock.patch.object(OWN.common, "fetch") as f:
            stats = OWN.bulk_update(s, {"100"}, "2026-10-22", links={"2026q3": "u"})
        f.assert_not_called()
        self.assertEqual(stats["quarters"], [])

    def test_quarter_end(self):
        self.assertEqual(OWN._quarter_end("2026q3"), "2026-09-30")
        self.assertEqual(OWN._quarter_end("2025q4"), "2025-12-31")
        self.assertEqual(OWN.line_key("Class B Common Stock", "I", "By the Smith Family Trust"),
                         "class b|I|smith family trust")
        self.assertIsNone(OWN.line_key("Warrants (right to buy)", "D"))


if __name__ == "__main__":
    unittest.main()
