"""P3D diagnostics, evaluator side (ADR 0006 sections 3-4): D2 loss decomposition (S4..S1) of the live/H60 P3
recommendations and D3 controls (P3 model, random entry, marketable entry), gross and net at $500 Tiered.
Every envelope is verified with the engine's public key. R4: imports nothing from scalp_engine.

    python -m scalp_eval.diagnose_p3 --pubkey D:\\scalp-env\\keys\\p3_signing.pub.pem --commit <hash> --out <json>
"""
import argparse
import datetime as dt
import json
import math
import sys
import time
from pathlib import Path

import polars as pl

from scalp_eval import fills_variants as FV
from scalp_eval.data_access import ET, day_bounds, quote_view, read_frame
from scalp_eval.evaluate_p3 import WINDOWS, data_root, half_spreads, window_of
from scalp_eval.fills import signal_shares, simulate
from scalp_eval.metrics import profit_factor
from scalp_eval.verify import load_public_key, verify

UTC = dt.timezone.utc


def load(pubkey):
    root = data_root() / "recommendations"
    sets = {"model": sorted((root / "p3").glob("p3-live-h60-*.jsonl")),
            "random": sorted((root / "p3d").glob("p3d-random-h60-*.jsonl")),
            "marketable": sorted((root / "p3d").glob("p3d-marketable-h60-*.jsonl"))}
    by_day, n = {}, 0
    for name, paths in sets.items():
        for path in paths:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                p = verify(json.loads(line), [pubkey])
                n += 1
                t = dt.datetime.fromisoformat(p["created_at"].replace("Z", "+00:00"))
                d = t.astimezone(ET).date()
                if window_of(d):
                    by_day.setdefault(d, []).append((name, t, p))
    return by_day, n


def summary(rows):
    """rows: list of (pnl, risk) -> trades, PF, expectancy R."""
    if not rows:
        return {"trades": 0, "pf": None, "expectancy_r": None}
    pf = profit_factor([x[0] for x in rows])
    return {"trades": len(rows), "pf": None if pf is None else (round(pf, 4) if pf != math.inf else "inf"),
            "expectancy_r": round(sum(x[0] / x[1] for x in rows) / len(rows), 4)}


def run(pubkey_path):
    root = data_root()
    by_day, n_verified = load(load_public_key(pubkey_path))
    days = sorted(by_day)
    cal = read_frame(root / "calendar", day_bounds(days[0])[0], day_bounds(days[-1])[1])
    cal_rows = {r["trading_date"]: r for r in cal.iter_rows(named=True)}
    d2 = {w: {s: [] for s in ("S4", "S3", "S2", "S1")} | {"conflicts": 0, "status": {}} for w in WINDOWS}
    d3 = {(w, k, ph): [] for w in WINDOWS for k in ("model", "random", "marketable") for ph in ("gross", "net")}
    t0 = time.monotonic()
    for k, d in enumerate(days):
        w, c = window_of(d), cal_rows[d]
        syms = sorted({p["symbol"] for _, _, p in by_day[d]})
        bars = read_frame(root / "raw_bars_1m", *day_bounds(d), columns=["symbol", "feed", "open", "high", "low", "close",
                                                                          "volume"], symbols=set(syms))
        bars = bars.filter((pl.col("feed") == "sip") & (pl.col("event_at") >= c["open_at"]) &
                           (pl.col("event_at") < c["close_at"])).sort(["symbol", "event_at"])
        bar_of = {s: (g["event_at"].to_list(), g["open"].to_list(), g["high"].to_list(), g["low"].to_list(),
                      g["close"].to_list(), g["volume"].to_list())
                  for (s,), g in bars.partition_by("symbol", as_dict=True, maintain_order=True).items()}
        qs = quote_view(read_frame(root / "raw_quote_samples", *day_bounds(d))).filter(
            (pl.col("status") == "ok") & pl.col("symbol").is_in(syms)).sort(["symbol", "event_at"])
        samples = {s: (g["event_at"].to_list(), (g["ask_price"] - g["bid_price"]).to_list())
                   for (s,), g in qs.partition_by("symbol", as_dict=True, maintain_order=True).items()}
        for name, t, p in by_day[d]:
            sym = p["symbol"]
            limit, stop, target = float(p["entry"]["limit_price"]), float(p["stop_price"]), float(p["target_price"])
            hs, hsd = half_spreads(samples, sym, t)
            hs = 0.0 if hs is None else hs
            hsd = hs if hsd is None else hsd
            b = bar_of.get(sym, ([], [], [], [], [], []))
            q = signal_shares(500.0, limit, stop)
            risk1 = limit - stop
            if name in ("model", "random"):
                tr = simulate(t, limit, stop, target, p["ttl_seconds"], p["horizon_minutes"], q, b, c["close_at"], hs, hsd, d)
                if tr.status == "filled":
                    d3[(w, name, "gross")].append((tr.pnl_gross, tr.risk))
                    d3[(w, name, "net")].append((tr.pnl["tiered"], tr.risk))
                if name == "model":
                    st = d2[w]["status"]
                    st[tr.status] = st.get(tr.status, 0) + 1
                    if tr.status == "filled":
                        d2[w]["S4"].append((tr.exit_gross - tr.entry_gross) / risk1)
                    for step in ("S3", "S2", "S1"):
                        status, r, conflict = FV.step_r(step, t, limit, stop, target, p["ttl_seconds"], p["horizon_minutes"],
                                                        q, b, c["close_at"], hsd)
                        if r is not None:
                            d2[w][step].append(r)
                            if step == "S1" and conflict:
                                d2[w]["conflicts"] += 1
            else:
                status, gross, net, risk = FV.marketable(t, limit, stop, target, p["horizon_minutes"], q, b, c["close_at"], hs, d)
                if status == "filled":
                    d3[(w, "marketable", "gross")].append((gross, risk))
                    d3[(w, "marketable", "net")].append((net, risk))
        if (k + 1) % 40 == 0:
            print(f"diagnose: {d} ({k + 1}/{len(days)}), {(time.monotonic() - t0) / 60:.1f} min", flush=True)
    out = {"verified": n_verified, "d2": {}, "d3": {}}
    for w in WINDOWS:
        m = {s: (sum(d2[w][s]) / len(d2[w][s]) if d2[w][s] else None) for s in ("S4", "S3", "S2", "S1")}
        out["d2"][w] = {"n": {s: len(d2[w][s]) for s in ("S4", "S3", "S2", "S1")},
                        "mean_r": {s: None if v is None else round(v, 4) for s, v in m.items()},
                        "delta": {"S3-S4 (fill selection)": round(m["S3"] - m["S4"], 4),
                                  "S2-S3 (entry markup)": round(m["S2"] - m["S3"], 4),
                                  "S1-S2 (exit frictions)": round(m["S1"] - m["S2"], 4)},
                        "same_bar_conflicts_S1": d2[w]["conflicts"], "p3_status": d2[w]["status"]}
        for name in ("model", "random", "marketable"):
            out["d3"][f"{name}/{w}"] = {ph: summary(d3[(w, name, ph)]) for ph in ("gross", "net")}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scalp_eval.diagnose_p3")
    ap.add_argument("--pubkey", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    started = dt.datetime.now(UTC)
    res = run(a.pubkey)
    res["run"] = {"started_utc": started.isoformat(), "finished_utc": dt.datetime.now(UTC).isoformat(), "commit": a.commit}
    Path(a.out).write_text(json.dumps(res, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "verified"}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
