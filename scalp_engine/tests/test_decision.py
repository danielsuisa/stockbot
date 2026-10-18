"""P3 Stage 2 tests: labels, new features, leakage (future perturbation, stored availability) and purging."""
import collections
import datetime as dt
import hashlib
import json
import math
import os
import random
import unittest
from pathlib import Path

import polars as pl

from scalp_engine import decision as D
from scalp_engine import features as F
from scalp_engine import ingest, proxies, purge, universe
from scalp_engine.contracts import load_json, load_schema, validate
from scalp_engine.data_access import day_bounds, partition_dates, read_frame

UTC = dt.timezone.utc
ROOT = Path(os.environ.get("SCALP_DATA_ROOT") or r"D:\scalp-data")
HAVE = (ROOT / "screener_snapshots").exists() and (ROOT / "raw_bars_1m").exists()
FEATURE_COLS = ["ret_5m", "ret_15m", "ret_30m", "ret_open", "range_open_pct", "ret_5m_sip", "ret_15m_sip", "ret_30m_sip",
                "ret_open_sip", "range_open_pct_sip", "sigma_1m_5d", "dist_stop_pct", "dilution_v2", "pass_dilution_v2"] + \
               [c for c in D.P2_LIVE if c != "inputs_available_at"] + [f"{c}_sip" for c in D.P2_SIP if c != "inputs_available_at"]


def utc(*a):
    return dt.datetime(*a, tzinfo=UTC)


def bars(symbol, start, closes, highs=None, lows=None, opens=None):
    n = len(closes)
    return pl.DataFrame({"symbol": [symbol] * n, "event_at": [start + dt.timedelta(minutes=i) for i in range(n)],
                         "open": opens or closes, "high": highs or closes, "low": lows or closes, "close": closes,
                         "volume": [100] * n, "vwap": closes},
                        schema={"symbol": pl.Utf8, "event_at": pl.Datetime("us", "UTC"), "open": pl.Float64,
                                "high": pl.Float64, "low": pl.Float64, "close": pl.Float64, "volume": pl.Int64,
                                "vwap": pl.Float64})


class Registry(unittest.TestCase):
    def test_registry_p3_matches_code(self):
        committed = {e["name"]: e for e in load_json(D.REGISTRY_FILE)}
        sch = load_schema("feature_definition")
        for e in D.registry():
            validate(e, sch)
            self.assertEqual(committed.get(e["name"]), e, f"{e['name']}: regenerate registry_p3.json / bump version")


class Labels(unittest.TestCase):
    def setUp(self):
        self.t = utc(2024, 6, 3, 14, 0)  # 10:00 ET
        self.close_at = utc(2024, 6, 3, 20, 0)
        self.sigma1 = 0.01 / math.sqrt(60)  # sigma_60 = 1%

    def label(self, highs, lows, closes, opens=None, H=60):
        b = bars("A", self.t + dt.timedelta(minutes=1), closes, highs, lows, opens)
        return D.SipPath(b, self.close_at).label("A", self.t, self.sigma1, H)

    def test_target_first(self):
        entry, lab, r, end = self.label([100.5, 101.6], [99.5, 100.0], [100.0, 101.0], opens=[100.0, 100.5])
        self.assertEqual((entry, lab, r), (100.0, 1, 1.5))
        self.assertEqual(end, self.t + dt.timedelta(minutes=61))

    def test_stop_first_and_same_bar_conflict(self):
        self.assertEqual(self.label([100.2, 102.0], [98.9, 99.5], [99.5, 101.0], opens=[100.0, 99.5])[1:3], (0, -1.0))
        self.assertEqual(self.label([101.6], [98.9], [100.0], opens=[100.0])[1:3], (0, -1.0))  # same bar: stop wins

    def test_time_exit_r(self):
        entry, lab, r, _ = self.label([100.2] * 3, [99.8] * 3, [100.0, 100.1, 100.5], opens=[100.0, 100.0, 100.1], H=3)
        self.assertEqual(lab, 0)
        self.assertAlmostEqual(r, (100.5 - 100.0) / (100.0 * self.sigma1 * math.sqrt(3)))

    def test_entry_is_first_bar_after_t_plus_3s(self):
        b = bars("A", self.t, [100.0, 101.0], opens=[100.0, 101.0])  # a bar opening exactly at t is not the entry
        entry, *_ = D.SipPath(b, self.close_at).label("A", self.t, self.sigma1, 60)
        self.assertEqual(entry, 101.0)


class NewFeatures(unittest.TestCase):
    def test_sigma_pooled_std(self):
        import statistics
        r = [0.001, -0.002, 0.0005, 0.003] * 150
        st = {"A": (len(r), math.fsum(r), math.fsum(x * x for x in r))}
        self.assertAlmostEqual(D.sigma_1m([st], "A"), statistics.stdev(r), places=12)
        self.assertIsNone(D.sigma_1m([{"A": (10, 0.0, 0.0)}], "A"))

    def test_momentum_ignores_unavailable_bars(self):
        open_at = utc(2024, 6, 3, 13, 30)
        b = bars("A", open_at, [100.0 + i for i in range(60)])
        t = open_at + dt.timedelta(minutes=40)
        base = D.momentum(b, ["A"], [t], open_at)
        future = (pl.col("event_at") + F.BAR_AVAIL) > t
        pert = b.with_columns(pl.when(future).then(pl.col(c) * 9).otherwise(pl.col(c)).alias(c) for c in ("high", "low", "close"))
        self.assertTrue(base.equals(D.momentum(pert, ["A"], [t], open_at)))
        self.assertAlmostEqual(base["ret_5m"][0], math.log(138.0 / 133.0))  # last available bar opened at +38 min


