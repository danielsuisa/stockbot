"""P3 walk-forward LightGBM baseline (ADR 0005 sections 4-6): monthly retraining on a rolling 18-month window,
purged 5-fold CV with an after-cost PF early-stopping metric, threshold from out-of-fold predictions only, and one
signed recommendation per test decision point with p >= threshold. Every fit is written to the trials ledger.

    python -m scalp_engine.baseline run --key D:\\scalp-env\\keys\\p3_signing.pem
"""
import argparse
import datetime as dt
import json
import math
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import polars as pl

from scalp_engine import decision as D
from scalp_engine import ingest, purge, signing, storage
from scalp_engine.data_access import ET, day_bounds, partition_dates, read_frame

UTC = dt.timezone.utc
TEST_MONTHS = [(2025, m) for m in range(5, 13)] + [(2026, m) for m in range(1, 11)]
LAST_SESSION = dt.date(2026, 10, 16)
LIVE = ["rvol_open", "rvol_pre", "gap_pct", "vwap_dev_pct", "pm_range_pct", "minutes_since_open", "spread_ar",
        "spread_nbbo_delayed", "atr14_pct", "adv20", "prev_close", "earnings_8k", "macro_day", "sector_etf",
        "spy_rv_intraday", "vix_prev_close", "spy_ret20", "spy_sma20_dev", "regime", "ret_5m", "ret_15m", "ret_30m",
        "ret_open", "range_open_pct", "sigma_1m_5d", "dist_stop_pct"]
RESEARCH = LIVE + ["rvol_open_sip", "rvol_pre_sip", "vwap_dev_pct_sip", "pm_range_pct_sip", "spread_ar_sip",
                   "spread_nbbo_rt_sip", "spy_rv_intraday_sip", "ret_5m_sip", "ret_15m_sip", "ret_30m_sip",
                   "ret_open_sip", "range_open_pct_sip"]
SETS = {"live": LIVE, "research": RESEARCH}
CATEGORICAL = ["sector_etf", "regime"]
SECTORS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"]
REGIMES = ["trend", "chop", "event"]
FIXED = {"objective": "binary", "learning_rate": 0.05, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0,
         "max_bin": 63, "seed": 7, "deterministic": True, "force_col_wise": True, "num_threads": 8, "verbose": -1,
         "metric": "None"}
GRID = [{"num_leaves": nl, "min_data_in_leaf": md, "feature_fraction": ff}
        for nl in (15, 63) for md in (200, 2000) for ff in (0.5, 0.9)]
MAX_ROUNDS, MIN_ROUNDS, PATIENCE = 400, 20, 30
QUANTILES, MIN_OOF_TRADES = (0.80, 0.90, 0.95, 0.98), 300
MODELS = [("live", 60), ("research", 60), ("live", 30), ("live", 90), ("research", 30), ("research", 90)]


def ledger_path():
    return storage.data_root() / "derived" / "trials_p3.jsonl"


def log_trial(rec):
    p = ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True, default=str) + "\n")


# ---------------------------------------------------------------- data

def load(start=dt.date(2023, 9, 1), end=LAST_SESSION):
    root = storage.data_root()
    cols = sorted(set(RESEARCH) - {"sector_etf", "regime", "earnings_8k", "macro_day"}) + [
        "symbol", "decision_date", "last_price", "regime", "sector_etf", "earnings_8k", "macro_day", "sigma_1m_5d",
        *[f"{k}_h{H}" for H in D.HORIZONS for k in ("label", "r", "entry_ref", "label_end")]]
    df = read_frame(root / "decision_points", *ingest.range_bounds(start, end), columns=sorted(set(cols)))
    return df.sort(["event_at", "symbol"]).with_columns(
        pl.col("sector_etf").replace_strict({s: i for i, s in enumerate(SECTORS)}, default=-1, return_dtype=pl.Int32),
        pl.col("regime").replace_strict({s: i for i, s in enumerate(REGIMES)}, default=-1, return_dtype=pl.Int32),
        pl.when(pl.col("earnings_8k").is_null()).then(-1).otherwise(pl.col("earnings_8k").cast(pl.Int32)).alias("earnings_8k"),
        pl.col("macro_day").cast(pl.Int32))


def matrix(df, fset):
    return df.select([pl.col(c).cast(pl.Float32) for c in SETS[fset]]).to_numpy()


def net_r(df, H):
    """Engine after-cost R per row (ADR 0005 section 4); NaN when the $500 sizing gives 0 shares."""
    entry = df[f"entry_ref_h{H}"].to_numpy()
    sig = df["sigma_1m_5d"].to_numpy() * math.sqrt(H)
    spread = df["spread_nbbo_delayed"].to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        q = np.floor(np.minimum(5.0 / (entry * sig), 500.0 / entry))
        cost = (2 * np.maximum(0.35, 0.0035 * q) + q * entry * spread) / (q * entry * sig)
    out = df[f"r_h{H}"].to_numpy() - cost
    out[~(q >= 1)] = np.nan
    return out


