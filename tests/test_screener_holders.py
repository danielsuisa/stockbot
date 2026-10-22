"""Screener 5%+ holders and float (offline, known values): Schedule 13G / 13D XML cover pages, index grouping by
accession, latest filing per (issuer, filer), a 13G holder that also files Form 4 as a 10% owner counted once,
qualified institutions (Rule 13d-1(b)) kept in the float, float never <= 0, the float methods."""
import datetime as dt
import unittest
from zoneinfo import ZoneInfo

from bot.screener import evaluate, parser
from bot.screener.stores import holders as HOLD
from test_screener_evaluate import env, series, shares, uni

NY = ZoneInfo("America/New_York")

G13 = """<SEC-DOCUMENT>x
<XML>
<?xml version="1.0" encoding="UTF-8"?><edgarSubmission xmlns="http://www.sec.gov/edgar/schedule13g"><headerData>
<submissionType>{form}</submissionType><filerInfo><filer><filerCredentials><cik>{filer}</cik></filerCredentials>
</filer></filerInfo></headerData><formData><coverPageHeader><securitiesClassTitle>COMMON STOCK</securitiesClassTitle>
<eventDateRequiresFilingThisStatement>08/31/2026</eventDateRequiresFilingThisStatement><issuerInfo>
<issuerCik>0000000101</issuerCik></issuerInfo><designateRulesPursuantThisScheduleFiled>
<designateRulePursuantThisScheduleFiled>Rule {rule}</designateRulePursuantThisScheduleFiled>
</designateRulesPursuantThisScheduleFiled></coverPageHeader>
<coverPageHeaderReportingPersonDetails><reportingPersonName>A</reportingPersonName>
<reportingPersonBeneficiallyOwnedAggregateNumberOfShares>{shares}</reportingPersonBeneficiallyOwnedAggregateNumberOfShares>
<classPercent>{pct}</classPercent><typeOfReportingPerson>{typ}</typeOfReportingPerson>
</coverPageHeaderReportingPersonDetails></formData></edgarSubmission>
</XML></SEC-DOCUMENT>"""

D13 = """<?xml version="1.0"?><edgarSubmission xmlns="http://www.sec.gov/edgar/schedule13D"><headerData>
<submissionType>SCHEDULE 13D/A</submissionType><filerInfo><filer><filerCredentials><cik>0001843196</cik>
</filerCredentials></filer></filerInfo></headerData><formData><coverPageHeader><dateOfEvent>09/03/2026</dateOfEvent>
<issuerInfo><issuerCIK>0001821468</issuerCIK></issuerInfo></coverPageHeader><reportingPersons>
<reportingPersonInfo><reportingPersonCIK>0001843196</reportingPersonCIK><aggregateAmountOwned>223905836.00</aggregateAmountOwned>
<percentOfClass>41.30</percentOfClass></reportingPersonInfo>
<reportingPersonInfo><reportingPersonNoCIK>Y</reportingPersonNoCIK><aggregateAmountOwned>58453168.00</aggregateAmountOwned>
<percentOfClass>10.80</percentOfClass></reportingPersonInfo></reportingPersons></formData></edgarSubmission>"""


def g13(filer="0000000900", shares=6_000_000, pct=12.0, rule="13d-1(c)", typ="IN", form="SCHEDULE 13G"):
    return G13.format(filer=filer, shares=shares, pct=pct, rule=rule, typ=typ, form=form)


class Parse(unittest.TestCase):
    def test_13g_cover_page(self):
        d = HOLD.parse(g13(rule="13d-1(b)", typ="IA"))
        self.assertEqual((d["issuer"], d["event"], d["shares"], d["pct"], d["ciks"]),
                         ("101", "2026-08-31", 6e6, 12.0, ["900"]))
        self.assertTrue(d["passive"])
        self.assertFalse(HOLD.parse(g13())["passive"])

    def test_13d_group_counts_its_largest_person(self):
        d = HOLD.parse(D13)  # the group's 41.3% includes the 10.8% entity: the largest amount, not the sum
        self.assertEqual((d["issuer"], d["event"], d["shares"], d["pct"], d["ciks"], d["passive"]),
                         ("1821468", "2026-09-03", 223905836.0, 41.3, ["1843196"], False))

    def test_text_filing_is_not_read(self):
        self.assertIsNone(HOLD.parse("<SEC-DOCUMENT><TEXT>SCHEDULE 13G ... Item 9: 5.1%</TEXT>"))


IDX = b"""Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------
SCHEDULE 13G     Issuer Inc.                                                   101         2026-08-20  edgar/data/101/0000900-26-000001.txt
SCHEDULE 13G     Holder LP                                                     900         2026-08-20  edgar/data/900/0000900-26-000001.txt
SCHEDULE 13G/A   Issuer Inc.                                                   101         2026-09-10  edgar/data/101/0000900-26-000002.txt
SCHEDULE 13G/A   Holder LP                                                     900         2026-09-10  edgar/data/900/0000900-26-000002.txt
SC 13G           Other Inc.                                                    101         2024-02-10  edgar/data/101/0000901-24-000001.txt
SCHEDULE 13D     Elsewhere Corp.                                               555         2026-09-11  edgar/data/555/0000902-26-000001.txt
"""


