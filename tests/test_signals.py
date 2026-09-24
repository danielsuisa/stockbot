"""Block 1 - insider signal quality: classification, 10b5-1, relative size, cluster quality, dedup, market regime."""
import datetime as dt
import json
import os
import tempfile
import unittest
from unittest import mock

from bot import check, common, form4, market, scan
from test_bot import NO_ENRICH


def tx(code, shares, px, date="2026-09-21", ad="A", own="D", post="10000", notes=(), table="nonDerivativeTransaction"):
    refs = "".join(f'<footnoteId id="{n}"/>' for n in notes)
    return (f"<{table}><transactionDate><value>{date}</value></transactionDate><transactionCoding>"
            f"<transactionCode>{code}</transactionCode>{refs}</transactionCoding><transactionAmounts><transactionShares>"
            f"<value>{shares}</value></transactionShares><transactionPricePerShare><value>{px}</value>"
            f"</transactionPricePerShare><transactionAcquiredDisposedCode><value>{ad}</value>"
            f"</transactionAcquiredDisposedCode></transactionAmounts><postTransactionAmounts>"
            f"<sharesOwnedFollowingTransaction><value>{post}</value></sharesOwnedFollowingTransaction>"
            f"</postTransactionAmounts><ownershipNature><directOrIndirectOwnership><value>{own}</value>"
            f"</directOrIndirectOwnership></ownershipNature></{table}>")


def doc(txs, rel="<isDirector>1</isDirector>", owner="DOE JOHN", ocik="100", box=False, notes=None, form="4",
        original="", issuer="0000012345"):
    fn = "".join(f'<footnote id="{k}">{v}</footnote>' for k, v in (notes or {}).items())
    extra = f"<dateOfOriginalSubmission>{original}</dateOfOriginalSubmission>" if original else ""
    return form4.parse(
        f"<ownershipDocument><documentType>{form}</documentType>{extra}<issuer><issuerCik>{issuer}</issuerCik>"
        f"<issuerName>Acme</issuerName><issuerTradingSymbol>ACME</issuerTradingSymbol></issuer><reportingOwner>"
        f"<reportingOwnerId><rptOwnerCik>{ocik}</rptOwnerCik><rptOwnerName>{owner}</rptOwnerName></reportingOwnerId>"
        f"<reportingOwnerRelationship>{rel}</reportingOwnerRelationship></reportingOwner>"
        f"<aff10b5One>{'true' if box else 'false'}</aff10b5One><nonDerivativeTable>"
        + "".join(t for t in txs if t.startswith("<nonDerivative"))
        + "</nonDerivativeTable><derivativeTable>" + "".join(t for t in txs if t.startswith("<derivative"))
        + f"</derivativeTable><footnotes>{fn}</footnotes></ownershipDocument>")


def row(acc, who, value, weight=1.0, od=True, pct=0.2, kind="open", cik=1, sig=None, indirect=False, role="מנהל"):
    return {"acc": acc, "cik": cik, "ticker": "ACME", "company": "Acme", "insider": who, "role": role,
            "insider_cik": who, "od": od, "weight": weight, "first": "2026-09-20", "last": "2026-09-20", "shares": 1,
            "value": value, "price": 10.0, "filed": "2026-09-21", "kind": kind, "plan_value": value if kind == "plan" else 0,
            "pct": pct, "post": 100, "sig": sig, "indirect": indirect}


