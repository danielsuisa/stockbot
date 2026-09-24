"""Offline tests for the ranked Item 1A diff (bot/tenk.py): hedge -> firm shifts, risk keywords that appear or
vanish, the boilerplate filter and the trimmed before/after quotes. Stdlib unittest, no network:
python -m unittest discover -s tests"""
import unittest
from unittest import mock

from bot import common, tenk

WORDS = ("supply weather credit labor pricing currency software regulatory privacy climate energy shipping insurance "
         "pension patent brand retail wholesale lending hiring leasing tax audit freight semiconductor fraud cyber "
         "outsourcing logistics warranty recall inflation interest housing travel advertising licensing mining farming "
         "banking").split()
FILL = [f"Our {w} exposure could adversely affect results of operations, margins and the overall financial "
        f"condition of the {w} segment." for w in WORDS]
USG = "We sell to the U.S. Government and other public agencies under contracts that can be terminated at any time."
DEBT = ("As of {}, our total outstanding indebtedness was approximately {}, including amounts under our revolving "
        "facility.")
GC = "There is substantial doubt about our ability to continue as a going concern within one year after issuance."
COV = [f"If we breach the {x} covenants in our loan agreements, the lenders could accelerate repayment of all amounts "
       f"outstanding." for x in ("leverage", "coverage", "reporting")]
DELIST = ("Our common stock could be delisted from Nasdaq if we fail to regain compliance with the minimum bid price "
          "requirement.")
LIQ_OLD = "Tight liquidity in the markets for our loans and leases could raise the cost of the funding that we need."
LIQ_NEW = "Rising interest rates have reduced liquidity in the markets for our receivables and raised funding costs."
PRI = FILL + [USG, DEBT.format("December 31, 2024", "$1.2 billion"), GC, *COV, LIQ_OLD]
CUR = [FILL[0].replace("could adversely affect", "has adversely affected"),  # hedge -> firm: 'could→has'
       FILL[1].replace("operations,", "operations, liquidity,"),  # a keyword appears inside an edited sentence
       FILL[2].replace("could", "has, and could again,"),  # firm words added before a kept hedge: '+has'
       "Labor exposure could adversely affect our results of operations, our margins and the overall financial "
       "health of this labor segment.",  # substantive rewording without any signal: neither listed nor boilerplate
       FILL[4].replace("margins", "profit margins"),  # cosmetic (similarity >= 0.95): boilerplate
       *FILL[5:], USG, DEBT.format("September 30, 2025", "$950.4 million"),  # only a date/amount moved: boilerplate
       DELIST, LIQ_NEW]


def doc(sents):
    body = "".join(f"<p>{s}</p>" for s in sents)
    return (f"<html><body><p>Item 1A. Risk Factors</p>{body}<p>Item 1B. Unresolved Staff Comments</p><p>None.</p>"
            "</body></html>").encode()


ROWS = [{"form": "10-K", "primaryDocument": d, "accessionNumber": "0-0-" + d, "filingDate": f}
        for d, f in (("cur", "2026-02-01"), ("pri", "2025-02-01"))]


def run(cur, pri, rows=ROWS):
    docs = {"cur": cur if isinstance(cur, bytes) else doc(cur), "pri": pri if isinstance(pri, bytes) else doc(pri)}
    with mock.patch.object(common, "fetch", side_effect=lambda url: docs[url.rsplit("/", 1)[1]]), \
            mock.patch("builtins.print"):
        return tenk.analyze(1, rows)


def labels(s):
    return list(tenk._labels(s))


def pair(before, after):
    return tenk._pair(before, after, tenk._labels(before), tenk._labels(after), set())


