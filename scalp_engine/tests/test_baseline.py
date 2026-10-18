"""P3 baseline mechanics on a small synthetic decision-point store (temp data root): walk-forward, purged CV,
threshold, signed recommendations. Also unit checks of the engine metric and price rounding."""
import datetime as dt
import json
import math
import os
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import polars as pl

from scalp_engine import baseline as B
from scalp_engine import decision as D
from scalp_engine import signing, storage
from scalp_engine.data_access import ET

UTC = dt.timezone.utc


class Units(unittest.TestCase):
    def test_pf(self):
        self.assertAlmostEqual(B.pf(np.array([1.5, -1.0, -1.0, np.nan])), 0.75)
        self.assertEqual(B.pf(np.array([1.0])), float("inf"))

    def test_cents(self):
        self.assertEqual(B.cents_up(10.001), 10.01)
        self.assertEqual(B.cents_down(9.999), 9.99)
        self.assertEqual(B.cents_up(10.00), 10.0)

    def test_net_r_zero_shares_is_nan(self):
        df = pl.DataFrame({"entry_ref_h60": [50.0, 600.0], "sigma_1m_5d": [0.001, 0.001], "spread_nbbo_delayed": [0.0005, 0.0005],
                           "r_h60": [1.5, 1.5]})
        v = B.net_r(df, 60)
        q = math.floor(min(5 / (50 * 0.001 * math.sqrt(60)), 500 / 50))
        cost = (2 * max(0.35, 0.0035 * q) + q * 50 * 0.0005) / (q * 50 * 0.001 * math.sqrt(60))
        self.assertAlmostEqual(v[0], 1.5 - cost)
        self.assertTrue(math.isnan(v[1]))


class EndToEnd(unittest.TestCase):
    def test_one_month_two_models(self):
        rng = random.Random(1)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"SCALP_DATA_ROOT": tmp}), \
                mock.patch.object(B, "TEST_MONTHS", [(2025, 5)]), \
                mock.patch.object(B, "GRID", B.GRID[:2]):
            days = [dt.date(2023, 11, 1) + dt.timedelta(days=i) for i in range(0, 580) if (dt.date(2023, 11, 1) + dt.timedelta(days=i)).weekday() < 5]
            rows = []
            for d in days:
                for k in range(8):
                    t = dt.datetime.combine(d, dt.time(14, 45 + 5 * (k % 3)), UTC)
                    x = rng.gauss(0, 1)
                    lab = int(x + rng.gauss(0, 1) > 1.0)
                    r = {c: rng.gauss(0, 1) for c in B.RESEARCH if c not in ("sector_etf", "regime", "earnings_8k", "macro_day")}
                    r.update({"ret_5m": x, "spread_nbbo_delayed": 0.0004, "sigma_1m_5d": 0.0008, "last_price": 50.0,
                              "event_at": t, "symbol": f"S{k}", "decision_date": d, "regime": "chop", "sector_etf": "XLK",
                              "earnings_8k": False, "macro_day": False})
                    for H in D.HORIZONS:
                        r.update({f"label_h{H}": lab, f"r_h{H}": 1.5 if lab else -1.0, f"entry_ref_h{H}": 50.0,
                                  f"label_end_h{H}": t + dt.timedelta(minutes=H + 1)})
                    rows.append(r)
            df = pl.DataFrame(rows, infer_schema_length=None)
            cols = [c["name"] for c in storage.contract("decision_points")["columns"]]
            full = df.with_columns(*[pl.lit(None, dtype=storage.schema("decision_points")[c]).alias(c) for c in cols if c not in df.columns])
            full = full.with_columns(pl.lit(["x@1.0.0+0123456789ab"], dtype=pl.List(pl.Utf8)).alias("feature_versions"))
            sch = storage.schema("decision_points")
            fill = []
            for c in storage.contract("decision_points")["columns"]:
                if c["nullable"] or c["name"] in df.columns or c["name"] == "feature_versions":
                    continue
                typ = sch[c["name"]]
                val = (pl.lit(True) if typ == pl.Boolean else pl.col("event_at") if typ == pl.Datetime("us", "UTC")
                       else pl.lit("false" if c["name"] == "dilution_v2" else "synthetic") if typ == pl.Utf8
                       else pl.lit(1, dtype=typ) if typ in (pl.Int32, pl.Int64) else pl.lit(15.0))
                fill.append(val.cast(typ).alias(c["name"]))
            full = full.with_columns(fill)
            for (d,), g in full.partition_by("decision_date", as_dict=True).items():
                storage.write_partition("decision_points", storage.validate("decision_points", g.select(cols).cast(storage.schema("decision_points"))),
                                        part="t", source="synthetic", date=d)
            key_dir = Path(tmp) / "k"
            key_dir.mkdir()
            signing.keygen(key_dir / "p.pem", key_dir / "p.pub.pem")
            summary = B.run(str(key_dir / "p.pem"), models=[("live", 60), ("research", 60)])
            self.assertEqual([s["model"] for s in summary], ["live-h60", "research-h60"])
            recs = (Path(tmp) / "recommendations" / "p3" / "p3-live-h60-2025-05.jsonl").read_text().splitlines()
            self.assertGreater(len(recs), 0)
            from cryptography.hazmat.primitives import serialization
            pub = serialization.load_pem_public_key((key_dir / "p.pub.pem").read_bytes())
            env = json.loads(recs[0])
            self.assertTrue(signing.verify(env, pub))
            self.assertTrue(env["payload"]["created_at"].startswith("2025-05"))
            ledger = [json.loads(x) for x in B.ledger_path().read_text().splitlines()]
            self.assertEqual(sum(1 for x in ledger if x["model"] == "live-h60" and x["fold"] != "final"), 2 * 5)


if __name__ == "__main__":
    unittest.main()