class Purge(unittest.TestCase):
    def rows(self):
        sessions = [dt.date(2024, 1, 2) + dt.timedelta(days=i) for i in range(50)]
        rows = []
        for s in sessions:
            for h in (14, 15, 16):
                t = dt.datetime.combine(s, dt.time(h), UTC)
                rows.append((s, t, t + dt.timedelta(minutes=90)))
        return sessions, rows

    def test_purged_kfold_no_overlap_and_embargo(self):
        sessions, rows = self.rows()
        for train, valid in purge.purged_kfold(rows, sessions, k=5, embargo=1):
            v0, v1 = min(rows[i][1] for i in valid), max(rows[i][2] for i in valid)
            vdays = sorted({rows[i][0] for i in valid})
            nxt = sessions[sessions.index(vdays[-1]) + 1] if vdays[-1] != sessions[-1] else None
            for i in train:
                self.assertFalse(rows[i][1] <= v1 and rows[i][2] >= v0)  # label interval overlaps validation span
                self.assertNotEqual(rows[i][0], nxt)  # embargo session

    def test_walkforward_purge(self):
        sessions, rows = self.rows()
        test_start = sessions[40]
        train = purge.walkforward_train(rows, sessions, test_start, months=18, embargo=1)
        test_t0 = min(r[1] for r in rows if r[0] >= test_start)
        self.assertTrue(all(rows[i][2] < test_t0 for i in train))
        self.assertNotIn(sessions[39], {rows[i][0] for i in train})  # embargo
        self.assertEqual(max(rows[i][0] for i in train), sessions[38])


@unittest.skipUnless(HAVE, "store not present")
class LeakageReal(unittest.TestCase):
    """Future perturbation on real sessions: alter every bar available after its decision time and every session
    after d; decision-point features are unchanged (labels may change: they look forward by design)."""

    def test_future_perturbation(self):
        rng = random.Random(hashlib.sha256(b"p3-leakage-1").hexdigest())
        cal = read_frame(ROOT / "calendar", *ingest.range_bounds(dt.date(2023, 8, 1), dt.date(2026, 10, 16))).sort("trading_date")
        rows = cal.to_dicts()
        cmap = proxies.latest_cik_map()
        cik_of = {s: c for s, c in cmap.select("symbol", "cik").iter_rows() if c}
        for k in sorted(rng.sample(range(40, len(rows)), 3)):
            c, d = rows[k], rows[k]["trading_date"]
            prior = collections.deque(maxlen=D.SIGMA_SESSIONS)
            for j in range(k - D.SIGMA_SESSIONS, k):
                pc = rows[j]
                b = read_frame(ROOT / "raw_bars_1m", *day_bounds(pc["trading_date"]))
                prior.append(D.day_return_stats(b.filter(pl.col("feed") == "sip"), pc["open_at"], pc["close_at"]))
            uni = read_frame(ROOT / "universe_snapshots", *day_bounds(dt.date(d.year, d.month, 1))[:1],
                             day_bounds(d)[1]).filter((pl.col("method_version") == universe.METHOD) & (pl.col("universe") == "top500"))
            mm = set(uni["symbol"].to_list())
            snaps = read_frame(ROOT / "screener_snapshots", *day_bounds(d)).filter(
                pl.col("event_at").is_in([F.et(d, ct) for ct in D.DECISION_TIMES]))
            fil = read_frame(ROOT / "edgar_filings", day_bounds(d)[0] - dt.timedelta(days=31), day_bounds(d)[1])
            bars_d = read_frame(ROOT / "raw_bars_1m", *day_bounds(d))
            c2 = dict(c, prev_session_close_at=rows[k - 1]["session_close_at"])
            base = D.session_rows(d, c2, mm, snaps, bars_d, list(prior), fil, cik_of)
            for t in rng.sample([F.et(d, ct) for ct in D.DECISION_TIMES], 4):
                fut = (pl.col("event_at") + F.BAR_AVAIL) > t
                pb = bars_d.with_row_index("_i").filter(~fut | (pl.col("_i") % 2 == 0)).drop("_i").with_columns(
                    *[pl.when(fut).then(pl.col(x) * 1.9).otherwise(pl.col(x)).alias(x) for x in ("open", "high", "close")],
                    pl.when(fut).then(pl.col("low") * 0.3).otherwise(pl.col("low")).alias("low"))
                pf = pl.concat([fil.filter((pl.col("event_at") + dt.timedelta(seconds=60)) <= t),
                                fil.head(0).vstack(fil.head(len(fil) and 1).with_columns(
                                    pl.lit(t).alias("event_at"), pl.lit("424B5").alias("form")))]) if fil.height else fil
                pert = D.session_rows(d, c2, mm, snaps.filter(pl.col("event_at") <= t), pb, list(prior), pf, cik_of)
                a = base.filter(pl.col("t") == t).sort("symbol").select(FEATURE_COLS)
                b2 = pert.filter(pl.col("t") == t).sort("symbol").select(FEATURE_COLS)
                with self.subTest(day=str(d), t=str(t)):
                    self.assertTrue(a.equals(b2, null_equal=True))

    @unittest.skipUnless((ROOT / "decision_points").exists(), "decision points not built")
    def test_stored_availability(self):
        bad = n = 0
        for day in sorted(partition_dates(ROOT / "decision_points")):
            s = read_frame(ROOT / "decision_points", *day_bounds(day), columns=["features_available_at"])
            n += s.height
            bad += s.filter(pl.col("features_available_at") > pl.col("event_at")).height
        self.assertGreater(n, 0)
        self.assertEqual(bad, 0)


if __name__ == "__main__":
    unittest.main()
