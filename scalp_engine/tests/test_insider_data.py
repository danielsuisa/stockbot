"""I1 SEC ingestion parsers (ADR 0009): data-set zip, acceptance header, daily index, ownership XML, ranges."""
import datetime as dt
import io
import unittest
import zipfile

import polars as pl

from scalp_engine import insider_data as I
from scalp_engine.data_access import HoldoutSealedError, check_range

UTC = dt.timezone.utc
GOT = dt.datetime(2026, 10, 19, tzinfo=UTC)


def tsv(header, rows):
    return "\t".join(header) + "\n" + "".join("\t".join(r) + "\n" for r in rows)


def make_zip():
    sub = ["ACCESSION_NUMBER", "FILING_DATE", "PERIOD_OF_REPORT", "DATE_OF_ORIG_SUB", "DOCUMENT_TYPE", "ISSUERCIK",
           "ISSUERNAME", "ISSUERTRADINGSYMBOL", "AFF10B5ONE"]
    own = ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"]
    nd = ["ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "TRANS_DATE", "TRANS_CODE", "TRANS_ACQUIRED_DISP_CD", "TRANS_SHARES",
          "TRANS_PRICEPERSHARE", "SHRS_OWND_FOLWNG_TRANS", "DIRECT_INDIRECT_OWNERSHIP", "NATURE_OF_OWNERSHIP",
          "TRANS_SHARES_FN", "NATURE_OF_OWNERSHIP_FN"]
    fn = ["ACCESSION_NUMBER", "FOOTNOTE_ID", "FOOTNOTE_TXT"]
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("SUBMISSION.tsv", tsv(sub, [
            ["0001-22-000001", "04-JAN-2022", "03-JAN-2022", "", "4", "0000123", "Acme", "ACME", "0"],
            ["0001-22-000002", "05-JAN-2022", "", "04-JAN-2022", "4/A", "123", "Acme", "ACME", "true"],
            ["0001-22-000003", "05-JAN-2022", "", "", "3", "456", "Beta", "BETA", ""]]))
        z.writestr("REPORTINGOWNER.tsv", tsv(own, [
            ["0001-22-000001", "0000999", "DOE JOHN", "Director,Officer", "CEO"],
            ["0001-22-000001", "0000998", "FUND LP", "TenPercentOwner", ""],
            ["0001-22-000002", "999", "DOE JOHN", "Director,Officer", "CEO"],
            ["0001-22-000003", "777", "ROE", "Director", ""]]))
        z.writestr("NONDERIV_TRANS.tsv", tsv(nd, [
            ["0001-22-000001", "11", "03-JAN-2022", "P", "A", "1,000", "10.5", "5000", "D", "", "F1, F2", ""],
            ["0001-22-000001", "12", "30-DEC-2021", "P", "A", "100", "10", "4000", "I", "By fund", "", "F3"],
            ["0001-22-000002", "13", "03-JAN-2022", "P", "A", "1000", "10.5", "5000", "D", "", "", ""]]))
        z.writestr("FOOTNOTES.tsv", tsv(fn, [["0001-22-000001", "F1", "Not made under a Rule 10b5-1 plan."],
                                             ["0001-22-000001", "F2", "Weighted average price."]]))
    return b.getvalue()


class Dataset(unittest.TestCase):
    def test_parse(self):
        filings, lines, st = I.parse_dataset(make_zip(), "2022q1", GOT, "rid")
        self.assertEqual(st["lines_dropped_pre_2022"], 1)  # the 2021-12-30 line
        self.assertEqual(len(filings), 4)
        f0 = [f for f in filings if f["accession"] == "0001-22-000001"]
        self.assertEqual([f["owner_seq"] for f in f0], [0, 1])
        self.assertEqual(f0[0]["issuer_cik"], "123")
        self.assertEqual(f0[0]["owner_cik"], "999")
        self.assertEqual(f0[0]["notes_10b5"], ["F1\x1fNot made under a Rule 10b5-1 plan."])
        self.assertEqual(f0[0]["event_at"], dt.datetime(2022, 1, 4, 5, tzinfo=UTC))
        amend = next(f for f in filings if f["accession"] == "0001-22-000002")
        self.assertEqual(amend["date_of_orig_sub"], dt.date(2022, 1, 4))
        self.assertEqual(len(lines), 2)  # Form 3 has no lines; the pre-2022 line is dropped
        l0 = next(x for x in lines if x["line_seq"] == 11)
        self.assertEqual((l0["shares"], l0["price"], l0["fn_ids"]), (1000.0, 10.5, ["F1", "F2"]))
        self.assertEqual(I.storage.frame("insider_lines", lines).height, 2)
        self.assertEqual(I.storage.frame("insider_filings", filings).height, 4)

    def test_required_filings_and_edge(self):
        filings, lines, _ = I.parse_dataset(make_zip(), "2022q1", GOT, "rid")
        extra = dict(filings[3], accession="0001-22-000009", filing_date=dt.date(2022, 1, 3), owner_seq=0)
        req, st = I.required_filings(pl.DataFrame(filings + [extra], schema=I.storage.schema("insider_filings")),
                                     I.storage.frame("insider_lines", lines))
        self.assertEqual(set(req["accession"].to_list()), {"0001-22-000001", "0001-22-000002"})
        self.assertEqual(st["edge_excluded"], 0)
        no_p = dict(extra, document_type="4/A", accession="0001-22-000010", issuer_cik="123")
        req, st = I.required_filings(pl.DataFrame(filings + [no_p], schema=I.storage.schema("insider_filings")),
                                     I.storage.frame("insider_lines", lines))
        self.assertEqual(st["edge_excluded"], 1)  # a 4/A filed 2022-01-03 without P lines: maybe accepted 2021-12-31
        self.assertNotIn("0001-22-000010", req["accession"].to_list())


