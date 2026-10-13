"""Index program phase 2, task 5 (spec 7.1, 7.2 and 7.3 with C2, E3): the overlay test and the decisions on SYNTHETIC
numbers. The block SE, the blocks and the consistency gate are bot.ensemble_stats' (the same formulas)."""
import math
import unittest

from bot import ensemble_stats as st, index_stats as ix


class Overlay(unittest.TestCase):
    def test_quarter_blocks(self):
        days = ["2009-01-02", "2009-03-31", "2009-04-01", "2010-01-04"]
        self.assertEqual(ix.quarter_blocks(days), ([0, 0, 1, 4], 5))  # quarters 2009Q1 .. 2010Q1: G = 5

    def test_quarter_se_by_hand(self):
        x = [1.0, 3.0, 2.0, 6.0]
        blocks = [0, 0, 1, 2]
        xb = 3.0  # u = (-2 + 0, -1, 3): sum of squares 4 + 1 + 9 = 14
        self.assertAlmostEqual(st.se(x, blocks, 3), math.sqrt(3 / 2 * 14) / 4)
        self.assertAlmostEqual(ix.block_se(x, ["2009-01-02", "2009-02-02", "2009-04-01", "2009-07-01"]),
                               math.sqrt(3 / 2 * 14) / 4)
        self.assertEqual(xb, sum(x) / len(x))

    def test_pi_and_benchmark_by_hand(self):
        days = ["d1", "d2", "d3", "d4"]
        held = {"d2", "d3"}
        r = [0.0, 0.01, 0.02, 0.0]
        spy = [0.01, 0.02, -0.01, 0.03]
        cash = [0.001, 0.001, 0.001, 0.001]
        pi, x = ix.overlay_x(r, spy, cash, held, days)
        self.assertEqual(pi, 0.5)
        b = [0.5 * s + 0.5 * c for s, c in zip(spy, cash)]
        for got, want in zip(x, [ri - bi for ri, bi in zip(r, b)]):
            self.assertAlmostEqual(got, want)

    def test_holm_order(self):
        self.assertEqual(ix.holm({"TOM": 1.9945, "D": 1.6670}), {"TOM": True, "D": True})
        self.assertEqual(ix.holm({"TOM": 1.9944, "D": 1.9}), {"TOM": False, "D": False})
        self.assertEqual(ix.holm({"TOM": 1.6669, "D": 2.5}), {"TOM": False, "D": True})
        self.assertEqual(ix.holm({"TOM": 1.9945 * (1 - 1e-12), "D": 0.0}), {"TOM": False, "D": False})  # unrounded

    def test_size_gate(self):
        self.assertFalse(ix.size_gate(0.0099 / 252))
        self.assertTrue(ix.size_gate(0.0100 / 252))

    def test_overlay_test_gates(self):
        days = [f"20{y:02d}-{m:02d}-15" for y in range(9, 27) for m in range(1, 13)][:212]
        x = [0.001 + (0.0001 if k % 2 else -0.0001) for k in range(len(days))]
        got = ix.overlay_test(x, days, [v - 0.0005 for v in x])
        self.assertGreater(got["t"], 10)
        self.assertTrue(got["consistency"]["pass"])  # every block positive
        self.assertTrue(got["size"])  # 252 x 0.001 = 25% a year
        self.assertTrue(got["stress"])  # 0.0005 > 0
        bad = ix.overlay_test(x, days, [v - 0.002 for v in x])
        self.assertFalse(bad["stress"])
        self.assertEqual(len(got["block_means"]), 10)

    def test_overlay_passes_needs_all_four(self):
        g = {"consistency": {"pass": True}, "size": True, "stress": True}
        self.assertTrue(ix.passes(g, holm_pass=True))
        self.assertFalse(ix.passes(g, holm_pass=False))
        self.assertFalse(ix.passes({**g, "size": False}, holm_pass=True))


class RoundTrips(unittest.TestCase):
    def test_round_trips_by_hand(self):
        trades = [("d1", "open", "in", 10_000.0, 9_997.3), ("d2", "open", "out", 10_500.0, 10_497.3),
                  ("d3", "close", "in", 10_497.3, 10_494.6), ("d4", "close", "out", 10_200.0, 10_197.3),
                  ("d5", "open", "in", 10_197.3, 10_194.6), ("d6", "open", "out", 10_100.0, 10_097.3),
                  ("d7", "open", "in", 10_097.3, 10_094.6)]  # the last one is still open
        trips = ix.round_trips(trades)
        self.assertEqual(len(trips), 3)
        self.assertAlmostEqual(trips[0], 10_497.3 / 10_000 - 1)  # after the exit over before the entry
        got = ix.round_trip_stats(trips)
        self.assertEqual((got["round_trips"], got["longest_losing_run"]), (3, 2))
        self.assertAlmostEqual(got["winning_share"], 1 / 3)
        self.assertAlmostEqual(got["worst"], 10_197.3 / 10_497.3 - 1)


class Decisions(unittest.TestCase):
    # values whose differences are exact in binary floating point: the decisions compare unrounded numbers
    STATIC = {"growth_p": 0.25, "growth_p_stress": 0.25}

    def pol(self, g, mdd=0.15, gs=None):
        return {"growth_p": g, "mdd_p": mdd, "growth_p_stress": g if gs is None else gs}

    def test_eligibility_c2(self):
        self.assertGreaterEqual(0.26 - 0.25, 0.01)
        self.assertTrue(ix.eligible(self.pol(0.26, mdd=0.20), self.STATIC)["eligible"])  # exactly 1.0 point, 20%
        self.assertFalse(ix.eligible(self.pol(0.26, mdd=0.2001), self.STATIC)["eligible"])  # a: over 20% over P
        self.assertFalse(ix.eligible(self.pol(0.2599), self.STATIC)["eligible"])  # b: 0.99 point is not 1.0
        self.assertFalse(ix.eligible(self.pol(0.26, gs=0.25), self.STATIC)["eligible"])  # d: not above STATIC
        got = ix.eligible(self.pol(0.26), self.STATIC)
        self.assertEqual(set(got), {"a", "b", "d", "eligible"})  # C2: condition c is removed

    def test_outcome_c2(self):
        e = {"eligible": True}
        n = {"eligible": False}
        self.assertEqual(ix.outcome({"TREND": n}, {}), ("STATIC", None))
        self.assertEqual(ix.outcome({"TREND": e}, {"TREND": 0.06}), ("TIMING", "TREND"))
        self.assertEqual(ix.outcome({"TREND": n, "TACT": e}, {"TACT": 0.06}), ("TIMING", "TACT"))
        self.assertEqual(ix.outcome({"TREND": e, "TACT": e}, {"TREND": 0.060, "TACT": 0.065}), ("TIMING", "TACT"))
        self.assertEqual(ix.outcome({"TREND": e, "TACT": e}, {"TREND": 0.060, "TACT": 0.0649}), ("TIMING", "TREND"))


class Tact(unittest.TestCase):
    def test_union_of_passing_overlays(self):
        tom = [("2020-01-03", "close", True), ("2020-01-08", "close", False)]
        dip = [("2020-01-06", "open", True), ("2020-01-10", "open", False)]
        got = ix.union_changes([tom, dip])
        # E3: in SPY when either holds a position; a change only when the combined state changes
        self.assertEqual(got, [("2020-01-03", "close", True), ("2020-01-10", "open", False)])
        self.assertEqual(ix.union_changes([tom]), tom)


if __name__ == "__main__":
    unittest.main()