class Keywords(unittest.TestCase):
    def test_primary_labels(self):
        cases = {
            "There is substantial doubt about our ability to continue as a going concern.": ["going concern"],
            "Our auditors included a going-concern paragraph in their report.": ["going concern"],
            "We identified material weaknesses in our internal control over reporting.": ["material weakness"],
            "We restated our financial statements for fiscal 2024.": ["restatement"],
            "The restatement of prior periods was time-consuming.": ["restatement"],
            "Our shares may be delisted from the Nasdaq Global Market.": ["delisting"],
            "We received a notice that we do not meet the minimum bid price requirement.": ["delisting"],
            "We may not satisfy the NYSE continued listing standards.": ["delisting"],
            "We received a subpoena from the Department of Justice.": ["SEC inquiry/subpoena"],
            "The SEC’s Division of Enforcement is investigating our revenue recognition.": ["SEC inquiry/subpoena"],
            "We responded to an inquiry from the staff of the Securities and Exchange Commission.":
                ["SEC inquiry/subpoena"],
            "We received a Wells notice last year.": ["SEC inquiry/subpoena"],
            "Our credit agreement contains financial covenants.": ["covenant"],
            "A limited number of customers account for a significant portion of our revenue.":
                ["customer concentration"],
            "Our largest customer accounted for 22% of net sales.": ["customer concentration"],
            "We depend on a small number of customers for most of our sales.": ["customer concentration"],
            "Customer concentration exposes us to credit risk.": ["customer concentration"],
            "Our liquidity depends on access to capital markets.": ["liquidity"],
        }
        for s, want in cases.items():
            self.assertEqual(labels(s), want, s)

    def test_boilerplate_is_not_a_signal(self):
        for s in ("Our Amended and Restated Certificate of Incorporation contains anti-takeover provisions.",
                  "We entered into an amended and restated credit agreement.",
                  "The principal investigator reported adverse events in the trial.",
                  "Encryption is enabled by default on all devices.",
                  "We file annual and quarterly reports with the SEC.",
                  "No customer accounted for more than 10% of revenue.",
                  "Our customers expect timely delivery of listing data."):
            self.assertEqual(labels(s), [], s)

    def test_sec_absorbs_investigation_and_order(self):
        found = tenk._labels("The SEC is investigating whether our litigation reserves and liquidity were adequate.")
        self.assertEqual(list(found), ["SEC inquiry/subpoena", "liquidity", "litigation"])  # heaviest first
        self.assertEqual(labels("A state investigation could lead to litigation."), ["investigation", "litigation"])
        s = "Tariffs raised costs."
        self.assertEqual(tenk._labels(s)["tariff"], (0, 6))  # span of the first match, for centring the quote

    def test_scores(self):
        self.assertEqual(tenk._kw_score([], set()), 0)
        self.assertEqual(tenk._kw_score(["covenant", "tariff"], {"covenant", "tariff"}), 5.5)  # heaviest + half
        self.assertEqual(tenk._kw_score(["going concern"], set()), 15)  # new to the other year's section: x1.5


class Hedges(unittest.TestCase):
    def test_replaced_hedge(self):
        c = pair("We may be subject to penalties from regulators, which could harm our reputation and results.",
                 "We are subject to penalties from regulators, which could harm our reputation and results.")
        self.assertEqual((c["kind"], c["labels"], c["score"]), ("hedge", ["may→are"], tenk.W_HEDGE))
        self.assertEqual(c["before"][slice(*c["sb"])], "may be")
        self.assertEqual(c["after"][slice(*c["sa"])], "are")

    def test_added_before_kept_hedge(self):
        c = pair("Adverse publicity could make it more difficult for us to attract new customers and partners.",
                 "Adverse publicity has in the past and could in the future make it more difficult for us to "
                 "attract new customers and partners.")
        self.assertEqual((c["kind"], c["labels"], c["score"]), ("hedge", ["+has in the past"], tenk.W_ADDED))
        self.assertEqual(c["after"][slice(*c["sa"])], "has in the past and")

    def test_firmed_up_risk_keeps_keyword_context(self):
        c = pair("We may breach the financial covenants in our credit facility next year.",
                 "We have breached the financial covenants in our credit facility this year.")
        self.assertEqual(c["labels"], ["may→have"])
        self.assertEqual(c["score"], tenk.W_HEDGE + tenk._W["covenant"] / 2)  # hedge + half the kept keyword

    def test_not_a_shift(self):
        same = "Changes in trade policy could harm our results of operations and our financial condition."
        for after in (same.replace("could harm", "could have harmed"), same.replace("could", "may"),
                      same.replace("could harm", "could significantly harm").replace("and our", "and")):
            self.assertIsNone(pair(same, after), after)  # hedge kept, hedge -> hedge, or no firm word
        self.assertIsNone(pair("Our results are subject to seasonal swings in consumer demand across regions.",
                               "Our results may be subject to seasonal swings in consumer demand across regions."))
        long_ = pair("We may, depending on market conditions and many other factors, decide to sell the business.",
                     "We sold the business in the fourth quarter after a strategic review that lasted two years ago "
                     "is completed, decide to sell the business.")
        self.assertIsNone(long_)  # a whole clause rewritten is not a 'may -> is' hedge shift

    def test_month_may(self):
        t = "In May 2024 we may sell assets and Beginning in May, the FDA That May Result"
        ws = tenk._words(t)
        self.assertEqual([k for k in range(len(ws)) if tenk._is_hedge(t, ws, k)], [4, 14])


