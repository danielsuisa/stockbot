"""Phase C task 9 (spec 6.4, enforcement points 1 and 2): the blind report is built by one function from the arrays x
and c; it is byte-identical when every x_i changes sign or is shifted by a constant; it holds whitelisted keys only.
Synthetic numbers only."""
import json
import random
import unittest

from bot import blind


def case(x=None):
    rng = random.Random(5)
    n = 60
    x = x if x is not None else [rng.uniform(-1, 1) + 0.123456 for _ in range(n)]
    c = [rng.uniform(0, 0.1) for _ in range(n)]
    filled = [k % 3 != 0 for k in range(n)]
    sig = [("S1", "S2", "S3", "S4")[k % 4] for k in range(n)]
    blocks10 = [k * 10 // n for k in range(n)]
    counts = {"firings": {"S1": {"0": 3}}, "refusals": {"S1": {"already open": 1}}, "issued": {"S1": {"0": 2}},
              "pools_dropped_small": 1, "quote_refused": 2, "passing_quote_free": 100, "issued_total": 60}
    return x, c, filled, sig, blocks10, counts


def text(x, c, filled, sig, blocks10, counts):
    return blind.serialize(blind.report(x, c, filled, sig, blocks10, counts, K=1.2, r_s={"S3": 1.6}, w_s={"S3": 0.4}))


class Invariance(unittest.TestCase):
    def test_report_invariant_sign(self):
        x, *rest = case()
        self.assertEqual(text(x, *rest), text([-v for v in x], *rest))

    def test_report_invariant_shift(self):
        x, *rest = case()
        self.assertEqual(text(x, *rest), text([v + 3.7 for v in x], *rest))


class Whitelist(unittest.TestCase):
    def test_whitelist(self):
        rep = blind.report(*case(), K=1.2, r_s={"S3": 1.6}, w_s={"S3": 0.4})
        self.assertEqual(tuple(rep), blind.BLIND_KEYS)
        self.assertEqual(set(rep["cost_r_percentiles"]["S1"]), {"p50", "p75", "p90"})
        with self.assertRaises(ValueError):
            blind.check_whitelist({**rep, "x_bar": 0.1})

    def test_never_in_report(self):
        x, *rest = case()
        out = text(x, *rest)
        xbar = sum(x) / len(x)
        for v in (xbar, -xbar):
            self.assertNotIn(f"{v:.6f}"[:7], out)  # x-bar never appears, in any rounding
        words = json.dumps(blind.BLIND_KEYS)
        for bad in ("mean", "median", "return", "win", "sum", "exit", "fill", "hold"):
            self.assertNotIn(bad, words)

    def test_cost_percentiles_nearest_rank_filled_only(self):
        vals = [0.01 * k for k in range(1, 11)]  # ten filled S1 tickets
        got = blind.percentiles(vals)
        self.assertEqual(got, {"p50": 0.05, "p75": 0.08, "p90": 0.09})


if __name__ == "__main__":
    unittest.main()
