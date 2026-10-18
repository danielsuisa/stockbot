"""P1 data-quality report: aggregates only (no price rows), computed through data_access day by day.

    python -m scalp_engine.quality --start 2023-08-01 --end 2026-10-16 --out docs/engine/P1-data-quality.md
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import polars as pl

from scalp_engine import storage, universe
from scalp_engine.data_access import day_bounds, load_holdout, partition_dates, quote_view, read_frame

TABLES = ["calendar", "asset_snapshots", "corporate_actions", "raw_bars_1d", "universe_snapshots", "raw_bars_1m",
          "raw_quote_samples"]
QUANTS = (0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90)


def _q(s):
    s = s.drop_nulls()
    if s.len() == 0:
        return {}
    return {f"p{int(q * 100):02d}": round(float(s.quantile(q, "nearest")), 4) for q in QUANTS} | {
        "min": round(float(s.min()), 4), "mean": round(float(s.mean()), 4), "n": s.len()}


def sealed_partitions(root):
    """Partition dates inside any sealed window, per table: directory metadata only."""
    h = load_holdout()
    out = {}
    for t in TABLES:
        dates = partition_dates(root / t)
        bad = [d for d in dates if h.overlaps(*day_bounds(d))]
        out[t] = {"partitions": len(dates), "first": str(min(dates)) if dates else None,
                  "last": str(max(dates)) if dates else None, "in_sealed_windows": len(bad),
                  "files": sum(len(v) for v in dates.values()),
                  "bytes": sum(f.stat().st_size for v in dates.values() for f in v)}
    return out


def report(start, end):
    root = storage.data_root()
    lo, hi = day_bounds(start)[0], day_bounds(end)[1]
    cal = read_frame(root / "calendar", lo, hi).sort("trading_date").unique("trading_date", keep="last",
                                                                           maintain_order=True)
    uni = read_frame(root / "universe_snapshots", lo, hi)
    if uni.height:
        uni = uni.filter(pl.col("method_version") == universe.METHOD)
    members = {}
    status = {}
    for r in uni.filter(pl.col("universe") == "top500").iter_rows(named=True):
        members.setdefault((r["snapshot_date"].year, r["snapshot_date"].month), set()).add(r["symbol"])
        status[r["symbol"]] = r["status"]
    pool = {}
    try:  # validity windows (delisting / rename dates) of members, to separate expected gaps
        from scalp_engine import ingest
        pool = ingest.candidate_pool(start, end)
    except Exception:  # noqa: BLE001 - the report still runs without them
        pass

    cov_rows, ratio_rows, dup, ooo, nonpos, hl, rows_1m = [], [], {"sip": 0, "iex": 0}, {"sip": 0, "iex": 0}, 0, 0, {"sip": 0, "iex": 0}
    q_status, q_by_year, q_crossed, q_nonpos = {}, {}, 0, 0
    days_missing = {"raw_bars_1m": [], "raw_quote_samples": []}
    for c in cal.iter_rows(named=True):
        d = c["trading_date"]
        dlo, dhi = day_bounds(d)
        bars = read_frame(root / "raw_bars_1m", dlo, dhi)
        if bars.height == 0:
            days_missing["raw_bars_1m"].append(str(d))
            continue
        for feed in ("sip", "iex"):
            b = bars.filter(pl.col("feed") == feed)
            rows_1m[feed] += b.height
            dup[feed] += b.height - b.unique(["source", "feed", "symbol", "event_at"]).height
            prev_sym, prev_t = b["symbol"].shift(1), b["event_at"].shift(1)
            ooo[feed] += b.filter((pl.col("symbol") < prev_sym) | ((pl.col("symbol") == prev_sym) &
                                                                   (pl.col("event_at") <= prev_t))).height
        nonpos += bars.filter((pl.col("open") <= 0) | (pl.col("high") <= 0) | (pl.col("low") <= 0) |
                              (pl.col("close") <= 0)).height
        hl += bars.filter(pl.col("high") < pl.col("low")).height
        mem = members.get((d.year, d.month), set())
        if mem:
            rth = bars.filter((pl.col("event_at") >= c["open_at"]) & (pl.col("event_at") < c["close_at"]) &
                              pl.col("symbol").is_in(sorted(mem)))
            expected = int((c["close_at"] - c["open_at"]).total_seconds() // 60)
            per = rth.group_by(["symbol", "feed"]).agg(pl.len().alias("n"), pl.col("volume").sum().alias("vol"))
            sip = per.filter(pl.col("feed") == "sip").select("symbol", pl.col("n").alias("sip_n"), pl.col("vol").alias("sip_vol"))
            iex = per.filter(pl.col("feed") == "iex").select("symbol", pl.col("vol").alias("iex_vol"))
            base = pl.DataFrame({"symbol": sorted(mem)}).join(sip, on="symbol", how="left").join(iex, on="symbol", how="left")
            for r in base.iter_rows(named=True):
                info = pool.get(r["symbol"], {})
                gone = info.get("valid_until") is not None and d >= info["valid_until"]
                cov_rows.append((str(d), r["symbol"], (r["sip_n"] or 0) / expected, gone, c["early_close"]))
                if r["sip_vol"]:
                    ratio_rows.append((r["iex_vol"] or 0) / r["sip_vol"])
            qs = quote_view(read_frame(root / "raw_quote_samples", dlo, dhi))
            if qs.height == 0:
                days_missing["raw_quote_samples"].append(str(d))
            else:
                dup["quotes"] = dup.get("quotes", 0) + qs.height - qs.unique(["source", "feed", "symbol", "event_at"]).height
                for s, n in qs.group_by("status").len().iter_rows():
                    q_status[s] = q_status.get(s, 0) + n
                    y = q_by_year.setdefault(d.year, {})
                    y[s] = y.get(s, 0) + n
                ok = qs.filter(pl.col("status") == "ok")
                q_crossed += ok.filter(pl.col("bid_price") > pl.col("ask_price")).height
                q_nonpos += ok.filter((pl.col("bid_price") <= 0) | (pl.col("ask_price") <= 0)).height
    cov = pl.DataFrame(cov_rows, schema=["day", "symbol", "coverage", "after_delisting", "early_close"], orient="row")
    live = cov.filter(~pl.col("after_delisting"))
    daily = read_frame(root / "raw_bars_1d", lo, hi, columns=["source", "feed", "symbol"])
    other_dups = {
        "raw_bars_1d": daily.height - daily.unique(["source", "feed", "symbol", "event_at"]).height,
        "calendar": read_frame(root / "calendar", lo, hi).height - cal.height,
        "universe_snapshots": uni.height - uni.unique(["snapshot_date", "symbol", "universe"]).height,
    }
    gone_syms = sorted(s for s, st in status.items() if st in ("delisted", "inactive"))
    renamed_syms = sorted(s for s, st in status.items() if st == "renamed")
    cov_by_sym = live.group_by("symbol").agg(pl.col("coverage").mean().alias("c"), (pl.col("coverage") > 0).sum().alias("days"))
    usable = cov_by_sym.filter(pl.col("symbol").is_in(gone_syms) & (pl.col("days") > 0))
    usable_ren = cov_by_sym.filter(pl.col("symbol").is_in(renamed_syms) & (pl.col("days") > 0))
    return {
        "range": [str(start), str(end)], "sessions": cal.height, "early_closes": int(cal["early_close"].sum()),
        "universe_method": universe.METHOD,
        "member_symbols": len(status), "member_symbol_days": cov.height,
        "member_symbol_days_after_delisting_or_rename": cov.height - live.height,
        "sip_rth_coverage_all_member_days": _q(cov["coverage"]),
        "sip_rth_coverage_excl_after_delisting": _q(live["coverage"]),
        "share_member_days_coverage_ge_99pct": round(float((live["coverage"] >= 0.99).mean()), 4),
        "share_member_days_zero_coverage": round(float((live["coverage"] == 0).mean()), 4),
        "sip_rth_coverage_early_close_days": _q(live.filter(pl.col("early_close"))["coverage"]),
        "rows_raw_bars_1m": rows_1m, "duplicate_keys": {**dup, **other_dups}, "out_of_order_rows": ooo,
        "non_positive_price_rows_1m": nonpos, "high_below_low_rows_1m": hl,
        "quote_sample_status": q_status, "quote_sample_status_by_year": q_by_year,
        "quote_missing_rate_excl_after_close": round(q_status.get("missing", 0) /
                                                     max(1, q_status.get("ok", 0) + q_status.get("missing", 0)), 5),
        "quote_crossed_ok_samples": q_crossed, "quote_non_positive_ok_samples": q_nonpos,
        "iex_sip_rth_volume_ratio": _q(pl.Series(ratio_rows, dtype=pl.Float64)),
        "delisted_or_inactive_members": len(gone_syms), "delisted_members_with_sip_minute_history": usable.height,
        "delisted_members_mean_coverage": round(float(usable["c"].mean()), 4) if usable.height else None,
        "renamed_members": len(renamed_syms), "renamed_members_with_sip_minute_history": usable_ren.height,
        "days_without_partition": {k: len(v) for k, v in days_missing.items()},
        "days_without_partition_first": {k: v[:5] for k, v in days_missing.items()},
        "store": sealed_partitions(root),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scalp_engine.quality")
    ap.add_argument("--start", required=True, type=dt.date.fromisoformat)
    ap.add_argument("--end", required=True, type=dt.date.fromisoformat)
    ap.add_argument("--json", help="write the aggregates as JSON here")
    a = ap.parse_args(argv)
    r = report(a.start, a.end)
    text = json.dumps(r, indent=2, default=str)
    if a.json:
        Path(a.json).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
