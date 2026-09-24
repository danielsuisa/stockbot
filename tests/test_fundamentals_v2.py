"""Offline tests for the fundamentals basis line, 10-Q TTM refresh, Beneish caveat and freshness flag (no network)."""
import datetime as dt
import json
import unittest
from unittest import mock

from bot import common, fundamentals as F

TODAY = dt.date(2026, 9, 24)
# a 52/53-week September year (Apple's calendar): quarter ends are 364 days apart, not 365
FY = {"2023-09-30": ("2022-10-02", "2023-11-03"), "2024-09-28": ("2023-10-01", "2024-11-01"),
      "2025-09-27": ("2024-09-29", "2025-10-31")}  # FY end: (FY start, 10-K filed)
Q = {"2024-06-29": ("2023-10-01", "2024-03-31", "2024-08-02"), "2025-06-28": ("2024-09-29", "2025-03-30", "2025-08-01"),
     "2026-06-27": ("2025-09-28", "2026-03-29", "2026-07-31")}  # Q3 end: (YTD start, 3-month start, 10-Q filed)
E, E1, E2 = sorted(Q, reverse=True)
ANNUAL = {  # FY2023, FY2024, FY2025
    "Revenues": (383, 391, 416), "CostOfRevenue": (214, 210, 221), "NetIncomeLoss": (97, 94, 112),
    "NetCashProvidedByUsedInOperatingActivities": (111, 118, 111), "OperatingIncomeLoss": (114, 123, 133),
    "SellingGeneralAndAdministrativeExpense": (25, 26, 27), "DepreciationDepletionAndAmortization": (11, 11, 12),
    "WeightedAverageNumberOfSharesOutstandingBasic": (15.8e9, 15.3e9, 14.9e9)}
YTD = {  # 9-month YTD ending E2, E1, E (the E1 revenue is restated 312 -> 313 in the newest 10-Q)
    "Revenues": (296, 313, 364), "CostOfRevenue": (160, 170, 190), "NetIncomeLoss": (79, 85, 101),
    "NetCashProvidedByUsedInOperatingActivities": (91, 81, 120), "OperatingIncomeLoss": (94, 101, 122),
    "WeightedAverageNumberOfSharesOutstandingBasic": (15.4e9, 15.0e9, 14.7e9)}
THREE = {"Revenues": (86, 94, 109), "NetIncomeLoss": (21, 23, 30),  # 3-month facts ending on the same dates: ignored
         "NetCashProvidedByUsedInOperatingActivities": (1, 2, 3), "OperatingIncomeLoss": (25, 28, 36),
         "WeightedAverageNumberOfSharesOutstandingBasic": (15.3e9, 14.9e9, 14.6e9)}
INSTANT = {  # FY2023, FY2024, FY2025 | E2, E1, E
    "Assets": ((350, 365, 359), (332, 331, 383)), "AssetsCurrent": ((140, 153, 148), (130, 122, 160)),
    "LiabilitiesCurrent": ((145, 176, 166), (131, 141, 150)), "Liabilities": ((290, 308, 285), (270, 266, 290)),
    "RetainedEarningsAccumulatedDeficit": ((-1, -19, -14), (-5, 2, 5)),
    "StockholdersEquity": ((60, 57, 74), (62, 65, 93)), "LongTermDebtNoncurrent": ((95, 86, 78), (97, 90, 72)),
    "AccountsReceivableNetCurrent": ((29, 33, 39), None), "PropertyPlantAndEquipmentNet": ((43, 45, 49), None)}


def rows():
    """Every fact as (tag, val, start, end, form, filed); annual comparatives are re-reported by the next 10-K."""
    out, fys = [], sorted(FY)
    for tag, vals in ANNUAL.items():
        for i, (end, val) in enumerate(zip(fys, vals)):
            for form_year in fys[i:i + 2]:  # own 10-K + the next one's comparative (latest filing wins)
                out.append((tag, val, FY[end][0], end, "10-K", FY[form_year][1]))
    for tag, (fyv, qv) in INSTANT.items():
        for end, val in zip(fys, fyv):
            out.append((tag, val, None, end, "10-K", FY[end][1]))
        for end, val in zip(sorted(Q), qv or ()):
            out.append((tag, val, None, end, "10-Q", Q[end][2]))
    qs = sorted(Q)
    for tag, vals in YTD.items():
        for i, (end, val) in enumerate(zip(qs, vals)):
            out.append((tag, val, Q[end][0], end, "10-Q", Q[end][2]))
            if i + 1 < len(qs):  # comparative in next year's 10-Q
                out.append((tag, 313 if (tag, end) == ("Revenues", E1) else val, Q[end][0], end, "10-Q", Q[qs[i + 1]][2]))
    out = [r if (r[0], r[3], r[5]) != ("Revenues", E1, Q[E1][2]) else r[:1] + (312,) + r[2:] for r in out]
    for tag, vals in THREE.items():
        for end, val in zip(qs, vals):
            out.append((tag, val, Q[end][1], end, "10-Q", Q[end][2]))
    return out


