"""P3 decision points (ADR 0005 sections 1-3): tradability filters, decision-time features and triple-barrier labels,
materialized only at the 22 decision times per session for tradable members.

    python -m scalp_engine.decision build --start 2023-08-01 --end 2026-10-16

Features reuse the P2 snapshot rows (registry_p2 ids) and add momentum features and the 5-session volatility scale
(registry_p3 ids). Labels look forward on SIP bars by construction; features never do (tests/test_decision.py).
"""
import argparse
import bisect
import collections
import datetime as dt
import hashlib
import inspect
import json
import math
import sys
import time

import polars as pl

from scalp_engine import dilution, ingest, storage, universe
from scalp_engine import features as F
from scalp_engine import proxies
from scalp_engine.contracts import CONTRACTS
from scalp_engine.data_access import ET, day_bounds, partition_dates, read_frame

UTC = dt.timezone.utc
REGISTRY_FILE = CONTRACTS / "features" / "registry_p3.json"
DECISION_TIMES = [dt.time(9 + (45 + 5 * i) // 60, (45 + 5 * i) % 60) for i in range(22)]  # 09:45 .. 11:30 ET
FIRST = dt.date(2023, 9, 1)
SIGMA_SESSIONS, SIGMA_MIN_RETURNS = 5, 500
HORIZONS = (30, 60, 90)
STOP_K, TARGET_K = 1.0, 1.5
ENTRY_LATENCY = dt.timedelta(seconds=3)
RET_LAGS = (5, 15, 30)
SOURCE = "scalp_engine"

P2_LIVE = ["px", "last_price", "prev_close", "adv20", "atr14_pct", "rvol_open", "rvol_pre", "gap_pct", "vwap_dev_pct",
           "pm_range_pct", "minutes_since_open", "spread_ar", "spread_nbbo_delayed", "earnings_8k", "macro_day",
           "sector_etf", "spy_rv_intraday", "vix_prev_close", "spy_ret20", "spy_sma20_dev", "regime",
           "inputs_available_at"]
P2_SIP = ["rvol_open", "rvol_pre", "vwap_dev_pct", "pm_range_pct", "spread_ar", "spread_nbbo_rt", "spy_rv_intraday",
          "inputs_available_at"]
NEW_LIVE = ["ret_5m", "ret_15m", "ret_30m", "ret_open", "range_open_pct"]


# ---------------------------------------------------------------- features (pure, availability-filtered)

def momentum(bars, symbols, times, open_at):
    """ret_5m / ret_15m / ret_30m / ret_open / range_open_pct from bars available at t (open + 62 s), and the
    latest availability used. One feed, one session."""
    b = bars.select("symbol", "event_at", "open", "high", "low", "close").sort(["symbol", "event_at"]).with_columns(
        (pl.col("event_at") + F.BAR_AVAIL).alias("avail"), (pl.col("event_at") >= open_at).alias("rth"))
    rth = b.filter(pl.col("rth")).with_columns(
        pl.col("open").first().over("symbol").alias("open0"),
        pl.col("high").cum_max().over("symbol").alias("hi"), pl.col("low").cum_min().over("symbol").alias("lo"))
    grid = pl.DataFrame({"symbol": [s for s in symbols for _ in times], "t": [t for _ in symbols for t in times]},
                        schema={"symbol": pl.Utf8, "t": pl.Datetime("us", "UTC")}).sort(["symbol", "t"])
    last = b.select("symbol", "avail", pl.col("close").alias("lp")).sort(["symbol", "avail"])
    out = grid.join_asof(last.rename({"avail": "lp_avail"}), left_on="t", right_on="lp_avail", by="symbol",
                         strategy="backward")
    for k in RET_LAGS:
        g = grid.with_columns((pl.col("t") - dt.timedelta(minutes=k)).alias("tk")).sort(["symbol", "tk"])
        lag = g.join_asof(last.rename({"lp": f"lp{k}"}), left_on="tk", right_on="avail", by="symbol",
                          strategy="backward").select("symbol", "t", f"lp{k}")
        out = out.join(lag, on=["symbol", "t"], how="left")
    r = rth.select("symbol", pl.col("avail").alias("r_avail"), "open0", "hi", "lo").sort(["symbol", "r_avail"])
    out = out.sort(["symbol", "t"]).join_asof(r, left_on="t", right_on="r_avail", by="symbol", strategy="backward")
    return out.select(
        "symbol", "t",
        *[(pl.col("lp") / pl.col(f"lp{k}")).log().alias(f"ret_{k}m") for k in RET_LAGS],
        (pl.col("lp") / pl.col("open0")).log().alias("ret_open"),
        ((pl.col("hi") - pl.col("lo")) / pl.col("lp")).alias("range_open_pct"),
        pl.max_horizontal("lp_avail", "r_avail").alias("mom_avail"))


def day_return_stats(sip_bars, open_at, close_at):
    """{symbol: (n, fsum r, fsum r^2)} of 1-minute log returns between consecutive SIP regular-session bars."""
    b = sip_bars.filter((pl.col("event_at") >= open_at) & (pl.col("event_at") < close_at)).sort(["symbol", "event_at"])
    out = {}
    for (s,), g in b.partition_by("symbol", as_dict=True, maintain_order=True).items():
        c = g["close"].to_list()
        r = [math.log(y / x) for x, y in zip(c, c[1:]) if x > 0 and y > 0]
        out[s] = (len(r), math.fsum(r), math.fsum(v * v for v in r))
    return out


def sigma_1m(prior_stats, symbol):
    """sigma_1m_5d: std of 1-minute returns pooled over the prior SIGMA_SESSIONS sessions (sip_eod), or None."""
    n = sum(st[symbol][0] for st in prior_stats if symbol in st)
    if n < SIGMA_MIN_RETURNS:
        return None
    s1 = math.fsum(st[symbol][1] for st in prior_stats if symbol in st)
    s2 = math.fsum(st[symbol][2] for st in prior_stats if symbol in st)
    var = (s2 - s1 * s1 / n) / (n - 1)
    return math.sqrt(var) if var > 0 else None


# ---------------------------------------------------------------- labels (forward-looking by design)

class SipPath:
    """SIP bars of one session per symbol, for triple-barrier labels."""

    def __init__(self, sip_bars, close_at):
        self.close_at = close_at
        self.by = {}
        for (s,), g in sip_bars.sort(["symbol", "event_at"]).partition_by("symbol", as_dict=True, maintain_order=True).items():
            self.by[s] = (g["event_at"].to_list(), g["open"].to_list(), g["high"].to_list(), g["low"].to_list(),
                          g["close"].to_list())

    def label(self, s, t, sigma1, H):
        """(entry, label, gross R, label end) or None; ADR 0005 section 2."""
        if s not in self.by or sigma1 is None:
            return None
        opens, o, hi, lo, cl = self.by[s]
        i = bisect.bisect_right(opens, t + ENTRY_LATENCY)  # first bar opening after t + 3 s
        if i >= len(opens) or opens[i] >= self.close_at:
            return None
        entry = o[i]
        sig = sigma1 * math.sqrt(H)
        stop, target = entry * (1 - STOP_K * sig), entry * (1 + TARGET_K * sig)
        end = min(opens[i] + dt.timedelta(minutes=H), self.close_at)
        j = bisect.bisect_left(opens, end)
        for k in range(i, j):
            if lo[k] <= stop:
                return entry, 0, -STOP_K, end
            if hi[k] >= target:
                return entry, 1, TARGET_K, end
        return entry, 0, (cl[j - 1] - entry) / (entry * sig), end


# ---------------------------------------------------------------- registry (P3 additions)

FEATURES_P3 = {
    "ret_5m": ("1.0.0", (momentum,), "iex", "iex_realtime"), "ret_15m": ("1.0.0", (momentum,), "iex", "iex_realtime"),
    "ret_30m": ("1.0.0", (momentum,), "iex", "iex_realtime"), "ret_open": ("1.0.0", (momentum,), "iex", "iex_realtime"),
    "range_open_pct": ("1.0.0", (momentum,), "iex", "iex_realtime"),
    "ret_5m_sip": ("1.0.0", (momentum,), "sip", "research_only"), "ret_15m_sip": ("1.0.0", (momentum,), "sip", "research_only"),
    "ret_30m_sip": ("1.0.0", (momentum,), "sip", "research_only"), "ret_open_sip": ("1.0.0", (momentum,), "sip", "research_only"),
    "range_open_pct_sip": ("1.0.0", (momentum,), "sip", "research_only"),
    "sigma_1m_5d": ("1.0.0", (day_return_stats, sigma_1m), "sip", "sip_eod"),
    "dist_stop_pct": ("1.0.0", (day_return_stats, sigma_1m), "sip", "sip_eod"),
    "dilution_filing": ("2.0.0", (dilution.dilution_v2,), "none", "public_realtime"),
}


def registry():
    out = []
    for name, (ver, funcs, feed, profile) in FEATURES_P3.items():
        h = hashlib.sha256("\n".join(inspect.getsource(f).replace("\r\n", "\n") for f in funcs).encode()).hexdigest()
        out.append({"schema_version": 2, "name": name, "version": ver, "code_hash": h, "feature_id": f"{name}@{ver}+{h[:12]}",
                    "inputs": ["raw_bars_1m"] if name != "dilution_filing" else ["edgar_filings", "cik_map"],
                    "window": "see ADR 0005 section 3", "feed": feed, "live_profile": profile,
                    "availability_rule": "bar open + 62 s" if profile in ("iex_realtime", "research_only") else
                    ("prior session 20:00 ET + 15 min" if profile == "sip_eod" else "acceptance + 60 s"),
                    "description": f"P3 decision-point feature {name} (ADR 0005)"})
    return out


def write_registry():
    REGISTRY_FILE.write_text(json.dumps(registry(), indent=2) + "\n", encoding="utf-8")


def feature_ids(feature_set):
    p2 = {e["name"]: e["feature_id"] for e in F.registry()}
    p3 = {e["name"]: e["feature_id"] for e in registry()}
    live = [p2[n] for n in ("rvol_open", "rvol_pre", "gap_pct", "vwap_dev_pct", "pm_range_pct", "minutes_since_open",
                            "spread_ar", "spread_nbbo_delayed", "atr14_pct", "adv20", "prev_close", "earnings_8k",
                            "macro_day", "sector_etf", "spy_rv_intraday", "vix_prev_close", "spy_ret20",
                            "spy_sma20_dev", "regime")]
    live += [p3[n] for n in NEW_LIVE + ["sigma_1m_5d", "dist_stop_pct", "dilution_filing"]]
    if feature_set == "research":
        live += [p2["spread_nbbo_rt"]] + [p3[f"{n}_sip"] for n in NEW_LIVE]
    return sorted(set(live))


# ---------------------------------------------------------------- build

def session_rows(d, cal_row, members, snaps, bars, prior_stats, filings, cik_of):
    """Decision-point rows (tradable members only) of one session, with features and labels."""
    times = [F.et(d, ct) for ct in DECISION_TIMES]
    live = snaps.filter(pl.col("variant") == "iex_live").select("event_at", "symbol", *[c for c in P2_LIVE])
    sip = snaps.filter(pl.col("variant") == "sip_research").select(
        "event_at", "symbol", *[pl.col(c).alias(f"{c}_sip") for c in P2_SIP])
    df = live.rename({"event_at": "t"}).join(sip.rename({"event_at": "t"}), on=["t", "symbol"], how="left")
    dil = dilution.dilution_v2(filings, cik_of, sorted(members), times)
    df = df.join(dil, on=["symbol", "t"], how="left")
    df = df.with_columns(
        pl.col("px").is_between(F.MIN_PRICE, F.MAX_PRICE).fill_null(False).alias("pass_price"),
        (pl.col("adv20") > F.MIN_ADV).fill_null(False).alias("pass_adv"),
        (pl.col("spread_nbbo_delayed") < F.MAX_SPREAD).fill_null(False).alias("pass_spread_delayed"))
    df = df.filter(pl.col("pass_price") & pl.col("pass_adv") & pl.col("pass_spread_delayed") & pl.col("pass_dilution_v2"))
    if df.height == 0:
        return None
    syms = sorted(set(df["symbol"].to_list()))
    for feed, suffix in (("iex", ""), ("sip", "_sip")):
        m = momentum(bars.filter(pl.col("feed") == feed), syms, times, cal_row["open_at"])
        m = m.rename({c: f"{c}{suffix}" for c in m.columns if c not in ("symbol", "t")})
        df = df.join(m, on=["symbol", "t"], how="left")
    sig = {s: sigma_1m(prior_stats, s) for s in syms}
    path = SipPath(bars.filter(pl.col("feed") == "sip"), cal_row["close_at"])
    rows = []
    for r in df.select("symbol", "t").iter_rows():
        s1 = sig[r[0]]
        rec = {"symbol": r[0], "t": r[1], "sigma_1m_5d": s1, "dist_stop_pct": s1 * math.sqrt(60) if s1 else None}
        for H in HORIZONS:
            lab = path.label(r[0], r[1], s1, H)
            rec[f"entry_ref_h{H}"] = lab[0] if lab else None
            rec[f"label_h{H}"] = lab[1] if lab else None
            rec[f"r_h{H}"] = lab[2] if lab else None
            rec[f"label_end_h{H}"] = lab[3] if lab else None
        rows.append(rec)
    lab = pl.DataFrame(rows, schema={"symbol": pl.Utf8, "t": pl.Datetime("us", "UTC"), "sigma_1m_5d": pl.Float64,
                                     "dist_stop_pct": pl.Float64,
                                     **{k: v for H in HORIZONS for k, v in (
                                         (f"entry_ref_h{H}", pl.Float64), (f"label_h{H}", pl.Int32),
                                         (f"r_h{H}", pl.Float64), (f"label_end_h{H}", pl.Datetime("us", "UTC")))}})
    df = df.join(lab, on=["symbol", "t"], how="left")
    sigma_avail = cal_row["prev_session_close_at"] + F.EOD_DELAY
    return df.with_columns(
        pl.max_horizontal("inputs_available_at", "inputs_available_at_sip", "mom_avail", "mom_avail_sip",
                          "dilution_avail", pl.lit(sigma_avail).dt.cast_time_unit("us")).alias("features_available_at"),
        pl.lit(d).alias("decision_date"))


def build(start, end, log_every=40):
    root = storage.data_root()
    lo, hi = ingest.range_bounds(start, end)
    cal = read_frame(root / "calendar", lo, hi).sort("trading_date").unique("trading_date", keep="last", maintain_order=True)
    uni = read_frame(root / "universe_snapshots", lo, hi).filter(
        (pl.col("method_version") == universe.METHOD) & (pl.col("universe") == "top500"))
    members = {}
    for r in uni.select("snapshot_date", "symbol").iter_rows():
        members.setdefault((r[0].year, r[0].month), set()).add(r[1])
    cmap = proxies.latest_cik_map()
    cik_of = {s: c for s, c in cmap.select("symbol", "cik").iter_rows() if c}
    filings = read_frame(root / "edgar_filings", *ingest.range_bounds(dt.date(2023, 6, 1), end)).filter(
        pl.col("cik").is_in(sorted(set(cik_of.values()))))
    done = storage.completed("decision_points", part="decision-v1")
    prior = collections.deque(maxlen=SIGMA_SESSIONS)
    rows_total, t0 = 0, time.monotonic()
    sessions = cal.iter_rows(named=True)
    prev_close = None
    for k, c in enumerate(sessions):
        d = c["trading_date"]
        bars = read_frame(root / "raw_bars_1m", *day_bounds(d), require_dates=[d])
        mm = members.get((d.year, d.month))
        key = storage.partition_key("decision_points", source=SOURCE, date=d)
        if d >= FIRST and mm and key not in done and prev_close is not None:
            snaps = read_frame(root / "screener_snapshots", *day_bounds(d), require_dates=[d]).filter(
                pl.col("event_at").is_in([F.et(d, ct) for ct in DECISION_TIMES]))
            fil = filings.filter((pl.col("event_at") >= day_bounds(d)[0] - dt.timedelta(days=31)) &
                                 (pl.col("event_at") < day_bounds(d)[1]))
            c2 = dict(c, prev_session_close_at=prev_close)
            df = session_rows(d, c2, mm, snaps, bars, list(prior), fil, cik_of)
            if df is not None:
                write(d, df)
                rows_total += df.height
        prior.append(day_return_stats(bars.filter(pl.col("feed") == "sip"), c["open_at"], c["close_at"]))
        prev_close = c["session_close_at"]
        if (k + 1) % log_every == 0:
            ingest.log(f"decision: {d} ({k + 1}), rows {rows_total}, {(time.monotonic() - t0) / 60:.1f} min")


def write(d, df):
    now = dt.datetime.now(UTC)
    ids = {fs: feature_ids(fs) for fs in ("live", "research")}
    out = df.rename({"t": "event_at"}).with_columns(
        pl.lit(ids["research"], dtype=pl.List(pl.Utf8)).alias("feature_versions"), pl.lit("mixed").alias("feed"),
        pl.lit("derived:decision-v1").alias("request_id"), pl.lit(now).dt.cast_time_unit("us").alias("ingested_at"),
        pl.lit(SOURCE).alias("source"), pl.lit(1, dtype=pl.Int32).alias("schema_version"))
    cols = [c["name"] for c in storage.contract("decision_points")["columns"]]
    storage.write_partition("decision_points", out.select(cols), part="decision-v1", source=SOURCE, date=d)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scalp_engine.decision")
    ap.add_argument("mode", choices=["build"])
    ap.add_argument("--start", required=True, type=dt.date.fromisoformat)
    ap.add_argument("--end", required=True, type=dt.date.fromisoformat)
    a = ap.parse_args(argv)
    build(a.start, a.end)
    return 0


if __name__ == "__main__":
    sys.exit(main())