class Discover(unittest.TestCase):
    def test_latest_filing_per_issuer_and_filer(self):
        st = {}
        lines = HOLD.index_lines(IDX)
        self.assertEqual(len(lines), 5)  # the text-era "SC 13G" is not read
        HOLD.discover(st, lines, {"101"})
        self.assertEqual(st["pending"], {"101|900": ["edgar/data/101/0000900-26-000002.txt", "2026-09-10",
                                                     "SCHEDULE 13G/A"]})
        HOLD.apply(st, "101|900", HOLD.parse(g13(shares=5e6)), "edgar/data/101/0000900-26-000002.txt", "2026-09-10")
        st["pending"] = {}
        HOLD.discover(st, lines, {"101"})  # read already: nothing pending again
        self.assertEqual(st["pending"], {})


def holder(shares, pct=12.0, ciks=("900",), filed="2026-09-10", passive=False):
    return {"ciks": list(ciks), "shares": shares, "pct": pct, "event": filed, "filed": filed, "acc": "a",
            "form": "SCHEDULE 13G", "passive": passive}


class Float(unittest.TestCase):
    def evaluate(self, own, hold, n=60e6):
        b = {"B": series([10.0] * 300)}
        e = env(b, uni("B", cik0=101), fund=shares(101, n), own=own, holders=hold)
        return evaluate.run(parser.parse("sh_price_o1"), e).rows[0]["ctx"]

    def test_13g_holder_that_files_form4_counts_once(self):
        own = {"issuers": {"101": {"7": {"role": "do", "h": [["2026-09-01", 5e6]]},
                                   "900": {"role": "t", "h": [["2026-09-01", 9e6]]}}}}
        hold = {"quarters": ["q"] * HOLD.QUARTERS, "pending_issuers": [],
                "issuers": {"101": {"900": holder(9e6), "950": holder(4e6, pct=6.6, ciks=("950",))}}}
        c = self.evaluate(own, hold)
        # 60M - (5M officers + 9M 10% owner, Form 4) - 4M (the other 13G holder); the 900 13G is the same 9M
        self.assertEqual((c.get("float"), c.get("float_method")), (42e6, "insiders+5pct"))

    def test_qualified_institutions_and_small_holders_stay_in_the_float(self):
        hold = {"quarters": ["q"] * HOLD.QUARTERS, "pending_issuers": [],
                "issuers": {"101": {"800": holder(6e6, passive=True), "801": holder(2e6, pct=3.3),
                                    "802": holder(3e6, pct=5.0)}}}
        c = self.evaluate({}, hold)
        self.assertEqual((c.get("float"), c.get("float_method")), (57e6, "insiders+5pct"))

    def test_incomplete_13d13g_coverage_is_labelled_insiders(self):
        own = {"issuers": {"101": {"7": {"role": "o", "h": [["2026-09-01", 5e6]]}}}}
        hold = {"quarters": ["q"] * HOLD.QUARTERS, "pending_issuers": ["101"], "issuers": {}}
        c = self.evaluate(own, hold)
        self.assertEqual((c.get("float"), c.get("float_method")), (55e6, "insiders"))

    def test_float_never_at_or_below_zero(self):
        own = {"issuers": {"101": {"7": {"role": "t", "h": [["2026-09-01", 40e6]]}}}}
        hold = {"quarters": ["q"] * HOLD.QUARTERS, "pending_issuers": [],
                "issuers": {"101": {"950": holder(30e6, pct=50.0, ciks=("950",))}}}
        c = self.evaluate(own, hold)  # 60M - 40M - 30M < 0: insiders only
        self.assertEqual((c.get("float"), c.get("float_method")), (20e6, "insiders"))
        self.assertEqual(c.get("float_flag"), "holdings >= shares outstanding")
        own["issuers"]["101"]["7"]["h"] = [["2026-09-01", 70e6]]
        c = self.evaluate(own, {})  # stale holdings above the share count: shares outstanding, flagged
        self.assertEqual((c.get("float"), c.get("float_method")), (60e6, "outstanding_proxy"))
        self.assertGreater(c.get("float"), 0)

    def test_message_footnote_counts_the_float_methods(self):
        import msgrules
        from bot.screener import format as fmt
        own = {"issuers": {"101": {"7": {"role": "o", "h": [["2026-09-01", 5e6]]}}}}
        b = {"A": series([10.0] * 300), "B": series([10.0] * 300)}
        fund = {"facts": {}, "shares": {"100": [["2026-06-30", "2026-06-30", 40e6]],
                                        "101": [["2026-06-30", "2026-06-30", 60e6]]}}
        q = parser.parse("sh_float_u100")
        res = evaluate.run(q, env(b, uni("A", "B"), fund=fund, own=own))
        text = fmt.screen_text("סריקה", q, res, dt.datetime(2026, 10, 20, 21, 0, tzinfo=NY))
        self.assertIn("מניות צפות: כל המניות (אין נתוני אחזקות) (<code>1</code>), בניכוי בעלי עניין בלבד "
                      "(<code>1</code>).", text)
        msgrules.check(self, text)

    def test_point_in_time(self):
        hold = {"quarters": ["q"] * HOLD.QUARTERS, "pending_issuers": [],
                "issuers": {"101": {"950": holder(10e6, filed="2026-10-25")}}}
        c = self.evaluate({}, hold)  # env day 2026-10-20: the filing came later
        self.assertEqual(c.get("float"), 60e6)


if __name__ == "__main__":
    unittest.main()