def companyfacts(drop=lambda r: False, extra=(), old=False):
    g = {}
    for r in [r for r in rows() if not drop(r)] + list(extra):
        tag, val, start, end, form, filed = r
        unit = "shares" if "Shares" in tag else "USD"
        e = {"end": end, "val": val, "accn": f"{form}-{filed}", "form": form, "filed": filed, "fy": 0, "fp": "x"}
        g.setdefault(tag, {"units": {}})["units"].setdefault(unit, []).append({**e, **({"start": start} if start else {})})
    dei = {F.SHARES: {"units": {"shares": [{"end": "2026-07-17", "val": 14.6, "accn": "q", "form": "10-Q",
                                            "filed": "2026-07-31"}]}}}
    if old:  # a company filing with SEC since 2009 is not "young"
        dei["EntityPublicFloat"] = {"units": {"USD": [{"end": "2009-03-28", "val": 1, "accn": "o", "form": "10-K",
                                                       "filed": "2009-10-27"}]}}
    return {"facts": {"us-gaap": g, "dei": dei}}


class Base(unittest.TestCase):
    def run_it(self, cf, sic=3571, today=TODAY, quote=(50.0, 1.0)):
        with mock.patch.object(common, "get_json", return_value=cf), \
                mock.patch.object(common, "cik_tickers", return_value={1: "ACME"}), \
                mock.patch.object(F, "quote", return_value=quote), mock.patch.object(F, "_today", return_value=today):
            res = F.analyze(1, "ACME", sic)
        text = "\n".join(F.format_he(res))
        self.assertEqual(common.rtl_bad_lines(text), [], text)
        self.assertLessEqual(common.visible(text), 1500, text)
        return res, text


