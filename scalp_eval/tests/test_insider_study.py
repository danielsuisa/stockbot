"""I2 evaluator golden set (ADR 0010; I2 prompt Stage 2): hand-computed cases the evaluator must reproduce exactly.
H = 3 throughout unless stated: day 1 = session i1, day H = i1 + 2."""
import datetime as dt
import random
import unittest

from scalp_eval import insider_study as S

DAYS = []
_d = dt.date(2024, 1, 2)
while len(DAYS) < 40:
    if _d.weekday() < 5:
        DAYS.append(_d)
    _d += dt.timedelta(days=1)


def bars(spec):
    """{i: (open, close)} -> Market bar tuple."""
    ii = sorted(spec)
    return (ii, [float(spec[i][0]) for i in ii], [float(spec[i][1]) for i in ii])


def market(sym_bars, **kw):
    return S.Market(DAYS, {s: bars(b) for s, b in sym_bars.items()}, **kw)


class Returns(unittest.TestCase):
    def test_g01_plain_close(self):
        m = market({"A": {1: (10, 10), 2: (10, 11), 3: (11, 12), 9: (12, 12)}})
        r = m.total_return("A", 1, 3)
        self.assertEqual((r["case"], r["i1"], r["iH"]), ("close", 1, 3))
        self.assertAlmostEqual(r["r"], 0.2, places=12)

    def test_g02_split(self):  # 2:1 forward split ex on day 2: 2 shares x 6 = 12 vs 10
        m = market({"A": {1: (10, 10), 2: (5, 5), 3: (6, 6), 9: (6, 6)}}, splits={"A": [(DAYS[2], 2.0)]})
        self.assertAlmostEqual(m.total_return("A", 1, 3)["r"], 0.2, places=12)

    def test_g03_dividend_reinvested(self):  # 0.50 ex day 2, close 10: 1.05 shares x 11 = 11.55 vs 10
        m = market({"A": {1: (10, 10), 2: (10, 10), 3: (11, 11), 9: (11, 11)}}, divs={"A": [(DAYS[2], 0.5)]})
        self.assertAlmostEqual(m.total_return("A", 1, 3)["r"], 0.155, places=12)

    def test_g04_cash_merger(self):  # no day-3 bar; cash merger 15 effective day 3
        m = market({"A": {1: (10, 10), 2: (10, 11)}}, mergers={"A": [(DAYS[3], "cash_mergers", 0.0, 15.0, None)]})
        r = m.total_return("A", 1, 3)
        self.assertEqual(r["case"], "merger_cash")
        self.assertAlmostEqual(r["r"], 0.5, places=12)

    def test_g05_stock_merger_with_acquirer(self):  # 0.5 acquirer shares x 40 = 20 vs 10
        m = market({"A": {1: (10, 10), 2: (10, 11)}, "B": {3: (39, 40), 9: (40, 40)}},
                   mergers={"A": [(DAYS[3], "stock_mergers", 0.5, 0.0, "B")]})
        r = m.total_return("A", 1, 3)
        self.assertEqual(r["case"], "merger_stock")
        self.assertAlmostEqual(r["r"], 1.0, places=12)

    def test_g06_stock_merger_without_acquirer(self):  # acquirer has no bar: the target's last close 11
        m = market({"A": {1: (10, 10), 2: (10, 11)}}, mergers={"A": [(DAYS[3], "stock_mergers", 0.5, 0.0, "ZZZ")]})
        r = m.total_return("A", 1, 3)
        self.assertEqual(r["case"], "merger_no_acquirer")
        self.assertAlmostEqual(r["r"], 0.1, places=12)

    def test_g07_other_delisting(self):  # last close 11 x 0.70 = 7.70 vs 10
        m = market({"A": {1: (10, 10), 2: (10, 11)}, "SPY": {i: (1, 1) for i in range(40)}})
        r = m.total_return("A", 1, 3)
        self.assertEqual(r["case"], "delisting")
        self.assertAlmostEqual(r["r"], -0.23, places=12)

    def test_g08_temporary_gap(self):  # no day-3 bar, first close after day H = 13
        m = market({"A": {1: (10, 10), 2: (10, 11), 4: (12, 13), 9: (13, 13)}})
        r = m.total_return("A", 1, 3)
        self.assertEqual((r["case"], r["exit_i"]), ("gap", 4))
        self.assertAlmostEqual(r["r"], 0.3, places=12)

    def test_g09_missing_entry_shift(self):  # no bar on day 1 (i=1): first open i=3, day H shifts to i=5
        m = market({"A": {3: (10, 10), 4: (10, 11), 5: (11, 12), 9: (12, 12)}})
        r = m.total_return("A", 1, 3)
        self.assertEqual((r["i1"], r["iH"]), (3, 5))
        self.assertAlmostEqual(r["r"], 0.2, places=12)

    def test_g10_missing_entry_drop(self):  # nothing in day 1 .. day 1 + 5
        m = market({"A": {7: (10, 10), 9: (10, 10)}})
        self.assertEqual(m.total_return("A", 1, 3), {"drop": "no_entry_open"})
        self.assertIsNone(m.entry("A", 1, shift=False))