class Classification(unittest.TestCase):
    def test_kinds_and_plans(self):
        d = doc([tx("P", 1000, 10), tx("P", 500, 12, notes=["F1"]), tx("M", 300, 5), tx("A", 200, 0),
                 tx("S", 100, 13, ad="D"), tx("A", 50, 0, table="derivativeTransaction")],
                notes={"F1": "Purchased pursuant to a Rule 10b5-1 trading plan adopted May 5.", "F2": "Weighted average."})
        self.assertEqual([b["plan"] for b in d["buys"]], [False, True])  # only the line the 10b5-1 footnote covers
        self.assertEqual([(a["code"], a["kind"], a["derivative"]) for a in d["acq"]],
                         [("M", "exercise", False), ("A", "grant", False), ("A", "grant", True)])
        s = form4.summarize(d)
        self.assertEqual((s["kind"], s["value"], s["plan_value"], s["shares"]), ("open", 10000.0, 6000.0, 1000.0))

    def test_checkbox_without_footnote_marks_all_purchases_plan(self):
        d = doc([tx("P", 1000, 10)], box=True)
        self.assertTrue(d["buys"][0]["plan"])
        self.assertEqual(form4.summarize(d)["kind"], "plan")
        d = doc([tx("P", 1000, 10), tx("S", 5, 1, ad="D", notes=["F1"])], box=True, notes={"F1": "Rule 10b5-1 plan sale"})
        self.assertFalse(d["buys"][0]["plan"])  # the plan footnote covers the sale only

    def test_relative_size_and_symbolic(self):
        s = form4.summarize(doc([tx("P", 1000, 10, post="100000")]))
        self.assertAlmostEqual(s["pct"], 0.01)
        self.assertTrue(form4.symbolic({**s, "value": 10000.0}))  # 1% of holdings and $10K
        self.assertFalse(form4.symbolic({**s, "value": 200000.0}))  # big $ is not symbolic
        self.assertEqual(form4.summarize(doc([tx("P", 1000, 10, post="1000")]))["pct"], 1.0)  # a new position
        self.assertIsNone(form4.summarize(doc([tx("P", 1000, 10, post="")]))["pct"])

    def test_role_weights(self):
        w = lambda rel: doc([tx("P", 1, 1)], rel=rel)["owners"][0]["weight"]
        self.assertEqual(w("<isOfficer>1</isOfficer><officerTitle>Chief Executive Officer</officerTitle>"), 1.5)
        self.assertEqual(w("<isOfficer>true</isOfficer><officerTitle>EVP &amp; CFO</officerTitle>"), 1.5)
        self.assertEqual(w("<isDirector>1</isDirector><otherText>Chairman of the Board</otherText>"), 1.5)
        self.assertEqual(w("<isOfficer>1</isOfficer><officerTitle>SVP Sales</officerTitle>"), 1.0)
        self.assertEqual(w("<isDirector>1</isDirector>"), 0.7)
        self.assertEqual(w("<isTenPercentOwner>1</isTenPercentOwner>"), 0.0)