class Ttm(Base):
    def test_ttm_math_and_basis(self):
        res, text = self.run_it(companyfacts(old=True))
        t = res["ttm"]
        self.assertEqual(t["end"], E)
        # TTM = FY + YTD - prior-year YTD (YTD, not the 3-month facts; the restated comparative wins)
        self.assertEqual(t["v"]["rev"][:2], [416 + 364 - 313, 391 + 313 - 296])
        self.assertEqual(t["v"]["ebit"][:2], [133 + 122 - 101, 123 + 101 - 94])
        self.assertEqual(t["v"]["ni"][:2], [112 + 101 - 85, 94 + 85 - 79])
        self.assertEqual(t["v"]["cfo"][:2], [111 + 120 - 81, 118 + 81 - 91])
        self.assertEqual(t["v"]["gp"][:2], [467 - (221 + 190 - 170), 408 - (210 + 170 - 160)])  # derived per period
        self.assertEqual(t["v"]["sh"][:2], [14.7e9, 15.0e9])  # weighted-average shares: the YTD value, never summed
        self.assertEqual(t["v"]["ta"], [383, 331, 332])  # balance sheets at E, ~1y and ~2y earlier (364-day years)
        self.assertEqual([c["ok"] for c in res["pio"]], [True] * 9)
        self.assertEqual(res["basis"], {
            "pio": {"kind": "TTM", "end": E, "form": "10-Q", "filed": "2026-07-31"},
            "alt": {"kind": "TTM", "end": E, "form": "10-Q", "filed": "2026-07-31"},
            "ben": {"kind": "FY", "end": "2025-09-27", "form": "10-K", "filed": "2025-10-31"}})
        z = 1.2 * 10 / 383 + 1.4 * 5 / 383 + 3.3 * 154 / 383 + 0.6 * 50 * 14.6 / 290 + 1.0 * 467 / 383
        self.assertAlmostEqual(res["alt"]["z"], z)
        self.assertIn("m", res["ben"])  # Beneish stays on the annual figures
        self.assertAlmostEqual(res["ben"]["idx"]["SGI"], 416 / 391)
        for want in (
                "<b>🧮 ציונים פורנזיים מדוחות <code>10-K</code> ו־<code>10-Q</code></b>",
                "<b>פיוטרוסקי F: <code>9/9</code></b> (TTM עד <code>2026-06-27</code>, <code>10-Q</code> הוגש"
                " <code>2026-07-31</code>)",
                "· אזור בטוח (TTM עד <code>2026-06-27</code>, <code>10-Q</code> הוגש <code>2026-07-31</code>)",
                "· סיכון נמוך (<code>10-K</code> FY <code>2025-09-27</code>, הוגש <code>2025-10-31</code>)",
                "(TTM) עד <code>2026-06-27</code>: הכנסות <code>467$</code>",
                "לא הונפקו מניות (ממוצע משוקלל מתחילת השנה ב־<code>10-Q</code>, לא TTM): <code>14.700B</code> מול"
                " <code>15.000B</code>"):
            self.assertIn(want, text)
        self.assertNotIn("⚠️", text)  # fresh, not fast-growing, not young

    def test_amended_10q_is_the_basis(self):
        amend = [("Revenues", 365, Q[E][0], E, "10-Q/A", "2026-08-20")]
        res, text = self.run_it(companyfacts(extra=amend, old=True))
        self.assertEqual(res["ttm"]["v"]["rev"][0], 416 + 365 - 313)  # the latest filing wins per period
        self.assertEqual(res["basis"]["pio"], {"kind": "TTM", "end": E, "form": "10-Q/A", "filed": "2026-08-20"})
        self.assertIn("(TTM עד <code>2026-06-27</code>, <code>10-Q/A</code> הוגש <code>2026-08-20</code>)", text)

    def test_piotroski_falls_back_to_fy_when_a_ttm_input_is_missing(self):
        drop = lambda r: r[0] == "NetCashProvidedByUsedInOperatingActivities" and r[4] == "10-Q" and r[3] == E1
        res, text = self.run_it(companyfacts(drop, old=True))  # no prior-year YTD cash flow -> no TTM cash flow
        self.assertIsNone(res["ttm"]["v"]["cfo"][0])
        self.assertEqual(res["basis"]["pio"]["kind"], "FY")
        self.assertEqual(res["basis"]["alt"]["kind"], "TTM")  # Altman needs no cash flow
        self.assertEqual(res["pio"][0]["vals"], [112 / 365])  # FY: NI 2025 / assets at the FY 2024 end
        self.assertIn("<b>פיוטרוסקי F: <code>8/9</code></b> (<code>10-K</code> FY <code>2025-09-27</code>, הוגש"
                      " <code>2025-10-31</code>)", text)
        self.assertIn("לא הונפקו מניות (ממוצע משוקלל): ", text)

    def test_altman_falls_back_to_fy_when_a_ttm_input_is_missing(self):
        drop = lambda r: r[0] == "OperatingIncomeLoss" and r[4] == "10-Q"
        res, text = self.run_it(companyfacts(drop, old=True))
        self.assertEqual((res["basis"]["alt"]["kind"], res["basis"]["pio"]["kind"]), ("FY", "TTM"))
        z = 1.2 * (148 - 166) / 359 + 1.4 * -14 / 359 + 3.3 * 133 / 359 + 0.6 * 50 * 14.6 / 285 + 1.0 * 416 / 359
        self.assertAlmostEqual(res["alt"]["z"], z)
        line = next(s for s in text.splitlines() if s.startswith("<b>אלטמן"))
        self.assertTrue(line.endswith("(<code>10-K</code> FY <code>2025-09-27</code>, הוגש <code>2025-10-31</code>)"))

    def test_long_term_debt_zero_rule_does_not_hide_a_lost_input(self):
        drop = lambda r: r[0] == "LongTermDebtNoncurrent" and r[4] == "10-Q"
        res, _ = self.run_it(companyfacts(drop, old=True))  # FY has real debt, TTM would need the "missing = 0" rule
        self.assertEqual(res["basis"]["pio"]["kind"], "FY")
        self.assertNotIn("ltd0", res["notes"])

    def test_no_newer_10q_stays_fy(self):
        res, text = self.run_it(companyfacts(lambda r: r[3] == E, old=True))
        self.assertIsNone(res["ttm"])  # the remaining 10-Qs all end before the last FY end
        drop = lambda r: r[4] == "10-Q"
        res, text = self.run_it(companyfacts(drop, old=True), today=dt.date(2026, 1, 15))
        self.assertIsNone(res["ttm"])
        self.assertEqual({b["kind"] for b in res["basis"].values()}, {"FY"})
        self.assertIn("<b>🧮 ציונים פורנזיים מדוחות <code>10-K</code></b>", text)
        self.assertNotIn("TTM", text)

    def test_10q_after_a_missing_10k_is_not_bridged(self):
        res, text = self.run_it(companyfacts(lambda r: r[5] == "2025-10-31", old=True))  # FY2025 10-K never filed
        self.assertEqual(res["fy"], ["2024-09-28", "2023-09-30"])
        self.assertEqual(res["ttm"]["end"], E1)  # E is 637 days after the last FY end: skipped, E1 is used
        self.assertEqual(res["ttm"]["v"]["ebit"][0], 123 + 101 - 94)
        self.assertEqual(res["basis"]["alt"], {"kind": "TTM", "end": E1, "form": "10-Q", "filed": "2026-07-31"})
        self.assertEqual(res["basis"]["pio"]["kind"], "FY")  # no YTD two years before E1 -> TTM would lose criteria
        self.assertIn("(TTM עד <code>2025-06-28</code>, <code>10-Q</code> הוגש <code>2026-07-31</code>)", text)

    def test_bank_uses_ttm_piotroski_only(self):
        res, text = self.run_it(companyfacts(old=True), sic=6022)
        self.assertTrue(res["fin"] and "alt" not in res)
        self.assertEqual(set(res["basis"]), {"pio"})
        self.assertEqual((res["basis"]["pio"]["kind"], res["newest"], res["stale"]), ("TTM", "2026-07-31", False))
        self.assertIn("אלטמן ובנייש לא חושבו", text)

    def test_derived_ttm_input_is_noted(self):  # gross profit tagged in the 10-K only -> TTM derives it, and says so
        gp = [("GrossProfit", r - c, FY[end][0], end, "10-K", FY[end][1])
              for end, r, c in zip(sorted(FY), ANNUAL["Revenues"], ANNUAL["CostOfRevenue"])]
        res, text = self.run_it(companyfacts(extra=gp, old=True))
        self.assertEqual((res["src"]["gp"], res["ttm"]["src"]["gp"]), ("GrossProfit", "rev-cogs"))
        self.assertEqual(res["basis"]["pio"]["kind"], "TTM")
        self.assertIn("ℹ️ רווח גולמי חושב: הכנסות פחות <code>CostOfRevenue</code>", text)

    def test_cover_page_shares_missing_or_stale(self):
        cf = companyfacts(old=True)
        del cf["facts"]["dei"][F.SHARES]
        res, _ = self.run_it(cf)
        self.assertEqual((res["alt"]["why"], res["alt"]["kind"], res["basis"]["alt"]["kind"]), ("noshares", "Z''", "TTM"))
        self.assertAlmostEqual(res["alt"]["z"], 6.56 * 10 / 383 + 3.26 * 5 / 383 + 6.72 * 154 / 383 + 1.05 * 93 / 290)
        cf["facts"]["dei"][F.SHARES] = {"units": {"shares": [{"end": "2024-01-31", "val": 9, "accn": "x", "form": "10-K",
                                                              "filed": "2024-02-01"}]}}
        res, text = self.run_it(cf)
        self.assertEqual(res["alt"]["why"], "stale")
        self.assertIn("מספר המניות בעמוד השער ישן (<code>2024-01-31</code>)", text)

    def test_chain_helpers(self):
        q = {("2025-01-01", "2025-06-30"): 1, ("2025-04-01", "2025-06-30"): 2, ("2024-01-01", "2024-06-30"): 3,
             ("2024-04-01", "2024-06-30"): 4, ("2023-01-02", "2023-06-30"): 5}
        self.assertEqual(F._chain(q, "2025-06-30", "2024-12-31"),
                         [("2025-01-01", "2025-06-30"), ("2024-01-01", "2024-06-30"), ("2023-01-02", "2023-06-30")])
        self.assertEqual(F._chain(q, "2025-06-30", "2024-09-30"), [])  # nothing starts after that FY end
        self.assertEqual(F._chain({("2025-01-01", "2025-06-30"): 1}, "2025-06-30", "2024-12-31"),
                         [("2025-01-01", "2025-06-30")])