class Snip(unittest.TestCase):
    T = ("Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau upsilon "
         "phi chi psi omega and so on until the end of this rather long sentence.")

    def test_centred_and_cut_at_words(self):
        i = self.T.index("upsilon")
        s = tenk._snip(self.T, (i, i + 7), 60)
        self.assertTrue(s.startswith("…") and s.endswith("…") and "upsilon" in s and len(s) <= 62, s)
        self.assertTrue(all(w in self.T.split() for w in s.strip("…").split()), s)  # no word cut in half
        self.assertEqual(tenk._snip(self.T, (0, 5), 500), self.T)
        self.assertEqual(tenk._snip(self.T, (0, 5), 40), "Alpha beta gamma delta epsilon zeta eta…")
        end = len(self.T)
        self.assertEqual(tenk._snip(self.T, (end - 9, end), 40), "…the end of this rather long sentence.")
        self.assertEqual(tenk._snip(self.T, (6, 150), 40), "…beta gamma delta epsilon zeta eta theta…")  # wide change
        self.assertEqual(tenk._snip("a " + "x" * 80 + " tail words", (3, 5), 30), "a " + "x" * 28 + "…")


class Ranked(unittest.TestCase):
    def test_end_to_end(self):
        r = run(CUR, PRI)
        self.assertEqual((r["n_cur"], r["n_pri"], r["same"], r["edited"], r["boiler"], r["new"], r["removed"]),
                         (44, 47, 36, 6, 2, 2, 5))
        self.assertEqual([(c["kind"], c["labels"], c["more"]) for c in r["top"]],
                         [("removed", ["going concern"], 0), ("removed", ["covenant"], 2), ("new", ["delisting"], 0),
                          ("hedge", ["could→has"], 0), ("hedge", ["+has"], 0)])
        self.assertEqual([c["score"] for c in r["top"]], [22.5, 11.25, 10.5, 8, 6])
        with mock.patch.object(tenk, "TOP", 10):  # removal outranks the addition of the same keyword
            rest = [(c["kind"], c["labels"]) for c in run(CUR, PRI)["top"][5:]]
        self.assertEqual(rest, [("removed", ["liquidity"]), ("kw", ["liquidity"]), ("new", ["liquidity"])])

        lines = tenk.format_he(r)
        text = "\n".join(lines)
        self.assertEqual(common.rtl_bad_lines(text), [])
        self.assertLessEqual(common.visible(text), tenk.BUDGET)
        self.assertIn("נערכו קלות: <code>6</code> (מתוכם <code>2</code> שגרתיים", text)
        self.assertIn("Lazy Prices", text)
        at = {x: i for i, x in enumerate(lines)}
        i = at["• הוסר (<code>going concern</code>):"]
        self.assertTrue(lines[i + 1].startswith("לפני: <i>There is substantial doubt"))
        self.assertTrue(lines[i + 2].startswith("• הוסר (<code>covenant</code>) ועוד <code>2</code> כאלה:"))
        i = at["• חדש (<code>delisting</code>):"]
        self.assertTrue(lines[i + 1].startswith("אחרי: <i>Our common stock could be delisted"))
        i = at["• נוסח הוקשח (<code>could→has</code>):"]
        self.assertTrue(lines[i + 1].startswith("לפני: ") and "could adversely affect" in lines[i + 1])
        self.assertTrue(lines[i + 2].startswith("אחרי: ") and "has adversely affected" in lines[i + 2])
        self.assertIn("• נוסח הוקשח (<code>+has</code>):", at)
        for line in lines:
            if line.startswith(("לפני:", "אחרי:")):
                self.assertLessEqual(common.visible(line), 6 + tenk.SNIP + 2)

    def test_boilerplate_rules(self):
        m, ratio = tenk._close(" ".join(CUR[-3].lower().split()), [(p, set(p.split())) for p in
                                                                    [" ".join(PRI[-6].lower().split())]])
        self.assertTrue(m and 0.85 <= ratio < 0.95)  # the date/amount pair counts via the masked comparison
        self.assertEqual(tenk._mask("as of december 31, 2024, we had $1.2 billion and one plant."),
                         tenk._mask("as of may 5, 2026, we had $950 million and two plant."))

    def test_budget_shrinks_quotes_then_drops_changes(self):
        long_ = " ".join(["filler words"] * 200)
        c = {"kind": "hedge", "labels": ["may→has", "covenant", "liquidity"], "more": 3, "score": 9,
             "before": long_, "after": long_, "sb": (1000, 1003), "sa": (1000, 1003)}
        res = {**run(CUR, PRI), "top": [c] * 5}
        text = "\n".join(tenk.format_he(res))
        self.assertLessEqual(common.visible(text), tenk.BUDGET)
        self.assertIn("(<code>may→has, covenant +1</code>) ועוד <code>3</code> כאלה:", text)
        self.assertEqual(text.count("• נוסח הוקשח"), 5)
        with mock.patch.object(tenk, "BUDGET", 900):
            text = "\n".join(tenk.format_he(res))
        self.assertTrue(1 <= text.count("• נוסח הוקשח") < 5)
        self.assertLessEqual(common.visible(text), 900)

    def test_nothing_material(self):
        cur = [FILL[0].replace("margins", "operating margins"), *FILL[1:]]
        r = run(cur, FILL)
        self.assertEqual((r["edited"], r["boiler"], r["top"]), (1, 1, []))
        text = "\n".join(tenk.format_he(r))
        self.assertIn("לא נמצאו שינויים מהותיים", text)
        self.assertEqual(common.rtl_bad_lines(text), [])
        text = "\n".join(tenk.format_he(run(FILL, FILL)))  # identical sections: metrics only
        self.assertIn("<code>100%</code>", text)
        self.assertNotIn("שינויים מהותיים", text)