class Controls(unittest.TestCase):
    def cands(self, specs):
        return [{"symbol": s, "cik": f"c{s}", "sic2": sic, "dv_decile": 4, "universe_b": True} for s, sic in specs]

    def event(self):
        return {"event_id": "cluster-1-20240103T120000Z-x", "symbol": "EVT", "cik": "c0", "entry_date": DAYS[1],
                "sic2": "36", "dv_decile": 4}

    def test_g11_control_redraw(self):
        names = ["A", "B", "C", "D", "E", "F"]
        m = market({s: {1: (10, 10)} for s in names if s != "C"})  # C has no open on day 1
        got, level, skipped = S.draw_controls(self.event(), self.cands([(s, "36") for s in names]), m, 1, {}, {})
        perm = sorted(names)
        random.Random(S.seed_of(f"{self.event()['event_id']}|{S.CONTROL_TAG}")).shuffle(perm)
        expect = [s for s in perm if s != "C"][:5]
        self.assertEqual((got, level), (expect, "sic2"))
        self.assertEqual(skipped, 0 if perm[-1] == "C" else 1)

    def test_g12_sic1_widening(self):  # two SIC 36 candidates, four SIC 35: widen to SIC 3, five taken
        specs = [("A", "36"), ("B", "36"), ("C", "35"), ("D", "35"), ("E", "35"), ("F", "35"), ("G", "28")]
        m = market({s: {1: (10, 10)} for s, _ in specs})
        got, level, _ = S.draw_controls(self.event(), self.cands(specs), m, 1, {}, {})
        self.assertEqual(level, "sic1")
        self.assertEqual(len(got), 5)
        self.assertTrue(set(got) <= {"A", "B", "C", "D", "E", "F"})

    def test_exclusions(self):  # an issuer with any event within 30 days is never a control
        specs = [("A", "36"), ("B", "36"), ("C", "36"), ("D", "36"), ("E", "36"), ("F", "36")]
        m = market({s: {1: (10, 10)} for s, _ in specs})
        got, _, _ = S.draw_controls(self.event(), self.cands(specs), m, 1, {"cA": [DAYS[1] + dt.timedelta(days=30)]}, {})
        self.assertNotIn("A", got)
        got, _, _ = S.draw_controls(self.event(), self.cands(specs), m, 1, {"cA": [DAYS[1] + dt.timedelta(days=31)]}, {})
        self.assertEqual(len(got), 5)


class Populations(unittest.TestCase):
    def test_g13_overlap_drop(self):
        idx = {d: i for i, d in enumerate(DAYS)}
        ev = [{"event_id": f"e{i}", "cik": "1", "trigger": "cluster", "entry_date": DAYS[i], "available_at": DAYS[i]}
              for i in (0, 2, 5)]
        kept, dropped = S.overlap(ev, 3, idx)
        self.assertEqual(([e["event_id"] for e in kept], dropped), (["e0", "e5"], 1))


