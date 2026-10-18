"""P3 evaluation (ADR 0005 sections 7-10). Run once on committed code.

Inputs: signed recommendations (JSON Lines of envelopes) under <data root>/recommendations/p3, verified with the
engine's PUBLIC key only; SIP bars, calendar and quote samples through scalp_eval.data_access. Output: one JSON with
signal-level metrics per (model, window, plan, before/after costs), account-level results and capital sensitivity,
and the gate verdict. R4: imports nothing from scalp_engine.

    python -m scalp_eval.evaluate_p3 --pubkey D:\\scalp-env\\keys\\p3_signing.pub.pem --commit <hash> --out <json>
"""
import argparse
import bisect
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import polars as pl

from scalp_eval import metrics
from scalp_eval.account import CashAccount, Signal
from scalp_eval.data_access import ET, day_bounds, quote_view, read_frame
from scalp_eval.fills import signal_shares, simulate
from scalp_eval.verify import load_public_key, verify

UTC = dt.timezone.utc
WINDOWS = {"W1": (dt.date(2025, 5, 1), dt.date(2025, 10, 31)), "W2": (dt.date(2025, 11, 1), dt.date(2026, 4, 30)),
           "W3": (dt.date(2026, 5, 1), dt.date(2026, 10, 16))}
CAPITAL = (500.0, 2000.0, 10000.0, 25000.0)
GATE_MODEL, GATE_PF, GATE_TRADES = "live-h60", 1.2, 100
NBBO_DELAY = dt.timedelta(minutes=15)


def data_root():
    return Path(os.environ.get("SCALP_DATA_ROOT") or r"D:\scalp-data")


def window_of(day):
    for w, (a, b) in WINDOWS.items():
        if a <= day <= b:
            return w
    return None


def load_recommendations(rec_dir, pubkey):
    """Verify every envelope; returns (payloads, counts). An invalid signature aborts the run."""
    out, n = [], 0
    for path in sorted(Path(rec_dir).glob("*.jsonl")):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    out.append(verify(json.loads(line), [pubkey]))
                    n += 1
    return out, n


def model_of(p):
    _, fs, h, *_ = p["model_version"].split("-")  # p3-<set>-h<H>-<YYYY-MM>
    return f"{fs}-{h}"


def half_spreads(samples, symbol, t):
    """(real half spread at t, delayed half spread at t - 15 min) from 'ok' samples; fallback: latest earlier sample."""
    s = samples.get(symbol)
    if not s:
        return None, None
    times, spreads = s
    i = bisect.bisect_right(times, t) - 1
    j = bisect.bisect_right(times, t - NBBO_DELAY) - 1
    return (spreads[i] / 2 if i >= 0 else None), (spreads[j] / 2 if j >= 0 else None)