class Problems(unittest.TestCase):
    def fmt(self, res):
        text = "\n".join(tenk.format_he(res))
        self.assertEqual(common.rtl_bad_lines(text), [])
        return text

    def test_count(self):
        with mock.patch.object(common, "submissions", return_value={}):
            r = tenk.analyze(1, ROWS[:1])
        self.assertEqual(r, {"dates": ["2026-02-01"], "problem": "count"})
        self.assertIn("נמצא רק דוח", self.fmt(r))
        self.assertIn("לא נמצאו דוחות שנתיים", self.fmt(tenk.analyze(1, [])))

    def test_prior_10k_on_an_older_page(self):
        sub = {"filings": {"files": [{"name": "far.json", "filingFrom": "2010-01-01", "filingTo": "2012-01-01"},
                                     {"name": "near.json", "filingFrom": "2024-06-01", "filingTo": "2025-06-01"}]}}
        page = {"form": ["8-K", "10-K"], "primaryDocument": ["x.htm", "pri"], "accessionNumber": ["0-1", "0-0-pri"],
                "filingDate": ["2025-03-01", "2025-02-01"]}
        with mock.patch.object(common, "submissions", return_value=sub), \
                mock.patch.object(common, "get_json", return_value=page) as get:
            ks = tenk._tenks(1, ROWS[:1])
        self.assertEqual([k["filingDate"] for k in ks], ["2026-02-01", "2025-02-01"])
        self.assertEqual(get.call_args_list, [mock.call("https://data.sec.gov/submissions/near.json")])

    def test_short_or_missing_sections(self):
        why = {"src": "As a smaller reporting company we are not required to provide this information.",
               "ref": "The information is incorporated herein by reference to Exhibit 13.",
               "short": "Investing in our stock involves risk."}
        for p, s in why.items():
            r = run(doc([s]), CUR)
            self.assertEqual((r["problem"], r["which"]), (p, "2026-02-01"))
            self.assertIn("<code>2026-02-01</code>", self.fmt(r))
        r = run(CUR, b"<html><body><p>Item 7. MD&A</p></body></html>")
        self.assertEqual((r["problem"], r["which"]), ("missing", "2025-02-01"))
        self.assertIn("לא אותר", self.fmt(r))


class Parsing(unittest.TestCase):
    def test_plain_text_10k(self):
        raw = b"ITEM 1A. RISK FACTORS\nOur business is\nhard-wrapped at seventy columns.\n\nNext paragraph here.\n"
        self.assertEqual(tenk.to_text(raw),
                         "ITEM 1A. RISK FACTORS Our business is hard-wrapped at seventy columns.\nNext paragraph here.")

    def test_annual_report_style_section(self):
        body = "\n".join(FILL[:20])
        text = (f"Table of Contents\nRisk Factors 24\nLegal Proceedings 40\nOverview\nRisk Factors\n{body}\n"
                f"Legal Proceedings\nNone.")
        sec = tenk.section(text)
        self.assertTrue(sec.startswith("Risk Factors\nOur supply") and sec.rstrip().endswith(FILL[19]), sec[-80:])
        self.assertNotIn("Legal Proceedings", sec)
        self.assertEqual(len(tenk.sentences(sec)), 20)


if __name__ == "__main__":
    unittest.main()
