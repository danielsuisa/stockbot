"""P3D evaluator diagnostics: step variants vs fills.simulate, frictionless exits, marketable control, outcome rule."""
import datetime as dt
import random
import unittest

from scalp_eval import fills_variants as FV
from scalp_eval.fills import simulate
from scalp_eval.p3d_outcome import outcome
from scalp_eval.tests.test_l7 import CLOSE, DAY, FILL, HSD, HS, T, bars

UTC = dt.timezone.utc


class Steps(unittest.TestCase):
    def test_s3_equals_s4_when_first_bar_fills(self):
        rng = random.Random(5)
        for _ in range(300):  # random paths whose first bar trades below the limit: S3 == S4 by construction
            rows = [FILL]
            px = 50.0
            for _ in range(80):
                o = px + rng.uniform(-0.3, 0.3)
                h, l = o + rng.uniform(0, 0.4), o - rng.uniform(0, 0.4)
                px = rng.uniform(l, h)
                rows.append((o, h, l, px, 10000))
            b = bars(rows)
            tr = simulate(T, 50.0, 49.5, 50.75, 600, 60, 10, b, CLOSE, HS, HSD, DAY)
            st, r3, _ = FV.step_r("S3", T, 50.0, 49.5, 50.75, 600, 60, 10, b, CLOSE, HSD)
            self.assertEqual(tr.status, "filled")
            self.assertAlmostEqual((tr.exit_gross - tr.entry_gross) / 0.5, r3, places=12)

    def test_s3_fills_where_s4_expires(self):
        b2 = bars([(50.05, 50.10, 50.00, 50.02, 10000)] * 3)  # never below the limit
        self.assertEqual(simulate(T, 50.0, 49.5, 50.75, 600, 60, 10, b2, CLOSE, HS, HSD, DAY).status, "expired")
        st, r, _ = FV.step_r("S3", T, 50.0, 49.5, 50.75, 600, 60, 10, b2, CLOSE, HSD)
        self.assertEqual(st, "filled")  # first eligible bar, whatever its low
        self.assertAlmostEqual(r, (50.02 - (50.0 - HSD)) / 0.5)  # time exit at the last close (H covers 3 bars)

    def test_s2_uses_bar_open_and_s1_frictionless(self):
        b = bars([(49.95, 50.80, 49.90, 50.70, 10000)])  # target inside the entry bar
        st, r2, _ = FV.step_r("S2", T, 50.0, 49.5, 50.75, 600, 60, 10, b, CLOSE, HSD)
        self.assertAlmostEqual(r2, (50.70 - 49.95) / 0.5)  # P3 exits: no target in the fill bar -> time exit at close
        st, r1, conflict = FV.step_r("S1", T, 50.0, 49.5, 50.75, 600, 60, 10, b, CLOSE, HSD)
        self.assertAlmostEqual(r1, (50.75 - 49.95) / 0.5)
        self.assertFalse(conflict)
        gap = bars([(49.95, 50.0, 49.9, 49.95, 10000), (49.0, 49.2, 48.8, 49.1, 10000)])
        self.assertAlmostEqual(FV.step_r("S1", T, 50.0, 49.5, 50.75, 600, 60, 10, gap, CLOSE, HSD)[1], (49.5 - 49.95) / 0.5)
        both = bars([(49.95, 50.9, 49.4, 50.0, 10000)])
        st, r, conflict = FV.step_r("S1", T, 50.0, 49.5, 50.75, 600, 60, 10, both, CLOSE, HSD)
        self.assertTrue(conflict)
        self.assertAlmostEqual(r, (49.5 - 49.95) / 0.5)  # stop wins

    def test_marketable(self):
        b = bars([(50.10, 50.20, 50.05, 50.15, 10000), (50.20, 50.80, 50.10, 50.70, 10000)])
        st, gross, net, risk = FV.marketable(T, 50.0, 49.5, 50.75, 60, 10, b, CLOSE, HS, DAY)
        self.assertEqual(st, "filled")
        self.assertAlmostEqual(gross, 10 * (50.75 - 50.10))  # bought at the open, no limit condition
        self.assertLess(net, 10 * (50.75 - 50.12))
        self.assertAlmostEqual(risk, 5.0)


class Rule(unittest.TestCase):
    def d1(self, top, rho, a):
        return {f"live-h60/{w}": {"top_decile_r": top, "spearman_decile_r": rho, "auc": a} for w in ("W1", "W2", "W3")}

    def d2(self, s1):
        return {w: {"mean_r": {"S1": s1}} for w in ("W1", "W2", "W3")}

    def p3(self, net):
        return {"signal": {f"live-h60/{w}": {"tiered": {"expectancy_r": net}} for w in ("W1", "W2", "W3")}}

    def test_outcomes(self):
        self.assertEqual(outcome(self.d1(0.05, 0.9, 0.6), self.d2(0.0), self.p3(-0.5))["outcome"], "A")
        self.assertEqual(outcome(self.d1(0.2, 0.9, 0.6), self.d2(0.0), self.p3(-0.5))["outcome"], "B")  # 0.2 < 0.5
        self.assertEqual(outcome(self.d1(0.6, 0.9, 0.6), self.d2(0.0), self.p3(-0.5))["outcome"], "C")
        self.assertEqual(outcome(self.d1(0.6, 0.5, 0.6), self.d2(0.0), self.p3(-0.5))["outcome"], "A")  # Spearman fails


if __name__ == "__main__":
    unittest.main()