def run(rec_dir, pubkey_path):
    root = data_root()
    recs, n_verified = load_recommendations(rec_dir, load_public_key(pubkey_path))
    by_day = {}
    for p in recs:
        t = dt.datetime.fromisoformat(p["created_at"].replace("Z", "+00:00"))
        d = t.astimezone(ET).date()
        if window_of(d):
            by_day.setdefault(d, []).append((t, p))
    days = sorted(by_day)
    cal = read_frame(root / "calendar", day_bounds(days[0])[0], day_bounds(days[-1])[1])
    cal_rows = {r["trading_date"]: r for r in cal.iter_rows(named=True)}
    sessions = sorted(cal_rows)
    models = sorted({model_of(p) for _, ps in by_day.items() for _, p in ps})
    sig_rows = {}  # (model, window, plan, phase) -> list of (exit_at, day, pnl, risk)
    status = {}  # (model, window) -> counts
    accounts = {(m, w, e): CashAccount(e, [s for s in sessions if WINDOWS[w][0] <= s <= WINDOWS[w][1]])
                for m in models for w in WINDOWS for e in CAPITAL if m == GATE_MODEL or e == 500.0}
    missing_spread = 0
    t0 = time.monotonic()
    for k, d in enumerate(days):
        w = window_of(d)
        c = cal_rows[d]
        syms = sorted({p["symbol"] for _, p in by_day[d]})
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
        day_signals = {}
        for t, p in by_day[d]:
            m = model_of(p)
            sym = p["symbol"]
            limit, stop = float(p["entry"]["limit_price"]), float(p["stop_price"])
            target = float(p["target_price"])
            hs, hsd = half_spreads(samples, sym, t)
            if hs is None:
                missing_spread += 1
                hs = hsd = 0.0
            hsd = hs if hsd is None else hsd
            b = bar_of.get(sym, ([], [], [], [], [], []))
            st = status.setdefault((m, w), {"recommendations": 0, "filled": 0, "expired": 0, "rejected": 0, "skipped": 0})
            st["recommendations"] += 1
            tr = simulate(t, limit, stop, target, p["ttl_seconds"], p["horizon_minutes"], signal_shares(500.0, limit, stop),
                          b, c["close_at"], hs, hsd, d)
            st[tr.status] += 1
            if tr.status == "filled":
                for plan in ("tiered", "fixed"):
                    sig_rows.setdefault((m, w, plan, "after"), []).append((tr.exit_at, d, tr.pnl[plan], tr.risk))
                sig_rows.setdefault((m, w, "any", "before"), []).append((tr.exit_at, d, tr.pnl_gross, tr.risk))
            day_signals.setdefault(m, []).append(Signal(t, d, sym, float(p["p"]), limit, stop, target, p["ttl_seconds"],
                                                        p["horizon_minutes"], b, c["close_at"], hs, hsd))
        for (m, ww, e), acc in accounts.items():
            if ww == w:
                acc.on_session(d, day_signals.get(m, []))
        if (k + 1) % 20 == 0:
            print(f"evaluate: {d} ({k + 1}/{len(days)}), {(time.monotonic() - t0) / 60:.1f} min", flush=True)
    # accounts: sessions without signals still settle; nothing else to do (orders resolve within a session)
    return summarize(sig_rows, status, accounts, models, n_verified, missing_spread)


def summarize(sig_rows, status, accounts, models, n_verified, missing_spread):
    out = {"verified_recommendations": n_verified, "missing_real_spread": missing_spread, "signal": {}, "account": {}}
    for m in models:
        for w in WINDOWS:
            key = f"{m}/{w}"
            out["signal"][key] = {"counts": status.get((m, w), {}),
                                  "before_costs": metrics.summarize(sig_rows.get((m, w, "any", "before"), []), f"{key}/before"),
                                  "tiered": metrics.summarize(sig_rows.get((m, w, "tiered", "after"), []), f"{key}/tiered"),
                                  "fixed": metrics.summarize(sig_rows.get((m, w, "fixed", "after"), []), f"{key}/fixed")}
    for (m, w, e), acc in accounts.items():
        rows = [(tr.exit_at, s.day, tr.pnl["tiered"], tr.risk) for s, tr in acc.trades if tr.status == "filled"]
        res = metrics.summarize(rows, f"account/{m}/{w}/{int(e)}", equity0=e)
        res.update({"start_equity": e, "final_equity": round(acc.final_equity(), 2),
                    "return_pct": round(100 * (acc.final_equity() / e - 1), 2), "blocked": acc.blocked})
        out["account"][f"{m}/{w}/{int(e)}"] = res
    gate = {}
    for w in WINDOWS:
        r = out["signal"].get(f"{GATE_MODEL}/{w}", {}).get("tiered", {})
        pf, n = r.get("pf"), r.get("trades", 0)
        pf_num = float("inf") if pf == "inf" else (pf or 0.0)
        gate[w] = {"pf_after_costs_tiered": pf, "trades": n, "pass": bool(n >= GATE_TRADES and pf_num > GATE_PF)}
    out["gate"] = {"windows": gate, "pass": all(g["pass"] for g in gate.values())}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scalp_eval.evaluate_p3")
    ap.add_argument("--recs", default=str(data_root() / "recommendations" / "p3"))
    ap.add_argument("--pubkey", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--trials", type=int, help="trials count from the engine's ledger")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    started = dt.datetime.now(UTC)
    res = run(a.recs, a.pubkey)
    res["trials"] = a.trials
    res["run"] = {"started_utc": started.isoformat(), "finished_utc": dt.datetime.now(UTC).isoformat(), "commit": a.commit,
                  "spec": "ADR 0005 sections 7-10"}
    Path(a.out).write_text(json.dumps(res, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(res["gate"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
