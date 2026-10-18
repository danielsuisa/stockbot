"""L1 pipeline (ADR 0003): loads static inputs, assembles one session's inputs for features.snapshot_frame, keeps the
prior sessions' volume profiles for RVOL, and runs the spread-estimator evaluation that fixes the estimator choice.

    python -m scalp_engine.pipeline spread-eval --start 2023-08-01 --end 2026-10-16

Nothing here derives the end date from the clock; reads go through data_access only.
"""
import argparse
import collections
import datetime as dt
import json
import sys
import time

import polars as pl

from scalp_engine import features as F
from scalp_engine import ingest, proxies, storage, universe
from scalp_engine.data_access import ET, day_bounds, partition_dates, quote_view, read_frame

UTC = dt.timezone.utc
FIRST_UNIVERSE = dt.date(2023, 9, 1)
TRAIN = (dt.date(2023, 9, 1), dt.date(2024, 12, 31))  # ADR 0003 section 3, fixed
TEST = (dt.date(2025, 1, 2), dt.date(2026, 10, 16))
SOURCE = "scalp_engine"


def choice_file():
    return storage.data_root() / "derived" / "spread_choice.json"


def load_static(start, end):
    root = storage.data_root()
    lo, hi = ingest.range_bounds(start, end)
    cal = read_frame(root / "calendar", lo, hi).sort("trading_date").unique("trading_date", keep="last", maintain_order=True)
    plan, _ = ingest.day_plan(start, end)
    uni = read_frame(root / "universe_snapshots", lo, hi).filter(
        (pl.col("method_version") == universe.METHOD) & (pl.col("universe") == "top500"))
    members = {}
    for r in uni.select("snapshot_date", "symbol", "rank").iter_rows():
        members.setdefault((r[0].year, r[0].month), {})[r[1]] = r[2]
    daily = read_frame(root / "raw_bars_1d", lo, hi)
    actions = read_frame(root / "corporate_actions", *ingest.range_bounds(start, end))
    pre_lo = ingest.range_bounds(dt.date(2023, 6, 1), end)
    vix = read_frame(root / "vix_daily", *pre_lo)
    macro = set(read_frame(root / "macro_events", *pre_lo)["event_date"].to_list())
    cmap = proxies.latest_cik_map()
    cik_of = {s: c for s, c in cmap.select("symbol", "cik").iter_rows() if c}
    filings = read_frame(root / "edgar_filings", *pre_lo).filter(pl.col("cik").is_in(sorted(set(cik_of.values()))))
    return {"cal": cal, "plan": plan, "members": members, "daily": daily, "actions": actions, "vix": vix,
            "macro": macro, "cik_of": cik_of, "filings": filings}


def daily_block(st, d, sessions_before, month_members):
    """Daily features of decision day d for its members, market features, and their availability."""
    splits = F.split_factors(st["actions"], d)
    lo = day_bounds(sessions_before[-21])[0] if len(sessions_before) >= 21 else day_bounds(sessions_before[0])[0]
    window = st["daily"].filter((pl.col("event_at") >= lo) & (pl.col("event_at") < day_bounds(d)[0]))
    adj = F.adjusted_daily(window, splits)
    feats = F.daily_window_features(adj.filter(pl.col("symbol").is_in(sorted(month_members))), sessions_before)
    market = F.market_daily(adj, st["vix"], st["macro"], d, sessions_before)
    prev = sessions_before[-1]
    prev_row = st["cal"].filter(pl.col("trading_date") == prev).row(0, named=True)
    daily_avail = prev_row["session_close_at"] + F.EOD_DELAY
    market_avail = max(daily_avail, F.et(prev, dt.time(18, 0)), F.et(d, dt.time(0, 0)))
    return feats, market, daily_avail, market_avail, prev_row["close_at"]


def sector_for_month(st, f, sessions_before, month_members):
    splits = F.split_factors(st["actions"], f)
    days = sessions_before[-61:]
    window = st["daily"].filter((pl.col("event_at") >= day_bounds(days[0])[0]) & (pl.col("event_at") < day_bounds(f)[0]))
    return F.sector_assignment(F.adjusted_daily(window, splits), sorted(month_members), days)


def session_inputs(st, sessions, k, prof, sector_cache):
    """Inputs of snapshot_frame for session sessions[k] (members of its month), or None if not screened."""
    root = storage.data_root()
    d = sessions[k]
    c = st["cal"].filter(pl.col("trading_date") == d).row(0, named=True)
    times = [F.et(d, ct) for ct in F.SNAPSHOT_TIMES]
    bars = read_frame(root / "raw_bars_1m", *day_bounds(d), require_dates=[d])
    tables = {feed: bars.filter(pl.col("feed") == feed) for feed in ("iex", "sip")}
    month_members = st["members"].get((d.year, d.month), {})
    ctx = {"day": d, "cal": c, "times": times, "tables": tables, "fetched": set(st["plan"][d][1])}
    if d < FIRST_UNIVERSE or not month_members:
        return ctx, None
    before = sessions[:k]
    feats, market, daily_avail, market_avail, prev_close_at = daily_block(st, d, before, month_members)
    mkey = (d.year, d.month)
    if mkey not in sector_cache:
        sector_cache[mkey] = sector_for_month(st, d, before, month_members)
    samples = quote_view(read_frame(root / "raw_quote_samples", *day_bounds(d)))
    filings = st["filings"].filter(pl.col("event_at") >= prev_close_at - dt.timedelta(days=31))
    inp = {"day": d, "open_at": c["open_at"], "prev_close_at": prev_close_at, "members": month_members,
           "bars": tables, "samples": samples, "filings": filings,
           "flags": F.filing_flags(filings, st["cik_of"], sorted(month_members), times, prev_close_at),
           "cik_of": st["cik_of"], "daily": feats, "market": market, "sector": sector_cache[mkey],
           "prior_profiles": {f: list(prof[f]) for f in prof}, "daily_avail": daily_avail, "market_avail": market_avail}
    return ctx, inp