class Clusters(unittest.TestCase):
    today = dt.date(2026, 9, 24)

    def test_three_insiders_open_market_only(self):
        rs = [row("a", "x", 60000), row("b", "y", 50000)]
        self.assertFalse(scan.evaluate(rs, None)["cluster"])  # two is no longer a cluster
        rs.append(row("c", "z", 40000, kind="plan"))
        self.assertFalse(scan.evaluate(rs, None)["cluster"])  # a 10b5-1 plan buyer does not count
        rs.append(row("d", "w", 40000))
        ev = scan.evaluate(rs, None)
        self.assertEqual((ev["cluster"], len(ev["plan"])), ((3, 150000), 1))
        self.assertFalse(scan.evaluate([row("p", "q", 900000, kind="plan")], None)["big"])  # plan buys never alert

    def test_quality_score_parts(self):
        rs = [row("a", "ceo", 400000, weight=1.5, pct=0.25), row("b", "cfo", 300000, weight=1.5, pct=0.25),
              row("c", "dir", 300000, weight=0.7, pct=0.25)]
        score, parts = scan.quality(rs, {"tag": "panic"})
        self.assertEqual(parts, {"breadth": 20, "seniority": 21, "size": 10, "conviction": 15, "market": 10})
        self.assertEqual(score, 76)  # 20 + round(25 * 3.7 / 4.5) + 10 + 15 + 10
        self.assertEqual(scan.quality(rs, {"tag": "euphoria"})[0], 56)
        token = [row("a", "ceo", 5000, weight=1.5, pct=0.01)]  # symbolic: weight halved, no conviction
        self.assertEqual(scan.quality(token, None)[1]["seniority"], round(25 * 0.75 / 4.5))
        self.assertEqual(scan.quality(token, None)[1]["conviction"], 0)

    def test_etra_regression_20m_not_40m(self):
        """ETRA 2026-09-21: OrbiMed and director Carl Gordon each filed the identical indirect trades."""
        lines = [tx("P", 333333, 15, own="I"), tx("P", 1000000, 15, own="I")]
        fund = doc(lines, owner="Orbimed Advisors Llc", ocik="1")
        gordon = doc(lines, owner="GORDON CARL L", ocik="2")
        with mock.patch.object(common, "tickers", return_value={"ETRA": (1, "Electra")}), \
                mock.patch.object(scan, "foreign", return_value=False), mock.patch.object(scan, "pay", return_value={}), \
                mock.patch.object(scan, "links", return_value={}), NO_ENRICH:
            rows = [scan.make_row(acc, d, d["buys"], "ETRA", "2026-09-23") for acc, d in (("880", fund), ("881", gordon))]
            st = {"buys": rows, "alerted": {}, "regime": {"tag": "normal"}}
            (text, marks, snaps, entries), = scan.alerts(st, self.today)
        self.assertIn("<code>20.0M$</code>", text)
        self.assertNotIn("40.0M$", text)
        self.assertNotIn("אשכול", text)  # one purchase, one buyer
        self.assertEqual(sorted(marks[str(rows[0]["cik"])]), ["880", "881"])  # both filings marked as alerted
        self.assertEqual(common.rtl_bad_lines(text), [])

    def test_multi_transaction_filing_is_one_row(self):
        d = doc([tx("P", 100, 10), tx("P", 200, 11), tx("P", 300, 12)])
        with mock.patch.object(common, "tickers", return_value={"ACME": (12345, "Acme")}):
            r = scan.make_row("1", d, d["buys"], "ACME", "2026-09-21")
        self.assertEqual((r["shares"], r["value"]), (600.0, 100 * 10 + 200 * 11 + 300 * 12))

    def test_amendment_without_purchases_removes_row_and_unmatched_is_new(self):
        orig = doc([tx("P", 100, 10)])
        with mock.patch.object(common, "tickers", return_value={"ACME": (12345, "Acme")}):
            r = scan.make_row("o", orig, orig["buys"], "ACME", "2026-09-21")
            buys = {"o": r}
            gone = doc([tx("S", 100, 10, ad="D")], form="4/A", original="2026-09-21")
            self.assertEqual(scan.amend(buys, "a", gone, None), "o")
            self.assertEqual(buys, {})  # the corrected filing has no purchase any more
            self.assertIsNone(scan.amend({}, "a", gone, None))  # original not in the window -> treated as new

    def test_block_shows_quality_plan_info_and_regime(self):
        rs = [row("a", "x", 60000, weight=1.5), row("b", "y", 50000), row("c", "z", 40000, weight=0.7),
              row("d", "w", 9000, kind="plan")]
        ev = scan.evaluate(rs, {"tag": "normal", "spy20": 0.01, "iwm20": 0.02, "vix": 16.0, "vol_source": "VIX"})
        info = [{"acc": "i", "cik": 1, "last": "2026-09-20", "kinds": {"exercise": 2, "grant": 1}}]
        with mock.patch.object(scan, "pay", return_value={"peo": 1e6, "neo": 5e5, "end": "2025-12-31"}):
            text = scan.block(ev, set(), {"tag": "normal", "spy20": 0.01, "iwm20": 0.02, "vix": 16.0,
                                          "vol_source": "VIX"}, info)
        for want in ("איכות: <code>", "רוחב", "בכירות", "10b5-1", "מימוש אופציות", "מצב שוק: <b>רגיל</b>", "מהשכר השנתי"):
            self.assertIn(want, text)
        self.assertEqual(common.rtl_bad_lines(text), [])

    def test_check_report_marks_plan_buys(self):
        plan = doc([tx("P", 100, 10, notes=["F1"])], notes={"F1": "Rule 10b5-1 plan"})
        subs = [{"form": "4", "filingDate": "2026-09-21", "accessionNumber": "0-0-1", "primaryDocument": "x/f.xml"}]
        with mock.patch.object(form4, "fetch", return_value=plan):
            lines = check.insiders(12345, subs)
        self.assertTrue(any("לא נספרה" in l for l in lines))
        self.assertTrue(any("<code>0</code> דיווחי רכישה" in l for l in lines))  # open-market count excludes it


class Schema(unittest.TestCase):
    def test_v1_state_migrates(self):
        v1 = {"days": {"20260922": "ok"}, "buys": [
            {"acc": "a", "cik": 1, "ticker": "T", "company": "C", "insider": "X", "role": "דירקטור, CEO",
             "insider_cik": "9", "od": True, "first": "2026-09-20", "last": "2026-09-20", "shares": 1, "value": 5.0,
             "price": 5.0, "filed": "2026-09-21"},
            {"acc": "b", "cik": 1, "ticker": "T", "company": "C", "insider": "Y", "role": "דירקטור", "insider_cik": "8",
             "od": True, "first": "2026-09-20", "last": "2026-09-20", "shares": 1, "value": 5.0, "price": 5.0,
             "filed": "2026-09-21"}], "alerted": {"1": ["a"]}}
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "state.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(v1, fh)
            st = scan.load(scan.Path(p))
            scan.save(scan.Path(p), st)
            again = scan.load(scan.Path(p))
        self.assertEqual((st["v"], st["info"], st["prices"], st["alerted"]), (2, [], {}, {"1": ["a"]}))
        self.assertEqual([(r["kind"], r["weight"]) for r in st["buys"]], [("open", 1.5), ("open", 0.7)])
        self.assertEqual(again["buys"], st["buys"])