class Freshness(Base):
    def test_stale_flag_after_274_days(self):
        cf = companyfacts(lambda r: r[4] == "10-Q", old=True)  # newest filing used: the 10-K of 2025-10-31
        filed = dt.date(2025, 10, 31)
        res, text = self.run_it(cf, today=filed + dt.timedelta(274))
        self.assertEqual((res["newest"], res["stale"]), ("2025-10-31", False))
        self.assertNotIn("ישנים", text)
        res, text = self.run_it(cf, today=filed + dt.timedelta(275))
        self.assertTrue(res["stale"])
        self.assertIn("⚠️ נתונים פונדמנטליים ישנים (הדוח האחרון הוגש <code>2025-10-31</code>)", text)

    def test_ttm_filing_counts_as_newest(self):
        res, text = self.run_it(companyfacts(old=True), today=dt.date(2027, 4, 1))  # 244 days after the 10-Q
        self.assertEqual((res["newest"], res["stale"]), ("2026-07-31", False))


class BeneishCaveat(Base):
    def test_growth_and_young(self):
        fast = lambda r: r[0] == "Revenues" and r[3] == "2025-09-27"
        extra = [("Revenues", 520, "2024-09-29", "2025-09-27", "10-K", "2025-10-31")]  # SGI 520/391 = 1.33
        res, text = self.run_it(companyfacts(fast, extra, old=True))
        self.assertEqual(res["ben"]["caveat"], {"growth": 520 / 391 - 1, "first": None})
        self.assertIn("⚠️ סיכון גבוה לחיובי־שגוי בבנייש: צמיחת הכנסות של <code>+33%</code> בשנה", text)
        self.assertNotIn("5 שנים", text)
        res, text = self.run_it(companyfacts(fast, extra))  # earliest filing 2023-11-03 -> under 5 years
        self.assertEqual(res["ben"]["caveat"], {"growth": 520 / 391 - 1, "first": "2023"})
        self.assertIn("<code>+33%</code> בשנה · כנראה פחות מ־5 שנים בבורסה", text)
        self.assertIn("<code>2023</code>, הערכה)", text)

    def test_young_only_and_boundaries(self):
        res, text = self.run_it(companyfacts())
        self.assertEqual((res["first_filed"], res["ben"]["caveat"]), ("2023-11-03", {"growth": None, "first": "2023"}))
        self.assertNotIn("צמיחת הכנסות", text)
        res, _ = self.run_it(companyfacts(), today=dt.date(2023, 11, 3) + dt.timedelta(F.YOUNG_DAYS))
        self.assertNotIn("caveat", res["ben"])  # 5 years after the first filing: no longer young
        flat = [("Revenues", 391 * 1.3, "2024-09-29", "2025-09-27", "10-K", "2025-10-31")]
        res, _ = self.run_it(companyfacts(lambda r: r[0] == "Revenues" and r[3] == "2025-09-27", flat, old=True))
        self.assertNotIn("caveat", res["ben"])  # SGI of exactly 1.30 is not "above 30%"


