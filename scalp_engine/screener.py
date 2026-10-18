"""L2 screener (ADR 0003 section 6): runs both variants over every session from the first universe month and writes
screener_snapshots (filter results, feature values and ids, regime) and feature_daily.

    python -m scalp_engine.screener build --start 2023-08-01 --end 2026-10-16

The filters themselves are computed in features.snapshot_frame from the L2 table constants; the spread estimator is the
one recorded by `python -m scalp_engine.pipeline spread-eval` (ADR 0003 section 3).
"""
import argparse
import datetime as dt
import json
import sys
import time

import polars as pl

from scalp_engine import features as F
from scalp_engine import ingest, storage
from scalp_engine.pipeline import SOURCE, choice_file, load_static, new_profiles, push_profiles, session_inputs

UTC = dt.timezone.utc


def run_build(start, end, log_every=20):
    st = load_static(start, end)
    sessions = st["cal"]["trading_date"].to_list()
    choice = json.loads(choice_file().read_text(encoding="utf-8"))["choice"]
    done = storage.completed("screener_snapshots", part="screener-v1")
    prof, cache = new_profiles(), {}
    t0 = time.monotonic()
    for k, d in enumerate(sessions):
        ctx, inp = session_inputs(st, sessions, k, prof, cache)
        if inp is not None and storage.partition_key("screener_snapshots", source=SOURCE, date=d) not in done:
            parts = [F.snapshot_frame(inp, v, ctx["times"], choice) for v in F.VARIANTS]
            write_snapshots(d, pl.concat(parts), inp["daily"], inp["market"], inp["daily_avail"], inp["market_avail"],
                            inp["members"], inp["sector"])
        push_profiles(prof, ctx)
        if (k + 1) % log_every == 0:
            ingest.log(f"build: {d} ({k + 1}/{len(sessions)}), {(time.monotonic() - t0) / 60:.1f} min")


def write_snapshots(d, df, feats, market, daily_avail, market_avail, members, sector):
    now = dt.datetime.now(UTC)
    out = df.rename({"t": "event_at"}).with_columns(
        pl.lit(d).alias("snapshot_date"),
        pl.col("variant").replace_strict({v: F.feature_ids(v)[0] for v in F.VARIANTS},
                                         return_dtype=pl.List(pl.Utf8)).alias("feature_versions"),
        pl.lit("derived:screener-v1").alias("request_id"), pl.lit(now).dt.cast_time_unit("us").alias("ingested_at"),
        pl.lit(SOURCE).alias("source"), pl.lit(1, dtype=pl.Int32).alias("schema_version"))
    cols = [c["name"] for c in storage.contract("screener_snapshots")["columns"]]
    storage.write_partition("screener_snapshots", out.select(cols), part="screener-v1", source=SOURCE, date=d)
    rows = [{"event_at": F.et(d, dt.time(4)), "available_at": daily_avail, "trading_date": d, "symbol": s,
             "prev_close": r["prev_close"], "adv20": r["adv20"], "atr14_pct": r["atr14_pct"], "sector_etf": sector.get(s),
             "spy_ret20": None, "spy_sma20_dev": None, "vix_prev_close": None, "macro_day": None}
            for s, r in ((r["symbol"], r) for r in feats.iter_rows(named=True))]
    rows.append({"event_at": F.et(d, dt.time(4)), "available_at": market_avail, "trading_date": d, "symbol": "_MARKET",
                 "prev_close": None, "adv20": None, "atr14_pct": None, "sector_etf": None, **market})
    fd = pl.DataFrame(rows, schema={"event_at": pl.Datetime("us", "UTC"), "available_at": pl.Datetime("us", "UTC"),
                                    "trading_date": pl.Date, "symbol": pl.Utf8, "prev_close": pl.Float64,
                                    "adv20": pl.Float64, "atr14_pct": pl.Float64, "sector_etf": pl.Utf8,
                                    "spy_ret20": pl.Float64, "spy_sma20_dev": pl.Float64, "vix_prev_close": pl.Float64,
                                    "macro_day": pl.Boolean})
    ids = sorted(e["feature_id"] for e in F.registry() if e["live_profile"] in ("sip_eod", "public_eod"))
    fd = fd.with_columns(pl.lit(ids, dtype=pl.List(pl.Utf8)).alias("feature_versions"), pl.lit("sip").alias("feed"),
                         pl.lit("derived:feature-daily-v1").alias("request_id"),
                         pl.lit(now).dt.cast_time_unit("us").alias("ingested_at"), pl.lit(SOURCE).alias("source"),
                         pl.lit(1, dtype=pl.Int32).alias("schema_version"))
    cols = [c["name"] for c in storage.contract("feature_daily")["columns"]]
    storage.write_partition("feature_daily", fd.select(cols), part="feature-daily-v1", source=SOURCE, date=d)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scalp_engine.screener")
    ap.add_argument("mode", choices=["build"])
    ap.add_argument("--start", required=True, type=dt.date.fromisoformat)
    ap.add_argument("--end", required=True, type=dt.date.fromisoformat)
    a = ap.parse_args(argv)
    run_build(a.start, a.end)
    return 0


if __name__ == "__main__":
    sys.exit(main())
