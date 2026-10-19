"""The stockbot insider rule at 2ffda0a, reimplemented (ADR 0009 section 3). The vectors are copied from stockbot's
tests at 2ffda0a (tests/test_signals.py, tests/test_review.py, tests/test_bot.py); test names keep the originals."""
import datetime as dt
import unittest

from scalp_engine import insider_rule as R

DIR = [("100", "DOE JOHN", "Director", "")]


def tx(code, shares, px, date="2026-09-21", ad="A", own="D", post=10000.0, notes=()):
    return {"trans_date": dt.date.fromisoformat(date), "trans_code": code, "acquired_disposed": ad, "shares": shares,
            "price": px, "shares_after": post, "direct_indirect": own, "nature": "", "fn_ids": list(notes)}


def doc(txs, owners=DIR, box=False, notes=None, form="4", original=None, issuer="12345"):
    return R.make_doc(form, issuer, owners, txs, box="true" if box else "false", original=original,
                      notes=[f"{k}\x1f{v}" for k, v in (notes or {}).items()])


def row(acc, who, value, od=True, kind="open", cik=1, sig=None, indirect=False, weight=1.0):
    return {"acc": acc, "cik": cik, "insider": who, "insider_cik": who, "od": od, "weight": weight, "first": "2026-09-20",
            "last": "2026-09-20", "shares": 1, "value": value, "price": 10.0, "filed": "2026-09-21", "kind": kind,
            "sig": sig, "indirect": indirect}


def make(acc, d, filed="2026-09-21"):
    return R.make_row(acc, d, d["buys"], filed)


class Classification(unittest.TestCase):
    def test_kinds_and_plans(self):
        d = doc([tx("P", 1000, 10), tx("P", 500, 12, notes=["F1"]), tx("M", 300, 5), tx("A", 200, 0),
                 tx("S", 100, 13, ad="D")],
                notes={"F1": "Purchased pursuant to a Rule 10b5-1 trading plan adopted May 5."})
        self.assertEqual([b["plan"] for b in d["buys"]], [False, True])
        s = R.summarize(d)
        self.assertEqual((s["kind"], s["value"], s["shares"]), ("open", 10000.0, 1000.0))

    def test_checkbox_without_footnote_marks_all_purchases_plan(self):
        d = doc([tx("P", 1000, 10)], box=True)
        self.assertTrue(d["buys"][0]["plan"])
        self.assertEqual(R.summarize(d)["kind"], "plan")
        d = doc([tx("P", 1000, 10), tx("S", 5, 1, ad="D", notes=["F1"])], box=True, notes={"F1": "Rule 10b5-1 plan sale"})
        self.assertFalse(d["buys"][0]["plan"])  # the plan footnote covers the sale only

    def test_not_plan_footnote_denies(self):
        d = doc([tx("P", 1000, 10, notes=["F1"])], notes={"F1": "This purchase was not made pursuant to a Rule 10b5-1 plan."})
        self.assertFalse(d["buys"][0]["plan"])

    def test_role_weights(self):
        w = lambda rel, title="": R.make_doc("4", "1", [("1", "X", rel, title)], [])["owners"][0]["weight"]
        self.assertEqual(w("Officer", "Chief Executive Officer"), 1.5)
        self.assertEqual(w("Officer", "EVP & CFO"), 1.5)
        self.assertEqual(w("Officer", "SVP Sales"), 1.0)
        self.assertEqual(w("Director"), 0.7)
        self.assertEqual(w("TenPercentOwner"), 0.0)