class Formatting(Base):
    def test_basis_he(self):
        self.assertEqual(F._basis_he({"kind": "FY", "end": "2025-09-27", "form": None, "filed": None}),
                         " (<code>10-K</code> FY <code>2025-09-27</code>)")
        self.assertEqual(F._basis_he({"kind": "TTM", "end": "2026-06-27", "form": None, "filed": None}),
                         " (TTM עד <code>2026-06-27</code>)")

    def test_missing_scores_still_show_basis(self):
        keep = ("Revenues", "NetIncomeLoss", "NetCashProvidedByUsedInOperatingActivities")
        res, text = self.run_it(companyfacts(lambda r: r[0] not in keep, old=True), quote=(None, 1.0))
        self.assertNotIn("z", res["alt"])
        self.assertIn("<b>בנייש M:</b> חסר", text)
        self.assertEqual(text.count("(<code>10-K</code> FY <code>2025-09-27</code>, הוגש <code>2025-10-31</code>)"), 2)
        line = next(s for s in text.splitlines() if s.startswith("<b>אלטמן Z:</b> חסר"))
        self.assertTrue(line.endswith(" (<code>10-K</code> FY <code>2025-09-27</code>)"))  # no balance sheet: no date

    def test_zero_denominators(self):
        zero = [("AccountsReceivableNetCurrent", 0, None, "2024-09-28", "10-K", "2025-10-31"),
                ("Liabilities", 0, None, "2025-09-27", "10-K", "2025-10-31")]
        drop = lambda r: r[4] == "10-Q" or (r[0] == "AccountsReceivableNetCurrent" and r[3] == "2024-09-28") or \
            (r[0] == "Liabilities" and r[3] == "2025-09-27")
        res, text = self.run_it(companyfacts(drop, zero, old=True))
        self.assertIn("<b>אלטמן Z:</b> לא ניתן לחישוב (מכנה אפס) (<code>10-K</code> FY", text)
        self.assertIn("<b>בנייש M:</b> לא ניתן לחישוב (מכנה אפס ב־<code>DSRI</code>) (<code>10-K</code> FY", text)

    def test_errors(self):
        for cf, key in ((None, "nofacts"), ({"facts": {"dei": {}}}, "nogaap"),
                        ({"facts": {"us-gaap": {"Assets": {"units": {"USD": []}}}}}, "no10k")):
            res, text = self.run_it(cf)
            self.assertEqual(res["error"], key)
            self.assertIn(F.ERR[key], text)

    def test_long_worst_case_stays_under_budget(self):
        gone = ("OperatingIncomeLoss", "Liabilities", "LongTermDebtNoncurrent", "SellingGeneralAndAdministrativeExpense")
        drop = lambda r: r[0] in gone or (r[0] == "Revenues" and r[3] == "2025-09-27")
        pretax = F.FLOW["ebit"][2]  # the longest fallback tag name
        extra = [(pretax, r[1], *r[2:]) for r in rows() if r[0] == "OperatingIncomeLoss"]
        extra += [("LiabilitiesAndStockholdersEquity", r[1], *r[2:]) for r in rows() if r[0] == "Assets"]
        extra += [(F.INST["eqn"][0], 50, *r[2:]) for r in rows() if r[0] == "Assets"]
        sga = [r for r in rows() if r[0] == "SellingGeneralAndAdministrativeExpense"]
        extra += [(t, r[1] / 2, *r[2:]) for r in sga for t in ("SellingAndMarketingExpense", F.FLOW["ga"][0])]
        extra += [("Revenues", 520, "2024-09-29", "2025-09-27", "10-K", "2025-10-31")]
        res, text = self.run_it(companyfacts(drop, extra), quote=(None, 1.0))  # Z'', every note, both caveats
        self.assertEqual((res["alt"]["kind"], res["alt"]["ebit_tag"]), ("Z''", pretax))
        self.assertEqual(sorted(res["notes"]), ["gp", "ltd0", "sga", "tl"])
        self.assertEqual(set(res["ben"]["caveat"]), {"growth", "first"})
        self.assertEqual(res["basis"]["pio"]["kind"], "TTM")
        self.assertNotIn(F.TTM_LINE, text)  # over budget: the optional TTM summary line is the one dropped
        with mock.patch.object(F, "BUDGET", 10 ** 6):
            self.assertIn(F.TTM_LINE, "\n".join(F.format_he(res)))


