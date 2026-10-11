"""Phase C tasks 8 and 10 (spec 6.1, 6.3, 6.4, 6.6): blocks, the SE of 6.3, T10/T5, check 1, b, the gates and the
outcomes. Synthetic numbers only."""
import math
import unittest

from bot import ensemble_stats as st


class Blocks(unittest.TestCase):
    def test_ten_blocks_uneven(self):
        n = 23
        got = [st.block(i, n) for i in range(n)]
        self.assertEqual(got, [10 * i // 23 for i in range(23)])
        self.assertEqual((got[0], got[-1]), (0, 9))
        self.assertEqual([st.long_block(b) for b in range(10)], [0, 0, 1, 1, 2, 2, 3, 3, 4, 4])


class SE(unittest.TestCase):
    def test_hand_example(self):
        x = [1, -1, 2, 0, 1, -2, 1, 1, 3]
        blocks = [0, 0, 0, 1, 1, 2, 2, 2, 3]
        self.assertAlmostEqual(st.mean(x), 0.6667, places=4)
        se = st.se(x, blocks, G=4)
        self.assertAlmostEqual(se, 0.3966, places=4)
        self.assertAlmostEqual(st.mean(x) / se, 1.68, places=2)

    def test_t10_t5_known_series(self):
        x = [0.5, -0.2, 0.3, 0.1, -0.4, 0.6, 0.2, 0.0, -0.1, 0.4, 0.3, -0.3]
        b10 = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 0]
        b5 = [b // 2 for b in b10]
        xb = st.mean(x)
        u10 = [sum(v - xb for v, b in zip(x, b10) if b == g) for g in range(10)]
        u5 = [sum(v - xb for v, b in zip(x, b5) if b == g) for g in range(5)]
        self.assertAlmostEqual(st.se(x, b10, 10), math.sqrt(10 / 9 * sum(u * u for u in u10)) / 12)
        self.assertAlmostEqual(st.se(x, b5, 5), math.sqrt(5 / 4 * sum(u * u for u in u5)) / 12)
        self.assertEqual((st.CRIT_T10, st.CRIT_T5), (2.262, 2.776))

    def test_invariance_of_the_se(self):
        x = [0.5, -0.2, 0.3, 0.1, -0.4]
        b = [0, 1, 2, 3, 4]
        base = st.se(x, b, 10)
        self.assertAlmostEqual(st.se([-v for v in x], b, 10), base)
        self.assertAlmostEqual(st.se([v + 3.7 for v in x], b, 10), base)


class Check1(unittest.TestCase):
    def test_mu_min_b_and_the_limit(self):
        self.assertAlmostEqual(st.mu_min(0.01, 0.015), 4.1 * 0.015)
        self.assertAlmostEqual(st.b_allowance(1.2243), 1.2243 * 0.0041)
        self.assertTrue(st.confirmable(0.09, 0.01))  # exactly 0.10 R
        self.assertFalse(st.confirmable(0.09, 0.0100001))

    def test_design_effect(self):
        x = [1.0, 2.0, 3.0, 4.0]
        s2 = sum((v - 2.5) ** 2 for v in x) / 3
        self.assertAlmostEqual(st.design_effect(x, 0.8), 0.8 ** 2 / (s2 / 4))
        self.assertAlmostEqual(st.design_effect([-v for v in x], 0.8), st.design_effect(x, 0.8))


class Gates(unittest.TestCase):
    def test_950th_rule_and_p(self):
        ak = [k / 1000 for k in range(1000)]  # the 950th smallest is 0.949
        self.assertFalse(st.above_950th(0.949, ak))  # strictly above
        self.assertTrue(st.above_950th(0.9491, ak))
        self.assertAlmostEqual(st.p_value(0.9491, ak), (1 + 50) / 1001)

    def test_gate1_with_b(self):
        blocks10 = [g for g in range(10) for _ in range(5)]
        x = [0.5 + 0.01 * (k % 3) for k in range(50)]
        ok = st.gate1(x, 0.0, blocks10)
        self.assertTrue(ok["pass"])
        self.assertFalse(st.gate1(x, 10.0, blocks10)["pass"])  # b larger than every x: the mean turns negative

    def test_gate3_blocks_and_halves(self):
        blocks10 = [g for g in range(10) for _ in range(2)]
        pos7 = [1.0] * 14 + [-1.0] * 6  # blocks 0-6 positive, 7-9 negative: 7 of 10, but the second half is not > 0
        self.assertEqual(st.gate3(pos7, blocks10)["blocks_positive"], 7)
        self.assertFalse(st.gate3(pos7, blocks10)["pass"])
        alt = [1.0, 1.0, -1.0, -1.0] * 5  # alternating blocks: 5 of 10
        self.assertFalse(st.gate3(alt, blocks10)["pass"])
        good = [1.0] * 6 + [-0.1] * 2 + [1.0] * 6 + [-0.1] * 2 + [1.0] * 4  # blocks 3 and 7 negative: 8 of 10
        self.assertTrue(st.gate3(good, blocks10)["pass"])

    def test_gate4_top_one_percent_and_signals(self):
        x = [1.0] + [-0.001] * 199  # n = 200: the top 1% is ceil(2) = 2 tickets
        sig = ["S1"] + ["S2"] * 199
        g = st.gate4(x, sig)
        self.assertFalse(g["without_top_1pct"])
        self.assertEqual(st.top_count(200), 2)
        self.assertEqual(st.top_count(150), 2)  # ceil(1.5)
        x2 = [0.1] * 100 + [-1.5] * 10  # mean (10 - 15) / 110 < 0
        sig2 = ["S1"] * 50 + ["S2"] * 50 + ["S3"] * 10
        g2 = st.gate4(x2, sig2)
        self.assertTrue(g2["each_signal_removed"]["S3"])  # without S3 the mean is positive
        self.assertFalse(g2["each_signal_removed"]["S1"])  # without S1: (5 - 15) / 60 < 0
        self.assertFalse(g2["pass"])

    def test_outcome(self):
        self.assertEqual(st.outcome(confirmable=False, comparable=True, gates=None), "NO-GO (not confirmable)")
        self.assertEqual(st.outcome(confirmable=True, comparable=False, gates=None), "NO-GO (not comparable)")
        self.assertEqual(st.outcome(True, True, {"1": True, "2": True, "3": True, "4": True, "5": False}), "NO-GO")
        self.assertEqual(st.outcome(True, True, {g: True for g in "12345"}), "GO")
        self.assertEqual(st.LABELS, {"GO": "עבר מבחן היסטורי, במעקב קדימה", "NO-GO": "ללא יתרון מוכח"})


if __name__ == "__main__":
    unittest.main()