class Parsers(unittest.TestCase):
    def test_acceptance_header(self):
        raw, at = I.parse_acceptance("<pre>&lt;ACCEPTANCE-DATETIME&gt;20240131163015\n&lt;TYPE&gt;4")
        self.assertEqual(raw, "20240131163015")
        self.assertEqual(at, dt.datetime(2024, 1, 31, 21, 30, 15, tzinfo=UTC))  # EST
        _, at = I.parse_acceptance("<ACCEPTANCE-DATETIME>20240701091500")
        self.assertEqual(at, dt.datetime(2024, 7, 1, 13, 15, tzinfo=UTC))  # EDT
        self.assertEqual(I.parse_acceptance("no header"), (None, None))

    def test_daily_index(self):
        text = ("Form Type   Company Name   CIK   Date Filed   File Name\n----\n"
                "4           ACME CORP                     123      20261001    edgar/data/123/0000000123-26-000001.txt\n"
                "4           DOE JOHN                      999      20261001    edgar/data/999/0000000123-26-000001.txt\n"
                "4/A         ACME CORP                     123      20261001    edgar/data/123/0000000123-26-000002.txt\n"
                "8-K         ACME CORP                     123      20261001    edgar/data/123/0000000123-26-000003.txt\n")
        idx = I.parse_daily_index(text)
        self.assertEqual(sorted(idx), ["0000000123-26-000001", "0000000123-26-000002"])
        self.assertEqual(idx["0000000123-26-000002"][0], "4/A")

    def test_ownership_xml(self):
        xml = """<SEC-HEADER>&lt;ACCEPTANCE-DATETIME&gt;20261001170100</SEC-HEADER>
<XML><ownershipDocument><documentType>4</documentType><periodOfReport>2026-09-30</periodOfReport>
<issuer><issuerCik>0000123</issuerCik><issuerName>Acme</issuerName><issuerTradingSymbol>ACME</issuerTradingSymbol></issuer>
<reportingOwner><reportingOwnerId><rptOwnerCik>0000999</rptOwnerCik><rptOwnerName>Doe John</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>true</isOfficer><officerTitle>CEO</officerTitle></reportingOwnerRelationship></reportingOwner>
<aff10b5One>0</aff10b5One>
<nonDerivativeTable><nonDerivativeTransaction><transactionDate><value>2026-09-30</value></transactionDate>
<transactionCoding><transactionCode>P</transactionCode><footnoteId id="F1"/></transactionCoding>
<transactionAmounts><transactionShares><value>1000</value></transactionShares><transactionPricePerShare><value>12.5</value></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
<postTransactionAmounts><sharesOwnedFollowingTransaction><value>9000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
<ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature></nonDerivativeTransaction></nonDerivativeTable>
<footnotes><footnote id="F1">Purchased under a Rule 10b5-1 trading plan.</footnote></footnotes></ownershipDocument></XML>"""
        f, l, (raw, at) = I.parse_ownership(xml, "0000000123-26-000001", dt.date(2026, 10, 1), GOT, "rid")
        self.assertEqual(at, dt.datetime(2026, 10, 1, 21, 1, tzinfo=UTC))
        self.assertEqual((f[0]["relationship"], f[0]["issuer_cik"], f[0]["owner_cik"]), ("Director,Officer", "123", "999"))
        self.assertEqual(f[0]["notes_10b5"], ["F1\x1fPurchased under a Rule 10b5-1 trading plan."])
        self.assertEqual((l[0]["trans_code"], l[0]["shares"], l[0]["price"], l[0]["fn_ids"]), ("P", 1000.0, 12.5, ["F1"]))
        I.storage.frame("insider_filings", f)
        I.storage.frame("insider_lines", l)

    def test_ranges_never_touch_sealed(self):
        lo, hi = I.quarter_range(2022, 1)
        self.assertEqual(lo, dt.datetime(2022, 1, 3, 5, tzinfo=UTC))
        check_range(lo, hi)
        for y, q in I.quarters():
            check_range(*I.quarter_range(y, q))
        check_range(*I.header_range(dt.date(2022, 1, 3)))
        with self.assertRaises(HoldoutSealedError):
            check_range(dt.datetime(2021, 12, 31, 22, tzinfo=UTC), dt.datetime(2022, 1, 4, tzinfo=UTC))
        self.assertEqual(I.quarter_range(2026, 3)[1], dt.datetime(2026, 10, 1, 4, tzinfo=UTC))
        self.assertEqual(list(I.quarters())[-1], (2026, 3))

    def test_fn_ids_and_dates(self):
        self.assertEqual(I.fn_ids(["F3, F1", "", None, "F1"]), ["F1", "F3"])
        self.assertEqual(I.sec_date("31-JAN-2024"), dt.date(2024, 1, 31))
        self.assertEqual(I.sec_date("2024-01-31"), dt.date(2024, 1, 31))
        self.assertIsNone(I.sec_date(""))


if __name__ == "__main__":
    unittest.main()
