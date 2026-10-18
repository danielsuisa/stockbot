"""P3D engine diagnostics: AUC, deciles, D1 table, payload-set hash."""
import datetime as dt
import unittest

import numpy as np
import polars as pl

from scalp_engine import diagnostics as G

UTC = dt.timezone.utc


class Units(unittest.TestCase):
    def test_auc(self):
        p = np.array([0.1, 0.4, 0.35, 0.8])
        y = np.array([0, 0, 1, 1])
        self.assertAlmostEqual(G.auc(p, y), 0.75)  # pairs (pos > neg): (0.35>0.1), (0.8>0.1), (0.8>0.4) = 3 of 4
        self.assertAlmostEqual(G.auc(np.array([0.5, 0.5]), np.array([0, 1])), 0.5)  # ties count half
        self.assertIsNone(G.auc(np.array([0.1]), np.array([1])))

    def test_deciles_and_table(self):
        n = 1000
        t0 = dt.datetime(2025, 6, 2, 14, tzinfo=UTC)
        p = np.linspace(0, 1, n)
        frame = pl.DataFrame({"symbol": [f"S{i:04d}" for i in range(n)], "event_at": [t0] * n, "p": p,
                              "label": (p > 0.9).astype(int), "r": np.where(p > 0.9, 1.5, -0.1)})
        d = G.deciles(frame.sample(fraction=1.0, shuffle=True, seed=3))
        self.assertEqual(sorted(set(d["decile"].to_list())), list(range(1, 11)))
        self.assertEqual(d.filter(pl.col("decile") == 10)["p"].min(), p[900])
        t = G.d1_table(frame)
        self.assertAlmostEqual(t["top_decile_r"], 1.5)  # decile 10 = p[900:], all above 0.9 (p[900] = 900/999)
        self.assertAlmostEqual(t["bottom_decile_r"], -0.1)
        self.assertAlmostEqual(t["auc"], 1.0)
        # Spearman on a strictly increasing R: deciles 1-9 above all share R = -0.1, so their means differ only by
        # float rounding and their order is noise
        mono = frame.with_columns(pl.col("p").alias("r"))
        self.assertAlmostEqual(G.d1_table(mono)["spearman_decile_r"], 1.0)

    def test_payload_hash_is_order_free(self):
        a = [{"rec_id": "b-0000001", "qty": 1}, {"rec_id": "a-0000001", "qty": 2}]
        self.assertEqual(G.payload_set_hash(a), G.payload_set_hash(list(reversed(a))))
        self.assertNotEqual(G.payload_set_hash(a), G.payload_set_hash([dict(a[0], qty=3), a[1]]))


if __name__ == "__main__":
    unittest.main()
