"""ADR 0003 section 7 leakage tests, registry discipline, and feature unit checks.

Leakage 1 (future perturbation) and 3 (replay parity) run on the real store and skip when it is absent; leakage 2 checks
the stored screener_snapshots / feature_daily tables. The synthetic unit tests always run."""
import datetime as dt
import hashlib
import json
import math
import os
import random
import unittest
from pathlib import Path

import polars as pl

from scalp_engine import features as F
from scalp_engine import pipeline as S, replay, storage
from scalp_engine.contracts import load_json, load_schema, validate
from scalp_engine.data_access import day_bounds, partition_dates, read_frame

UTC = dt.timezone.utc
ROOT = Path(os.environ.get("SCALP_DATA_ROOT") or r"D:\scalp-data")
HAVE_STORE = (ROOT / "raw_bars_1m").exists() and (ROOT / "edgar_filings").exists()
PARITY_DAYS = [dt.date(2024, 4, 17), dt.date(2024, 11, 29), dt.date(2024, 2, 26)]
COMPARED = [c for c in F.SNAPSHOT_COLUMNS if c not in ("t", "symbol", "variant", "feed")]
# every registered feature must be covered by a compared column
FEATURE_COLUMNS = {n: [n] for n in F.FEATURES}
FEATURE_COLUMNS.update({"rvol_pre": ["rvol_pre", "rvol"], "rvol_open": ["rvol_open", "rvol"]})


def utc(*a):
    return dt.datetime(*a, tzinfo=UTC)


class Registry(unittest.TestCase):
    def test_committed_registry_matches_code(self):
        committed = {e["name"]: e for e in load_json(F.REGISTRY_FILE)}
        sch = load_schema("feature_definition")
        for e in F.registry():
            validate(e, sch)
            c = committed.get(e["name"])
            self.assertIsNotNone(c, f"{e['name']} missing from registry_p2.json")
            if c["code_hash"] != e["code_hash"]:
                self.assertNotEqual(c["version"], e["version"],
                                    f"{e['name']}: defining code changed; bump its version and regenerate the registry")
            self.assertEqual(c, e, f"{e['name']}: regenerate registry_p2.json (features.write_registry)")

    def test_every_feature_is_compared_by_leakage_tests(self):
        for name in F.FEATURES:
            self.assertTrue(set(FEATURE_COLUMNS[name]) <= set(COMPARED), name)


class Units(unittest.TestCase):
    def test_corwin_schultz_zero_for_constant_bars(self):
        df = pl.DataFrame({"h0": [10.0], "l0": [10.0], "h1": [10.0], "l1": [10.0]})
        v = df.select(F._cs_pair(pl.col("h0"), pl.col("l0"), pl.col("h1"), pl.col("l1"))).item()
        self.assertEqual(v, 0.0)

    def test_regime_rules(self):
        t = dt.time(10, 0)
        self.assertEqual(F.regime(True, 12, 0.0, 0.0, 0.1, t), "event")
        self.assertEqual(F.regime(False, 31, 0.0, 0.0, 0.1, t), "event")
        self.assertEqual(F.regime(False, 15, 0.0, 0.0, 0.5, t), "event")
        self.assertEqual(F.regime(False, 15, 0.0, 0.0, 0.5, dt.time(9, 40)), "chop")  # RV rule only from 09:45
        self.assertEqual(F.regime(False, 15, 0.05, 0.01, 0.1, t), "trend")
        self.assertEqual(F.regime(False, 15, 0.05, -0.01, 0.1, t), "chop")  # signs disagree
        self.assertEqual(F.regime(False, 15, 0.02, 0.01, 0.1, t), "chop")

    def test_split_adjustment_uses_only_splits_known_by_d(self):
        bars = pl.DataFrame({"symbol": ["A", "A"], "event_at": [utc(2024, 6, 6, 4), utc(2024, 6, 7, 4)],
                             "open": [100.0, 10.0], "high": [100.0, 10.0], "low": [100.0, 10.0], "close": [100.0, 10.0],
                             "volume": [1.0, 10.0]})
        act = pl.DataFrame({"action_type": ["forward_splits"], "event_at": [utc(2024, 6, 7, 4)], "symbol": ["A"],
                            "raw": [json.dumps({"new_rate": 10, "old_rate": 1})]})
        before = F.adjusted_daily(bars, F.split_factors(act, dt.date(2024, 6, 6)))
        after = F.adjusted_daily(bars, F.split_factors(act, dt.date(2024, 6, 10)))
        self.assertEqual(before["close"].to_list(), [100.0, 10.0])  # split not known yet
        self.assertEqual(after["close"].to_list(), [10.0, 10.0])
        self.assertEqual(after["volume"].to_list(), [10.0, 10.0])