class Clusters(unittest.TestCase):
    def test_three_insiders_open_market_only(self):
        rs = [row("a", "x", 60000), row("b", "y", 50000)]
        self.assertFalse(R.evaluate(rs)["cluster"])
        rs.append(row("c", "z", 40000, kind="plan"))
        self.assertFalse(R.evaluate(rs)["cluster"])
        rs.append(row("d", "w", 40000))
        self.assertEqual(R.evaluate(rs)["cluster"], (3, 150000))
        self.assertFalse(R.evaluate([row("p", "q", 900000, kind="plan")])["big"])

    def test_cluster_needs_100k_and_officers_or_directors(self):
        self.assertFalse(R.evaluate([row("a", "x", 30000), row("b", "y", 30000), row("c", "z", 30000)])["cluster"])
        rs = [row("a", "x", 60000), row("b", "y", 50000), row("c", "fund", 400000, od=False)]
        ev = R.evaluate(rs)
        self.assertFalse(ev["cluster"])  # a 10% owner never counts toward the cluster
        self.assertEqual(R.evaluate(rs + [row("d", "w", 1)])["cluster"], (3, 110001))

    def test_etra_regression_20m_not_40m(self):
        lines = [tx("P", 333333, 15, own="I"), tx("P", 1000000, 15, own="I")]
        fund = doc(lines, owners=[("1", "Orbimed Advisors Llc", "TenPercentOwner", "")])
        gordon = doc(lines, owners=[("2", "GORDON CARL L", "Director", "")])
        rows = [make("880", fund, "2026-09-23"), make("881", gordon, "2026-09-23")]
        ev = R.evaluate(rows)
        self.assertEqual(len(ev["uniq"]), 1)
        self.assertAlmostEqual(ev["uniq"][0]["value"], 1333333 * 15)  # $20.0M, not $40.0M
        self.assertFalse(ev["cluster"])
        self.assertEqual(sorted(R.accs_of(ev["uniq"][0])), ["880", "881"])
        self.assertTrue(ev["uniq"][0]["od"])  # the director's identity wins over the fund's

    def test_multi_transaction_filing_is_one_row(self):
        d = doc([tx("P", 100, 10), tx("P", 200, 11), tx("P", 300, 12)])
        r = make("1", d)
        self.assertEqual((r["shares"], r["value"]), (600.0, 100 * 10 + 200 * 11 + 300 * 12))

    def test_amendment_without_purchases_removes_row_and_unmatched_is_new(self):
        buys = {"o": make("o", doc([tx("P", 100, 10)]))}
        gone = doc([tx("S", 100, 10, ad="D")], form="4/A", original="2026-09-21")
        self.assertEqual(R.amend(buys, "a", gone, None), "o")
        self.assertEqual(buys, {})
        self.assertIsNone(R.amend({}, "a", gone, None))


class Amendments(unittest.TestCase):
    def test_same_day_filings_of_one_insider(self):
        a, b = doc([tx("P", 100, 10, date="2026-09-17")]), doc([tx("P", 10000, 10, date="2026-09-18")])
        buys = {"A": make("A", a), "B": make("B", b)}
        fix = doc([tx("P", 10000, 11, date="2026-09-18")], form="4/A", original="2026-09-21")
        self.assertEqual(R.amend(buys, "F", fix, make("F", fix)), "B")
        self.assertEqual((buys["A"]["value"], buys["B"]["value"], buys["B"]["amended_by"]), (1000.0, 110000.0, "F"))

    def test_unrelated_or_partial_amendments_withdraw_nothing(self):
        buys = {"A": make("A", doc([tx("P", 100, 10, date="2026-09-17")]))}
        sale = doc([tx("S", 50, 12, date="2026-09-19", ad="D")], form="4/A", original="2026-09-21")
        grant_only = doc([], form="4/A", original="2026-09-21")
        removed = {}
        for d in (sale, grant_only):
            self.assertIsNone(R.amend(buys, "X", d, None, removed))
        self.assertEqual((list(buys), removed), (["A"], {}))
        recoded = doc([tx("A", 100, 0, date="2026-09-17")], form="4/A", original="2026-09-21")
        self.assertEqual(R.amend(buys, "R", recoded, None, removed), "A")
        self.assertEqual((buys, removed["A"]["by"]), ({}, "R"))

    def test_withdrawn_original_is_never_read_back(self):
        st = R.Issuer(12345)
        st.removed = {"o": {"by": "w", "cik": 12345, "date": "2026-09-21"}}
        orig = doc([tx("P", 1000, 1000)], owners=[("7", "BIG", "Director", "")])
        self.assertEqual(st.apply("o", orig, "2026-09-21", dt.date(2026, 9, 24)), "skipped")
        self.assertEqual(st.buys, {})

    def test_original_read_after_its_amendment_is_not_added(self):
        big = [("7", "BIG", "Director", "")]
        fix = doc([tx("P", 1000, 900)], owners=big, form="4/A", original="2026-09-18")
        st = R.Issuer(12345)
        st.buys = {"F": make("F", fix, filed="2026-09-22")}
        orig = doc([tx("P", 1000, 1000)], owners=big)
        st.apply("O", orig, "2026-09-18", dt.date(2026, 9, 24))
        self.assertEqual(list(st.buys), ["F"])  # $900K once, not $1.9M