class Quote(unittest.TestCase):
    def test_price_and_split(self):
        since = "2026-07-17"
        p1 = int(dt.datetime(2026, 7, 18, tzinfo=dt.timezone.utc).timestamp())
        body = {"chart": {"result": [{"meta": {"currency": "USD", "regularMarketPrice": 12.5},
                                      "events": {"splits": {"a": {"date": p1 + 86400, "numerator": 1, "denominator": 30},
                                                            "b": {"date": p1 - 86400, "numerator": 2, "denominator": 1}}}}]}}
        calls = []

        def fetch(url, **k):
            calls.append(url)
            if "query1" in url:
                raise OSError("down")
            return json.dumps(body).encode()
        with mock.patch.object(common, "fetch", side_effect=fetch), mock.patch("builtins.print"):
            px, f = F.quote("ACME", since)
        self.assertEqual((px, len(calls)), (12.5, 2))  # query1 failed, query2 answered
        self.assertAlmostEqual(f, 1 / 30)  # only the split after the cover-page date counts
        body["chart"]["result"][0]["meta"]["currency"] = "CAD"
        with mock.patch.object(common, "fetch", return_value=json.dumps(body).encode()):
            self.assertEqual(F.quote("ACME", since), (None, 1.0))  # a non-USD quote is never used


if __name__ == "__main__":
    unittest.main()