def build_context(st, sessions, k):
    """Inputs for session k with prior profiles rebuilt from the 20 sessions before it."""
    prof, cache = S.new_profiles(), {}
    for j in range(max(0, k - F.RVOL_SESSIONS), k):
        d = sessions[j]
        c = st["cal"].filter(pl.col("trading_date") == d).row(0, named=True)
        bars = read_frame(ROOT / "raw_bars_1m", *day_bounds(d))
        tables = {f: bars.filter(pl.col("feed") == f) for f in ("iex", "sip")}
        S.push_profiles(prof, {"day": d, "cal": c, "times": [F.et(d, t) for t in F.SNAPSHOT_TIMES], "tables": tables,
                               "fetched": set(st["plan"][d][1])})
    return S.session_inputs(st, sessions, k, prof, cache)


def frames_equal(a, b, cols):
    a, b = a.sort(["symbol", "t"]), b.sort(["symbol", "t"])
    if a.height != b.height:
        return False, "row count"
    for c in cols:
        x, y = a[c].to_list(), b[c].to_list()
        for i, (u, v) in enumerate(zip(x, y)):
            if u != v and not (isinstance(u, float) and isinstance(v, float) and math.isnan(u) and math.isnan(v)):
                return False, f"{c} row {i}: {u!r} != {v!r}"
    return True, ""


@unittest.skipUnless(HAVE_STORE, "store not present")
class LeakageFuturePerturbation(unittest.TestCase):
    """Leakage test 1: alter/delete every input row available after t; every feature at t is unchanged."""

    @classmethod
    def setUpClass(cls):
        cls.st = S.load_static(dt.date(2023, 8, 1), dt.date(2026, 10, 16))
        cls.sessions = cls.st["cal"]["trading_date"].to_list()
        cls.choice = json.loads(S.choice_file().read_text())["choice"] if S.choice_file().exists() else "cs"

    def perturbed(self, inp, t, variant):
        p = dict(inp)
        bars = {}
        for feed, b in inp["bars"].items():
            fut = (pl.col("event_at") + F.BAR_AVAIL) > t
            b2 = b.with_row_index("_i").filter(~fut | (pl.col("_i") % 2 == 0)).drop("_i")  # delete half of the future
            bars[feed] = b2.with_columns(*[pl.when(fut).then(pl.col(c) * 1.7).otherwise(pl.col(c)).alias(c)
                                           for c in ("open", "high", "close", "vwap")],
                                         pl.when(fut).then(pl.col("low") * 0.5).otherwise(pl.col("low")).alias("low"),
                                         pl.when(fut).then(pl.col("volume") * 3).otherwise(pl.col("volume")).alias("volume"))
        p["bars"] = bars
        cut = t - F.NBBO_DELAY if variant == "iex_live" else t
        s = inp["samples"]
        p["samples"] = s.with_columns(pl.when(pl.col("event_at") > cut).then(pl.col("ask_price") * 3)
                                      .otherwise(pl.col("ask_price")).alias("ask_price"))
        fil = inp["filings"].filter((pl.col("event_at") + F.FILING_DELAY) <= t)
        fake = []
        for sym in sorted(inp["members"])[:50]:
            cik = inp["cik_of"].get(sym)
            if cik:
                fake += [{"event_at": t + dt.timedelta(seconds=5), "cik": cik, "form": "8-K", "items": "2.02,9.01"},
                         {"event_at": t - dt.timedelta(seconds=30), "cik": cik, "form": "424B5", "items": None}]
        if fake:
            fil = pl.concat([fil.select("event_at", "cik", "form", "items"),
                             pl.DataFrame(fake, schema={"event_at": pl.Datetime("us", "UTC"), "cik": pl.Utf8, "form": pl.Utf8,
                                                        "items": pl.Utf8})])
        p["filings"] = fil
        p.pop("flags")
        return p

    def test_future_rows_do_not_change_features(self):
        rng = random.Random(hashlib.sha256(b"p2-leakage-1").hexdigest())
        candidates = [k for k, d in enumerate(self.sessions) if d >= dt.date(2023, 10, 2)]
        for k in sorted(rng.sample(candidates, 3)):
            ctx, inp = build_context(self.st, self.sessions, k)
            times = rng.sample(ctx["times"], 6)
            for variant in F.VARIANTS:
                full = F.snapshot_frame(inp, variant, ctx["times"], self.choice)
                for t in times:
                    base = full.filter(pl.col("t") == t)
                    base_inp = dict(inp)
                    base_inp.pop("flags")
                    alone = F.snapshot_frame(base_inp, variant, [t], self.choice)
                    pert = F.snapshot_frame(self.perturbed(inp, t, variant), variant, [t], self.choice)
                    for name, other in (("recomputed", alone), ("perturbed", pert)):
                        ok, why = frames_equal(base, other, COMPARED)
                        with self.subTest(day=str(self.sessions[k]), variant=variant, t=str(t), what=name):
                            self.assertTrue(ok, why)

    def test_daily_inputs_after_d_do_not_change_daily_features(self):
        rng = random.Random(hashlib.sha256(b"p2-leakage-1-daily").hexdigest())
        k = rng.choice([k for k, d in enumerate(self.sessions) if d >= dt.date(2023, 12, 1)])
        d, before = self.sessions[k], self.sessions[:k]
        mm = self.st["members"][(d.year, d.month)]
        base = S.daily_block(self.st, d, before, mm)
        st2 = dict(self.st)
        future = self.st["daily"].filter(pl.col("event_at") >= day_bounds(d)[0])
        st2["daily"] = pl.concat([self.st["daily"].filter(pl.col("event_at") < day_bounds(d)[0]),
                                  future.with_columns(pl.col("close") * 5, pl.col("volume") * 7)])
        st2["vix"] = self.st["vix"].with_columns(pl.when(pl.col("trading_date") >= d).then(99.0)
                                                 .otherwise(pl.col("close")).alias("close"))
        fake_split = pl.DataFrame({"action_type": ["forward_splits"], "event_at": [day_bounds(d + dt.timedelta(days=1))[0]],
                                   "symbol": [sorted(mm)[0]], "raw": [json.dumps({"new_rate": 4, "old_rate": 1})]})
        st2["actions"] = pl.concat([self.st["actions"].select("action_type", "event_at", "symbol", "raw"), fake_split])
        pert = S.daily_block(st2, d, before, mm)
        self.assertTrue(base[0].sort("symbol").equals(pert[0].sort("symbol")))
        self.assertEqual(base[1], pert[1])
        self.assertEqual(S.sector_for_month(self.st, d, before, mm),
                         S.sector_for_month(st2, d, before, mm))