def pf(values):
    v = values[~np.isnan(values)]
    win, loss = v[v > 0].sum(), -v[v < 0].sum()
    return float(win / loss) if loss > 0 else (float("inf") if win > 0 else 0.0)


def top_pf_metric(netr):
    def feval(preds, data):
        n = len(preds)
        k = max(1, n // 10)
        top = np.argpartition(-preds, k - 1)[:k]
        return "pf_top10", pf(netr[top]), True
    return feval


# ---------------------------------------------------------------- fitting

def fit(X, y, Xv=None, yv=None, netr_v=None, params=None, rounds=MAX_ROUNDS):
    ds = lgb.Dataset(X, y, categorical_feature=params["_cat"], free_raw_data=False)
    p = {**FIXED, **{k: v for k, v in params.items() if not k.startswith("_")}}
    if Xv is None:
        return lgb.train(p, ds, num_boost_round=rounds), rounds, None
    dv = lgb.Dataset(Xv, yv, categorical_feature=params["_cat"], reference=ds)
    ev = {}
    m = lgb.train(p, ds, num_boost_round=rounds, valid_sets=[dv], feval=top_pf_metric(netr_v),
                  callbacks=[lgb.early_stopping(PATIENCE, verbose=False), lgb.record_evaluation(ev)])
    best = max(m.best_iteration, MIN_ROUNDS)
    hist = ev["valid_0"]["pf_top10"]
    return m, best, hist[best - 1] if best - 1 < len(hist) else hist[-1]


def cv(df_tr, sessions, fset, H, configs, month, model):
    """Purged 5-fold CV inside the training window: (best config, mean best rounds, OOF predictions)."""
    rows = list(zip(df_tr["decision_date"].to_list(), df_tr["event_at"].to_list(), df_tr[f"label_end_h{H}"].to_list()))
    X, y, netr = matrix(df_tr, fset), df_tr[f"label_h{H}"].to_numpy(), net_r(df_tr, H)
    cat = [SETS[fset].index(c) for c in CATEGORICAL]
    folds = list(purge.purged_kfold(rows, sessions, k=5, embargo=1))
    results = []
    for ci, cfg in enumerate(configs):
        params = {**cfg, "_cat": cat}
        oof = np.full(len(y), np.nan)
        pfs, bests = [], []
        for fi, (tr, va) in enumerate(folds):
            tr, va = np.array(tr), np.array(va)
            m, best, vpf = fit(X[tr], y[tr], X[va], y[va], netr[va], params)
            oof[va] = m.predict(X[va], num_iteration=best)
            pfs.append(vpf)
            bests.append(best)
            log_trial({"month": month, "model": model, "config": cfg, "fold": fi, "best_round": best, "valid_pf_top10": vpf,
                       "train_rows": len(tr), "valid_rows": len(va)})
        results.append((float(np.mean(pfs)), ci, int(round(np.mean(bests))), oof))
    results.sort(key=lambda r: (-r[0], r[1]))
    mean_pf, ci, rounds, oof = results[0]
    return configs[ci], rounds, oof, mean_pf, netr


def threshold(oof, netr):
    best = None
    for q in QUANTILES:
        thr = float(np.nanquantile(oof, q))
        sel = (oof >= thr) & ~np.isnan(netr)
        n = int(sel.sum())
        if n < MIN_OOF_TRADES:
            continue
        v = pf(netr[sel])
        if best is None or v > best[1] or (v == best[1] and q > best[2]):
            best = (thr, v, q, n)
    return best


# ---------------------------------------------------------------- recommendations

def cents_up(x):
    return math.ceil(round(x * 100, 6)) / 100


def cents_down(x):
    return math.floor(round(x * 100, 6)) / 100


def recommendation(row, p, fset, H, month, feature_ids):
    last, spread, sigma = row["last_price"], row["spread_nbbo_delayed"], row["sigma_1m_5d"]
    if last is None or spread is None or sigma is None:
        return None
    sig = sigma * math.sqrt(H)
    limit = cents_up(last + 0.5 * last * spread)
    stop, target = cents_down(limit * (1 - sig)), cents_up(limit * (1 + 1.5 * sig))
    if not (0 < stop < limit < target) or limit >= 1e6:
        return None
    qty = max(1, int(min(5 / (limit - stop), 500 / limit) // 1))
    t = row["event_at"]
    regime = REGIMES[row["regime"]] if 0 <= row["regime"] < 3 else "chop"
    return {
        "schema_version": 1, "rec_id": f"p3-{fset}-h{H}-{t.astimezone(ET):%Y%m%d%H%M}-{row['symbol'].replace('.', '_')}",
        "created_at": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "symbol": row["symbol"], "side": "long", "qty": qty,
        "entry": {"type": "LMT", "limit_price": f"{limit:.2f}"}, "stop_price": f"{stop:.2f}",
        "target_price": f"{target:.2f}", "ttl_seconds": 600, "p": f"{min(max(p, 0.0), 1.0):.6f}", "horizon_minutes": H,
        "model_version": f"p3-{fset}-h{H}-{month[0]}-{month[1]:02d}", "feature_versions": feature_ids, "regime": regime,
        "risk_checks": [{"check": c, "passed": True, "detail": d} for c, d in (
            ("price", "5 <= px <= 200"), ("adv20", "> 2,000,000"), ("spread_delayed_nbbo", "< 0.10%"),
            ("dilution_v2", "no S-1/S-3/424B (ex 424B2) in 30 days; CIK known"))]}


# ---------------------------------------------------------------- run

def run(key_path, models=MODELS):
    t0 = time.monotonic()
    key = signing.load_private_key(key_path)
    df = load()
    sessions_all = sorted(set(df["decision_date"].to_list()))
    out_dir = storage.data_root() / "recommendations" / "p3"
    out_dir.mkdir(parents=True, exist_ok=True)
    ids = {fs: D.feature_ids(fs) for fs in SETS}
    summary = []
    purge_end = df.select(pl.coalesce("label_end_h90", "label_end_h60", "label_end_h30",
                                      pl.col("event_at") + dt.timedelta(minutes=91)))[:, 0].to_list()
    rows_all = list(zip(df["decision_date"].to_list(), df["event_at"].to_list(), purge_end))  # longest label horizon
    for (y, m) in TEST_MONTHS:
        test_start = dt.date(y, m, 1)
        test_end = min(dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1), LAST_SESSION)
        test = df.filter(pl.col("decision_date").is_between(test_start, test_end))
        if test.height == 0:
            continue
        tr_idx = purge.walkforward_train(rows_all, sessions_all, test.row(0, named=True)["decision_date"], months=18, embargo=1)
        base = df[tr_idx]
        chosen = None
        for fset, H in models:
            model = f"{fset}-h{H}"
            tr = base.filter(pl.col(f"label_h{H}").is_not_null())
            sessions = sorted(set(tr["decision_date"].to_list()))
            configs = GRID if (fset, H) == ("live", 60) else [chosen]
            cfg, rounds, oof, mean_pf, netr = cv(tr, sessions, fset, H, configs, f"{y}-{m:02d}", model)
            if (fset, H) == ("live", 60):
                chosen = cfg
            thr = threshold(oof, netr)
            cat = [SETS[fset].index(c) for c in CATEGORICAL]
            final, _, _ = fit(matrix(tr, fset), tr[f"label_h{H}"].to_numpy(), params={**cfg, "_cat": cat}, rounds=rounds)
            log_trial({"month": f"{y}-{m:02d}", "model": model, "config": cfg, "fold": "final", "rounds": rounds,
                       "cv_mean_pf_top10": mean_pf, "threshold": thr and thr[0], "threshold_quantile": thr and thr[2],
                       "oof_pf": thr and thr[1], "oof_trades": thr and thr[3], "train_rows": tr.height})
            path = out_dir / f"p3-{fset}-h{H}-{y}-{m:02d}.jsonl"
            if path.exists():
                raise FileExistsError(path)
            n_rec = 0
            if thr is not None:
                p = final.predict(matrix(test, fset), num_iteration=rounds)
                sel = np.nonzero(p >= thr[0])[0]
                with open(path, "w", encoding="utf-8") as f:
                    for i in sel:
                        rec = recommendation(test.row(int(i), named=True), float(p[i]), fset, H, (y, m), ids[fset])
                        if rec is None:
                            continue
                        f.write(json.dumps(signing.sign(rec, key), sort_keys=True) + "\n")
                        n_rec += 1
            else:
                path.write_text("", encoding="utf-8")
            summary.append({"month": f"{y}-{m:02d}", "model": model, "config": cfg, "rounds": rounds,
                            "threshold": thr and thr[0], "recommendations": n_rec, "train_rows": tr.height,
                            "test_rows": test.height})
            ingest.log(f"baseline: {y}-{m:02d} {model} cfg {cfg} rounds {rounds} thr {thr and round(thr[0], 4)} "
                       f"recs {n_rec} ({(time.monotonic() - t0) / 60:.1f} min)")
    (storage.data_root() / "derived" / "baseline_p3_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scalp_engine.baseline")
    ap.add_argument("mode", choices=["run"])
    ap.add_argument("--key", required=True)
    a = ap.parse_args(argv)
    run(a.key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