PROXY = ('<xbrli:context id="c1"><xbrli:entity><xbrli:identifier>1</xbrli:identifier></xbrli:entity><xbrli:period>'
         '<xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>'
         '<xbrli:context id="c2"><xbrli:entity><xbrli:identifier>1</xbrli:identifier></xbrli:entity><xbrli:period>'
         '<xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>'
         '<xbrli:context id="c3"><xbrli:entity><xbrli:identifier>1</xbrli:identifier><xbrli:segment>x</xbrli:segment>'
         '</xbrli:entity><xbrli:period><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>'
         '<ix:nonFraction name="ecd:PeoTotalCompAmt" contextRef="c1" scale="0">9,000,000</ix:nonFraction>'
         '<ix:nonFraction name="ecd:PeoTotalCompAmt" contextRef="c2" scale="0">12,000,000</ix:nonFraction>'
         '<ix:nonFraction name="ecd:PeoTotalCompAmt" contextRef="c3" scale="0">99,000,000</ix:nonFraction>'
         '<ix:nonFraction name="ecd:NonPeoNeoAvgTotalCompAmt" contextRef="c2" scale="3">3,000</ix:nonFraction>')


class Pay(unittest.TestCase):
    def setUp(self):
        scan.pay.cache_clear()
        scan._pay_budget[0] = scan.MAX_PAY

    tearDown = setUp

    def test_proxy_pay_parsing_and_ratio(self):
        subs = {"filings": {"recent": {"form": ["DEF 14A"], "accessionNumber": ["0-0-1"], "primaryDocument": ["p.htm"]}}}
        with mock.patch.object(common, "submissions", return_value=subs), \
                mock.patch.object(common, "fetch", return_value=PROXY.encode()):
            self.assertEqual(scan.pay(1), {"peo": 12e6, "neo": 3e6, "end": "2025-12-31"})  # latest, company-wide only
            ceo = row("a", "x", 1.2e6, weight=1.5, role="דירקטור, Chief Executive Officer")
            self.assertAlmostEqual(scan.pay_ratio(ceo), 0.1)
            self.assertAlmostEqual(scan.pay_ratio(row("b", "y", 3e5, weight=1.0, role="SVP")), 0.1)
            self.assertIsNone(scan.pay_ratio(row("c", "z", 3e5, weight=0.7)))  # directors: holdings ratio only

    def test_budget_and_missing_proxy(self):
        scan._pay_budget[0] = 0
        self.assertEqual(scan.pay(2), {})
        scan._pay_budget[0] = 5
        with mock.patch.object(common, "submissions", return_value={"filings": {"recent": {"form": ["10-K"],
                               "accessionNumber": ["x"], "primaryDocument": ["y"]}}}):
            self.assertEqual(scan.pay(3), {})


def bars(closes, start=dt.date(2026, 5, 1)):
    return [(start + dt.timedelta(i), c, 1000) for i, c in enumerate(closes)]


class Regime(unittest.TestCase):
    def run_regime(self, spy, iwm, vix):
        hist = {"SPY": bars(spy), "IWM": bars(iwm), "%5EVIX": bars(vix) if vix else []}
        with mock.patch.object(market, "history", side_effect=lambda t, rng="6mo": hist[t]):
            return market.regime()

    def test_tags(self):
        flat = [100.0] * 70
        self.assertEqual(self.run_regime(flat, flat, [18.0])["tag"], "normal")
        self.assertEqual(self.run_regime(flat, flat, [35.0])["tag"], "panic")
        self.assertEqual(self.run_regime([100.0] * 50 + [92.0] * 20 + [85.0], flat, [20.0])["tag"], "panic")  # -7.6%
        up = [100.0 + i * 0.3 for i in range(70)]
        self.assertEqual(self.run_regime(up, up, [13.0])["tag"], "euphoria")
        self.assertEqual(self.run_regime([], [], [])["tag"], "unknown")

    def test_realized_vol_fallback_and_line(self):
        wiggle = [100.0 + (3 if i % 2 else -3) for i in range(70)]
        r = self.run_regime(wiggle, wiggle, None)
        self.assertEqual(r["vol_source"], "SPY realized")
        self.assertGreater(r["vix"], 28)  # +-3% daily swings annualize far above 28 -> panic
        self.assertEqual(r["tag"], "panic")
        for tag in ("panic", "normal", "euphoria", "unknown"):
            line = market.regime_line({**r, "tag": tag})
            self.assertEqual(common.rtl_bad_lines(line), [], line)


if __name__ == "__main__":
    unittest.main()