@unittest.skipUnless(HAVE_STORE and (ROOT / "screener_snapshots").exists(), "screener output not present")
class LeakageStoredAvailability(unittest.TestCase):
    """Leakage test 2: every stored row's inputs were available at or before its decision time."""

    def test_stored_rows(self):
        bad, n = 0, 0
        for day in sorted(partition_dates(ROOT / "screener_snapshots")):
            lo, hi = day_bounds(day)
            s = read_frame(ROOT / "screener_snapshots", lo, hi, columns=["inputs_available_at"])
            n += s.height
            bad += s.filter(pl.col("inputs_available_at").is_null() | (pl.col("inputs_available_at") > pl.col("event_at"))).height
            f = read_frame(ROOT / "feature_daily", lo, hi, columns=["available_at"])
            bad += f.filter(pl.col("available_at") > pl.col("event_at")).height
        self.assertGreater(n, 0)
        self.assertEqual(bad, 0)


@unittest.skipUnless(HAVE_STORE, "store not present")
class LeakageReplayParity(unittest.TestCase):
    """Leakage test 3: features from the streamed P1 replay equal the batch features on the three P1 days."""

    def test_parity(self):
        st = S.load_static(dt.date(2023, 8, 1), dt.date(2026, 10, 16))
        sessions = st["cal"]["trading_date"].to_list()
        choice = json.loads(S.choice_file().read_text())["choice"] if S.choice_file().exists() else "cs"
        for day in PARITY_DAYS:
            k = sessions.index(day)
            ctx, inp = build_context(st, sessions, k)
            events = replay.HistoricalSource(ROOT).events(day)
            bars, samples, i = {"iex": [], "sip": []}, [], 0
            for t in ctx["times"]:
                t_us = replay.us(t)
                while i < len(events) and events[i].available_at <= t_us:
                    e = events[i]
                    p = json.loads(e.payload)
                    if e.kind == "bar":
                        bars[e.feed].append({"event_at": p["event_at"], "symbol": e.symbol, **{c: p[c] for c in (
                            "open", "high", "low", "close", "volume", "vwap", "trade_count")}})
                    elif e.kind == "quote_sample":
                        samples.append({"event_at": p["event_at"], "symbol": e.symbol, "status": p["status"],
                                        "bid_price": p["bid_price"], "ask_price": p["ask_price"]})
                    i += 1
                streamed = dict(inp)
                streamed.pop("flags")
                streamed["bars"] = {f: self._frame(bars[f], inp["bars"][f]) for f in bars}
                streamed["samples"] = self._samples(samples)
                for variant in F.VARIANTS:
                    batch = F.snapshot_frame(inp, variant, [t], choice)
                    stream = F.snapshot_frame(streamed, variant, [t], choice)
                    ok, why = frames_equal(batch, stream, COMPARED)
                    with self.subTest(day=str(day), t=str(t), variant=variant):
                        self.assertTrue(ok, why)

    @staticmethod
    def _frame(rows, like):
        sch = {"event_at": pl.Int64, "symbol": pl.Utf8, "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
               "close": pl.Float64, "volume": pl.Int64, "vwap": pl.Float64, "trade_count": pl.Int64}
        df = pl.DataFrame(rows, schema=sch)
        return df.with_columns(pl.from_epoch("event_at", time_unit="us").dt.replace_time_zone("UTC"))

    @staticmethod
    def _samples(rows):
        sch = {"event_at": pl.Int64, "symbol": pl.Utf8, "status": pl.Utf8, "bid_price": pl.Float64, "ask_price": pl.Float64}
        df = pl.DataFrame(rows, schema=sch)
        return df.with_columns(pl.from_epoch("event_at", time_unit="us").dt.replace_time_zone("UTC"))


if __name__ == "__main__":
    unittest.main()
