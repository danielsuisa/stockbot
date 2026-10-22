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


class Passive(unittest.TestCase):
    def test_a_known_institution_is_not_fetched_again(self):
        from unittest import mock
        st = {"filers": {"900": [3, 0], "901": [3, 1]}, "quarters": HOLD._quarters("2026-10-22", HOLD.QUARTERS), "days": [],
              "issuers": {}, "pending": {"101|900": ["edgar/data/101/a-1.txt", "2026-09-10", "SCHEDULE 13G/A"],
                                         "101|901": ["edgar/data/101/a-2.txt", "2026-09-10", "SCHEDULE 13G/A"]}}
        with mock.patch.object(HOLD.stores, "load", return_value={"data": st, "meta": {}}),                 mock.patch.object(HOLD.stores, "save", return_value={}),                 mock.patch.object(HOLD.common, "fetch", return_value=g13(filer="901").encode()) as f:
            store, _, stats = HOLD.update({"101"}, [], "2026-10-22")
        self.assertEqual([c.args[0] for c in f.call_args_list], ["https://www.sec.gov/Archives/edgar/data/101/a-2.txt"])
        self.assertEqual((stats["passive_skipped"], stats["parsed"], store["pending"]), (1, 1, {}))
        self.assertTrue(HOLD.covered(store, "101"))


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

    def test_a_13d_group_larger_than_its_form4_lines_counts_once_at_the_larger_amount(self):
        own = {"issuers": {"101": {"21+22": {"role": "t", "h": [["2026-01-30", 30e6]]}}}}
        hold = {"quarters": ["q"] * HOLD.QUARTERS, "pending_issuers": [],
                "issuers": {"101": {"22": holder(34e6, pct=56.7, ciks=("22", "23"))}}}
        c = self.evaluate(own, hold)  # 60M - 30M (Form 4) - 4M (the 13D group's excess) = 26M
        self.assertEqual((c.get("float"), c.get("float_method")), (26e6, "insiders+5pct"))

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

    def test_issuer_public_float_only_without_any_holdings(self):
        from bot.screener.stores import publicfloat as PF
        # 2025-06-30: $40M non-affiliate value at a raw $2.00 close = 20M shares of 30M; a 1:4 reverse split since
        # (split-adjusted close $8.00): 10M affiliates then = 2.5M now; 60M today - 2.5M = 57.5M
        rec = {"end": "2025-06-30", "val": 40e6, "raw": 2.0, "split": 8.0, "so_then": 30e6}
        self.assertEqual(PF.carried(rec, 60e6), 57.5e6)
        self.assertIsNone(PF.carried({}, 60e6))
        b = {"B": series([10.0] * 300)}
        e = env(b, uni("B", cik0=101), fund=shares(101, 60e6), public_float={"101": rec})
        c = evaluate.run(parser.parse("sh_price_o1"), e).rows[0]["ctx"]
        self.assertEqual((c.get("float"), c.get("float_method")), (57.5e6, "issuer_public_float"))
        own = {"issuers": {"101": {"7": {"role": "o", "h": [["2026-09-01", 5e6]]}}}}
        e = env(b, uni("B", cik0=101), fund=shares(101, 60e6), own=own, public_float={"101": rec})
        c = evaluate.run(parser.parse("sh_price_o1"), e).rows[0]["ctx"]  # Form 3/4/5 data wins
        self.assertEqual((c.get("float"), c.get("float_method")), (55e6, "insiders"))

    def test_nearest_cover_share_count(self):
        from bot.screener.stores import publicfloat as PF
        fund = {"shares": {"101": [["2025-05-20", "2025-05-08", 30e6], ["2025-08-20", "2025-08-01", 31e6],
                                   ["2026-03-01", "2026-02-20", 40e6]]}}
        self.assertEqual(PF.nearest_shares(fund, 101, "2025-06-30"), (31e6, "2025-08-01"))
        self.assertEqual(PF.nearest_shares(fund, 101, "2023-06-30"), (None, None))  # nothing within 200 days

    def test_holdings_and_shares_from_before_a_split_are_scaled(self):
        # BKYI (OBSERVED): a 1:10 reverse split on 2026-04-30; a 10% owner's 595,000 shares reported in 2024-11 and
        # a director's 10,778 after the split; 1.09M shares outstanding after it
        own = {"issuers": {"101": {"9": {"role": "t", "h": [["2024-11-27", 595_000.0]]},
                                   "7": {"role": "do", "h": [["2026-06-30", 10_778.0]]}}}}
        hold = {"quarters": ["q"] * HOLD.QUARTERS, "pending_issuers": [],
                "issuers": {"101": {"950": {**holder(1_000_000.0, pct=8.0, ciks=("950",), filed="2025-02-10"),
                                            "event": "2025-02-01"}}}}
        b = {"B": series([10.0] * 300)}
        splits = {"B": [["2026-04-30", 0.1]]}
        e = env(b, uni("B", cik0=101), fund=shares(101, 1_085_360), own=own, holders=hold, splits=splits)
        c = evaluate.run(parser.parse("sh_price_o1"), e).rows[0]["ctx"]
        # 1,085,360 - 59,500 - 10,778 - 100,000 = 915,082 (unscaled: 1,085,360 - 605,778 - 1,000,000 < 0)
        self.assertEqual((round(c.get("float")), c.get("float_method")), (915_082, "insiders+5pct"))
        old = env(b, uni("B", cik0=101), fund={"facts": {}, "shares": {"101": [["2026-03-01", "2026-02-20", 10e6]]}},
                  splits=splits)
        self.assertEqual(evaluate.run(parser.parse("sh_price_o1"), old).rows[0]["ctx"].get("shares_outstanding"), 1e6)

    def test_point_in_time(self):
        hold = {"quarters": ["q"] * HOLD.QUARTERS, "pending_issuers": [],
                "issuers": {"101": {"950": holder(10e6, filed="2026-10-25")}}}
        c = self.evaluate({}, hold)  # env day 2026-10-20: the filing came later
        self.assertEqual(c.get("float"), 60e6)


if __name__ == "__main__":
    unittest.main()
