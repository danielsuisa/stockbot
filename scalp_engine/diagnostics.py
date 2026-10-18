"""P3D diagnostics, engine side (ADR 0006): exact-reproduction rescoring of the P3 final models, D1 ranking signal,
D4 barrier geometry, and the signed control recommendation sets for D3. No training: every refit must reproduce the
stored P3 recommendation set of its month (SHA-256 of the canonical payload set), or the run stops.

    python -m scalp_engine.diagnostics rescore --key D:\\scalp-env\\keys\\p3_signing.pem
    python -m scalp_engine.diagnostics d1 --out <json>
    python -m scalp_engine.diagnostics d4 --out <json>
    python -m scalp_engine.diagnostics controls --key D:\\scalp-env\\keys\\p3_signing.pem
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import random
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl
from scipy.stats import spearmanr

from scalp_engine import baseline as B
from scalp_engine import ingest, purge, signing, storage
from scalp_engine.contracts import canonical_json
from scalp_engine.data_access import ET, day_bounds, partition_dates, read_frame

UTC = dt.timezone.utc
WINDOWS = {"W1": (dt.date(2025, 5, 1), dt.date(2025, 10, 31)), "W2": (dt.date(2025, 11, 1), dt.date(2026, 4, 30)),
           "W3": (dt.date(2026, 5, 1), dt.date(2026, 10, 16))}
REC_DIR = lambda: storage.data_root() / "recommendations" / "p3"
CONTROL_DIR = lambda: storage.data_root() / "recommendations" / "p3d"
MODEL_DIR = lambda: storage.data_root() / "models" / "p3"


class ReproductionMismatch(RuntimeError):
    pass


def window_of(d):
    for w, (a, b) in WINDOWS.items():
        if a <= d <= b:
            return w
    return None


def payload_set_hash(payloads):
    """SHA-256 of the canonical JSON of payloads sorted by rec_id (ADR 0006 section 1)."""
    return hashlib.sha256(canonical_json(sorted(payloads, key=lambda p: p["rec_id"]))).hexdigest()


def stored_payloads(fset, H, y, m):
    path = REC_DIR() / f"p3-{fset}-h{H}-{y}-{m:02d}.jsonl"
    return [json.loads(x)["payload"] for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ---------------------------------------------------------------- rescore (exact reproduction)

def rescore(log=ingest.log):
    """Refit each P3 final model from its ledger record, verify the month's recommendation set reproduces exactly,
    save the model and write OOS scores for every test decision point. Raises ReproductionMismatch on any mismatch."""
    finals = [json.loads(x) for x in B.ledger_path().read_text(encoding="utf-8").splitlines()]
    finals = {(r["model"], r["month"]): r for r in finals if r.get("fold") == "final"}
    df = B.load()
    sessions_all = sorted(set(df["decision_date"].to_list()))
    purge_end = df.select(pl.coalesce("label_end_h90", "label_end_h60", "label_end_h30",
                                      pl.col("event_at") + dt.timedelta(minutes=91)))[:, 0].to_list()
    rows_all = list(zip(df["decision_date"].to_list(), df["event_at"].to_list(), purge_end))
    ids = {fs: B.D.feature_ids(fs) for fs in B.SETS}
    MODEL_DIR().mkdir(parents=True, exist_ok=True)
    meta, t0 = [], time.monotonic()
    for (y, m) in B.TEST_MONTHS:
        test_start = dt.date(y, m, 1)
        test_end = min(dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1), B.LAST_SESSION)
        test = df.filter(pl.col("decision_date").is_between(test_start, test_end))
        tr_idx = purge.walkforward_train(rows_all, sessions_all, test.row(0, named=True)["decision_date"], months=18, embargo=1)
        base = df[tr_idx]
        for fset, H in B.MODELS:
            model, month = f"{fset}-h{H}", f"{y}-{m:02d}"
            rec = finals[(model, month)]
            tr = base.filter(pl.col(f"label_h{H}").is_not_null())
            if tr.height != rec["train_rows"]:
                raise ReproductionMismatch(f"{model} {month}: training rows {tr.height} != ledger {rec['train_rows']}")
            cat = [B.SETS[fset].index(c) for c in B.CATEGORICAL]
            final, _, _ = B.fit(B.matrix(tr, fset), tr[f"label_h{H}"].to_numpy(), params={**rec["config"], "_cat": cat},
                                rounds=rec["rounds"])
            p = final.predict(B.matrix(test, fset), num_iteration=rec["rounds"])
            thr = rec["threshold"]
            payloads = []
            for i in np.nonzero(p >= thr)[0]:
                r = B.recommendation(test.row(int(i), named=True), float(p[i]), fset, H, (y, m), ids[fset])
                if r is not None:
                    payloads.append(r)
            got, want = payload_set_hash(payloads), payload_set_hash(stored_payloads(fset, H, y, m))
            if got != want:
                raise ReproductionMismatch(f"{model} {month}: refit recommendation set {got[:16]} != stored {want[:16]}")
            path = MODEL_DIR() / f"p3-{model}-{month}.txt"
            final.save_model(str(path), num_iteration=rec["rounds"])
            meta.append({"model": model, "month": month, "config": rec["config"], "rounds": rec["rounds"],
                         "threshold": thr, "recommendations": len(payloads), "rec_set_sha256": got,
                         "model_file": path.name, "model_sha256": storage.sha256_file(path), "train_rows": tr.height})
            scores = test.select("event_at", "decision_date", "symbol").with_columns(
                pl.lit(model).alias("model"), pl.lit(month).alias("month"), pl.Series("p", p.astype(np.float64)))
            write_scores(scores, model)
            log(f"rescore: {month} {model} reproduced ({len(payloads)} recs, sha {got[:12]}) "
                f"{(time.monotonic() - t0) / 60:.1f} min")
    (MODEL_DIR() / "models_p3_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def write_scores(scores, model):
    now = dt.datetime.now(UTC)
    out = scores.with_columns(pl.lit("none").alias("feed"), pl.lit(f"derived:rescore-{model}").alias("request_id"),
                              pl.lit(now).dt.cast_time_unit("us").alias("ingested_at"), pl.lit("scalp_engine").alias("source"),
                              pl.lit(1, dtype=pl.Int32).alias("schema_version"))
    cols = [c["name"] for c in storage.contract("oos_scores")["columns"]]
    for (d,), g in out.partition_by("decision_date", as_dict=True, maintain_order=True).items():
        storage.write_partition("oos_scores", g.select(cols), part=f"scores-{model}", source="scalp_engine", date=d)


# ---------------------------------------------------------------- D1 and D4

def auc(p, y):
    """Mann-Whitney AUC with average ranks for ties."""
    r = pl.Series(p).rank("average").to_numpy()
    n1 = int(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return None
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def deciles(frame):
    """frame: symbol, event_at, p, label, r sorted any way -> decile 1..10 by p asc (ties by symbol, event_at)."""
    f = frame.sort(["p", "symbol", "event_at"]).with_row_index("rank")
    n = f.height
    return f.with_columns(((10 * pl.col("rank")) // n + 1).cast(pl.Int32).alias("decile"))


def d1_table(frame):
    """AUC, calibration, mean frictionless R per decile and the decile Spearman for one (model, window)."""
    f = deciles(frame)
    t = (f.group_by("decile").agg(pl.len().alias("n"), pl.col("p").mean().alias("mean_p"),
                                  pl.col("label").mean().alias("hit_rate"), pl.col("r").mean().alias("mean_r"))
         .sort("decile"))
    rho = spearmanr(t["decile"].to_numpy(), t["mean_r"].to_numpy()).statistic if t.height >= 2 else None
    return {"n": f.height, "auc": auc(f["p"].to_numpy(), f["label"].to_numpy()),
            "spearman_decile_r": None if rho is None or math.isnan(rho) else float(rho),
            "top_decile_r": float(t["mean_r"][-1]), "bottom_decile_r": float(t["mean_r"][0]),
            "deciles": [{k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.items()} for r in t.iter_rows(named=True)]}


def test_rows():
    root = storage.data_root()
    lo, hi = day_bounds(WINDOWS["W1"][0])[0], day_bounds(WINDOWS["W3"][1])[1]
    cols = ["symbol", "decision_date"] + [f"{k}_h{H}" for H in (30, 60, 90) for k in ("label", "r")]
    return read_frame(root / "decision_points", lo, hi, columns=cols)


def d1():
    root = storage.data_root()
    lo, hi = day_bounds(WINDOWS["W1"][0])[0], day_bounds(WINDOWS["W3"][1])[1]
    pts = test_rows()
    scores = read_frame(root / "oos_scores", lo, hi, columns=["symbol", "model", "p"])
    out = {}
    for fset, H in B.MODELS:
        model = f"{fset}-h{H}"
        s = scores.filter(pl.col("model") == model).join(pts, on=["symbol", "event_at"], how="inner")
        s = s.filter(pl.col(f"label_h{H}").is_not_null()).select(
            "symbol", "event_at", "decision_date", "p", pl.col(f"label_h{H}").alias("label"), pl.col(f"r_h{H}").alias("r"))
        for w, (a, b) in WINDOWS.items():
            out[f"{model}/{w}"] = d1_table(s.filter(pl.col("decision_date").is_between(a, b)))
    return out


def d4():
    pts = test_rows()
    out = {}
    for w, (a, b) in WINDOWS.items():
        x = pts.filter(pl.col("decision_date").is_between(a, b))
        for H in (30, 60, 90):
            y = x.filter(pl.col(f"label_h{H}").is_not_null())
            n = y.height
            tgt = y.filter(pl.col(f"label_h{H}") == 1).height
            stop = y.filter(pl.col(f"r_h{H}") == -1.0).height
            out[f"{w}/H{H}"] = {"n": n, "target_first": round(tgt / n, 4), "stop_first": round(stop / n, 4),
                                "time_exit": round((n - tgt - stop) / n, 4), "mean_r": round(float(y[f"r_h{H}"].mean()), 4)}
    return out


# ---------------------------------------------------------------- D3 control sets

def controls(key_path):
    """Signed control sets (ADR 0006 section 4): random entry per window, and the marketable re-emission."""
    key = signing.load_private_key(key_path)
    CONTROL_DIR().mkdir(parents=True, exist_ok=True)
    df = B.load(dt.date(2025, 5, 1), B.LAST_SESSION)
    ids = B.D.feature_ids("live")
    counts = {w: 0 for w in WINDOWS}
    for path in sorted(REC_DIR().glob("p3-live-h60-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                p = json.loads(line)["payload"]
                counts[window_of(dt.datetime.fromisoformat(p["created_at"].replace("Z", "+00:00")).astimezone(ET).date())] += 1
    out = {"random": {}, "marketable": 0}
    for w, (a, b) in WINDOWS.items():
        pool = df.filter(pl.col("decision_date").is_between(a, b) & pl.col("last_price").is_not_null() &
                         pl.col("spread_nbbo_delayed").is_not_null() & pl.col("sigma_1m_5d").is_not_null()).sort(["event_at", "symbol"])
        rng = random.Random(int(hashlib.sha256(f"{w}|p3d-random-v1".encode()).hexdigest(), 16))
        idx = sorted(rng.sample(range(pool.height), counts[w]))
        path = CONTROL_DIR() / f"p3d-random-h60-{w}.jsonl"
        if path.exists():
            raise FileExistsError(path)
        n = 0
        with open(path, "w", encoding="utf-8") as f:
            for i in idx:
                row = pool.row(i, named=True)
                r = B.recommendation(row, 0.5, "live", 60, (row["decision_date"].year, row["decision_date"].month), ids)
                if r is None:
                    continue
                r["model_version"] = f"p3d-random-h60-{w}"
                r["rec_id"] = r["rec_id"].replace("p3-live", "p3d-rand")
                f.write(json.dumps(signing.sign(r, key), sort_keys=True) + "\n")
                n += 1
        out["random"][w] = {"drawn": counts[w], "pool": pool.height, "written": n}
    for src in sorted(REC_DIR().glob("p3-live-h60-*.jsonl")):
        month = src.stem.split("h60-")[1]
        dst = CONTROL_DIR() / f"p3d-marketable-h60-{month}.jsonl"
        if dst.exists():
            raise FileExistsError(dst)
        with open(dst, "w", encoding="utf-8") as f:
            for line in src.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)["payload"]
                r["model_version"] = f"p3d-marketable-h60-{month}"
                r["rec_id"] = r["rec_id"].replace("p3-live", "p3d-mkt")
                f.write(json.dumps(signing.sign(r, key), sort_keys=True) + "\n")
                out["marketable"] += 1
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scalp_engine.diagnostics")
    ap.add_argument("step", choices=["rescore", "d1", "d4", "controls"])
    ap.add_argument("--key")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    started = dt.datetime.now(UTC).isoformat()
    res = {"rescore": rescore, "d1": d1, "d4": d4, "controls": lambda: controls(a.key)}[a.step]()
    if a.out:
        Path(a.out).write_text(json.dumps({"started_utc": started, "finished_utc": dt.datetime.now(UTC).isoformat(),
                                           "result": res}, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(res if a.step != "d1" else {k: {x: v[x] for x in ("n", "auc", "spearman_decile_r", "top_decile_r",
                                                                         "bottom_decile_r")} for k, v in res.items()},
                     indent=2, default=str)[:6000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