class Signals(unittest.TestCase):
    def test_joint_merge_is_order_independent(self):
        fund = {**row("f", "fund", 1e6, od=False, weight=0.0), "sig": [["2026-09-20", 1, 1]], "indirect": True}
        director = {**row("g", "dir", 1e6, weight=0.7), "sig": [["2026-09-20", 1, 1]], "indirect": True}
        others = [row("h", "x", 60000), row("i", "y", 50000)]
        for order in ([fund, director], [director, fund]):
            ev = R.evaluate(order + others)
            self.assertEqual(ev["cluster"], (3, 1110000.0))

    def test_joint_reports_count_once(self):
        fund = {"insider": "OrbiMed", "sig": [["2026-09-21", 1e6, 15.0]], "indirect": True, "value": 15e6}
        out = R.joint([fund, dict(fund, insider="Gordon")])
        self.assertEqual([(x["insider"], x["value"]) for x in out], [("OrbiMed / Gordon", 15e6)])
        direct = dict(fund, indirect=False)
        self.assertEqual(len(R.joint([direct, dict(direct, insider="Twin")])), 2)
        self.assertEqual(len(R.joint([{"insider": "a"}, {"insider": "b"}])), 2)


class Clock(unittest.TestCase):
    def test_window_prune_and_events(self):
        st = R.Issuer(1)
        d0 = dt.date(2026, 9, 1)
        people = [[(str(i), f"P{i}", "Director", "")] for i in range(4)]
        st.apply("a", doc([tx("P", 4000, 10, date="2026-09-01")], owners=people[0]), "2026-09-01", d0)
        st.apply("b", doc([tx("P", 4000, 10, date="2026-09-02")], owners=people[1]), "2026-09-02", d0 + dt.timedelta(1))
        self.assertEqual(st.events("b", st.evaluate()), [])
        st.apply("c", doc([tx("P", 4000, 10, date="2026-09-03")], owners=people[2]), "2026-09-03", d0 + dt.timedelta(2))
        ev = st.evaluate()
        self.assertEqual(st.events("c", ev), [("cluster", ["a", "b", "c"], 3, 120000.0)])
        snap = st.live_alert(ev)
        self.assertEqual(snap["kind"], "cluster")
        self.assertIsNone(st.live_alert(ev))  # nothing new: no second alert
        st.apply("d", doc([tx("P", 4000, 10, date="2026-10-02")], owners=people[3]), "2026-10-02", dt.date(2026, 10, 2))
        ev = st.evaluate()  # 'a' (last 2026-09-01) aged out on 2026-10-02 (since = 2026-09-02)
        self.assertEqual(sorted(st.buys), ["b", "c", "d"])
        self.assertEqual(st.events("d", ev)[0][:3], ("cluster", ["b", "c", "d"], 3))

    def test_big_event_is_the_big_filing(self):
        st = R.Issuer(1)
        st.apply("a", doc([tx("P", 60000, 10)]), "2026-09-21", dt.date(2026, 9, 21))
        self.assertEqual(st.events("a", st.evaluate()), [("big", ["a"], None, 600000.0)])
        st.apply("b", doc([tx("P", 100, 10)], owners=[("200", "X", "Officer", "")]), "2026-09-22", dt.date(2026, 9, 22))
        self.assertEqual(st.events("b", st.evaluate()), [])  # an older big purchase does not make b an event
        self.assertEqual(R.RULE_VERSION, "stockbot-2ffda0a/3-100000-30-500000")


if __name__ == "__main__":
    unittest.main()