class Inference(unittest.TestCase):
    def test_g14_cr1(self):  # deviations: week a -3, week b +3; SE = sqrt(2 * 18) / 4 = 1.5; t = 3 / 1.5
        c = S.clustered([1.0, 2.0, 3.0, 6.0], ["a", "a", "b", "b"])
        self.assertAlmostEqual(c["se"], 1.5, places=12)
        self.assertAlmostEqual(c["t"], 2.0, places=12)
        self.assertAlmostEqual(S.MDE_MULT, 2.8416, places=12)

    def test_bootstrap_seeded_and_bounded(self):
        vals, weeks = [0.01, -0.02, 0.03, 0.05, -0.01, 0.02], ["a", "a", "b", "c", "c", "d"]
        ci = S.bootstrap_ci(vals, weeks, n=2000)
        self.assertEqual(ci, S.bootstrap_ci(vals, weeks, n=2000))
        self.assertLessEqual(ci[0], sum(vals) / len(vals))
        self.assertGreaterEqual(ci[1], sum(vals) / len(vals))


class L7(unittest.TestCase):
    def test_g15_trade_hand_computed(self):
        """Entry 10 + 0.02 = 10.02, 49 shares; exit 11 - 0.01 = 10.99. Tiered buy 0.507206, sell 0.5264333066;
        P&L 47.53 - 1.0336393066 = 46.4963606934 -> 0.0929927213868 of $500; SPY +1% -> excess 0.0829927213868."""
        m = market({"A": {0: (10, 10), 1: (10, 10), 2: (10, 10.5), 3: (10.8, 11), 9: (11, 11)},
                    "SPY": {0: (400, 400), 1: (400, 401), 2: (401, 402), 3: (403, 404), 9: (404, 404)}})
        e = {"event_id": "e", "symbol": "A", "entry_date": DAYS[1], "available_at": DAYS[1]}
        counts = S._Counter()
        t = S.l7_trade(e, m, {("A", DAYS[1]): (9.98, 10.02)}, {("A", DAYS[3]): (10.99, 11.01)}, 3, counts)
        self.assertEqual(t["shares"], 49)
        self.assertAlmostEqual(t["net"], 46.4963606934 / 500, places=10)
        self.assertAlmostEqual(t["spy"], 0.01, places=12)
        self.assertAlmostEqual(t["excess"], 46.4963606934 / 500 - 0.01, places=10)
        self.assertAlmostEqual(t["cost_pct"], 1.0336393066 / 500, places=10)

    def test_l7_quote_fallbacks_and_drops(self):
        m = market({"A": {0: (10, 10), 1: (10, 10), 2: (10, 10), 3: (10, 11), 9: (11, 11)},
                    "SPY": {i: (1, 1) for i in range(40)}})
        e = {"event_id": "e", "symbol": "A", "entry_date": DAYS[1], "available_at": DAYS[1]}
        counts = S._Counter()
        t = S.l7_trade(e, m, {}, {("A", DAYS[0]): (9.9, 10.1), ("A", DAYS[2]): (10.9, 11.1)}, 3, counts)
        self.assertAlmostEqual(t["entry_px"], 10.1)  # the prior close - 5 min sample
        self.assertEqual(t["exit_px"], 11 - 0.1)    # day H has no sample: the previous session's
        self.assertIsNone(S.l7_trade(e, m, {}, {}, 3, counts))
        self.assertEqual(counts["no_entry_quote"], 1)

    def test_account_t1_settlement(self):
        base = {"entry_px": 10.0, "mult": 1.0, "div_per_share": 0.0, "exit_px": 11.0, "sell": True, "d1": DAYS[1], "dx": DAYS[3]}
        t1 = dict(base, event_id="a", available_at=1, i1=1, exit_i=3)
        t2 = dict(base, event_id="b", available_at=2, i1=3, exit_i=5)  # enters on the exit session: cash not settled
        t3 = dict(base, event_id="c", available_at=3, i1=4, exit_i=6)
        acc = S.account_sim([t1, t2, t3])
        self.assertEqual((acc["trades"], acc["skipped"]), (2, 1))


if __name__ == "__main__":
    unittest.main()