def push_profiles(prof, ctx):
    """This session's volume profiles become prior profiles for later sessions."""
    for feed in ("iex", "sip"):
        table = F.bar_table(ctx["tables"][feed], ctx["cal"]["open_at"])
        syms = sorted(set(ctx["tables"][feed]["symbol"].unique().to_list()) | ctx["fetched"])
        prof[feed].append((F.day_profile(table, syms, ctx["times"]), ctx["fetched"]))


def new_profiles():
    return {"iex": collections.deque(maxlen=F.RVOL_SESSIONS), "sip": collections.deque(maxlen=F.RVOL_SESSIONS)}


def run_spread_eval(start, end, log_every=40):
    st = load_static(start, end)
    sessions = st["cal"]["trading_date"].to_list()
    prof, cache, rows = new_profiles(), {}, []
    t0 = time.monotonic()
    for k, d in enumerate(sessions):
        ctx, inp = session_inputs(st, sessions, k, prof, cache)
        if inp is not None:
            sample_t = [F.et(d, ct) for ct in F.SAMPLE_TIMES]
            target = F.nbbo_at(inp["samples"], sorted(inp["members"]), sample_t, dt.timedelta(0)).filter(
                pl.col("s_at") == pl.col("t")).select("symbol", "t", pl.col("nbbo").alias("spread_nbbo_rt"))
            for variant in F.VARIANTS:
                fr = F.snapshot_frame(inp, variant, sample_t, "cs")
                rows.append(fr.select("t", "symbol", "feed", "spread_cs", "spread_ar")
                            .join(target, on=["symbol", "t"], how="left").with_columns(pl.lit(d).alias("day")))
        push_profiles(prof, ctx)
        if (k + 1) % log_every == 0:
            ingest.log(f"spread-eval: {d} ({k + 1}/{len(sessions)}), {(time.monotonic() - t0) / 60:.1f} min")
    finish_spread_eval(pl.concat(rows))


def finish_spread_eval(ev):
    """ADR 0003 section 3: errors in bps vs real-time NBBO; choose on TRAIN by IEX-bar median absolute error."""
    path = storage.data_root() / "derived" / "spread_eval.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    ev.write_parquet(path)
    ok = ev.filter(pl.col("spread_nbbo_rt").is_not_null() & (pl.col("spread_nbbo_rt") > 0) & (pl.col("spread_nbbo_rt") <= 0.05))
    out = {}
    for split, (a, b) in (("train", TRAIN), ("test", TEST)):
        s = ok.filter(pl.col("day").is_between(a, b))
        for feed in ("iex", "sip"):
            f = s.filter(pl.col("feed") == feed)
            for est in ("cs", "ar"):
                g = f.filter(pl.col(f"spread_{est}").is_not_null())
                err = (pl.col(f"spread_{est}") - pl.col("spread_nbbo_rt")) * 1e4
                m = g.select(err.abs().median().alias("mdae_bps"), err.median().alias("median_err_bps"),
                             pl.corr(f"spread_{est}", "spread_nbbo_rt", method="spearman").alias("spearman"),
                             ((pl.col(f"spread_{est}") < F.MAX_SPREAD) == (pl.col("spread_nbbo_rt") < F.MAX_SPREAD)).mean().alias("filter_agreement"),
                             pl.len().alias("n")).row(0, named=True)
                m["coverage"] = g.height / max(1, f.height)
                out[f"{split}/{feed}/{est}"] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()}
    cs, ar = out["train/iex/cs"]["mdae_bps"], out["train/iex/ar"]["mdae_bps"]
    choice = "ar" if ar < cs else "cs"
    rec = {"choice": choice, "rule": "lower train/iex median absolute error (bps); tie -> cs", "metrics": out,
           "train": [str(x) for x in TRAIN], "test": [str(x) for x in TEST], "rows": ev.height}
    choice_file().write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    ingest.log(f"spread-eval: choice {choice}; train iex mdae cs {cs} ar {ar}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scalp_engine.pipeline")
    ap.add_argument("mode", choices=["spread-eval"])
    ap.add_argument("--start", required=True, type=dt.date.fromisoformat)
    ap.add_argument("--end", required=True, type=dt.date.fromisoformat)
    a = ap.parse_args(argv)
    run_spread_eval(a.start, a.end)
    return 0


if __name__ == "__main__":
    sys.exit(main())
