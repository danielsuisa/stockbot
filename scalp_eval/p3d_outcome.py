"""ADR 0006 section 6 decision rule, applied to the result files of D1 (engine output JSON), D2 (evaluator output
JSON) and the P3 evaluation. Reads JSON only; shares no code with scalp_engine.

    python -m scalp_eval.p3d_outcome --d1 <json> --d2 <json> --p3 <json> --out <json>
"""
import argparse
import json
import sys
from pathlib import Path

TOP_R, SPEARMAN, AUC, MODEL = 0.10, 0.6, 0.52, "live-h60"


def outcome(d1, d2, p3):
    per = {}
    for w in ("W1", "W2", "W3"):
        x = d1[f"{MODEL}/{w}"]
        signal = (x["top_decile_r"] >= TOP_R and (x["spearman_decile_r"] or -1) >= SPEARMAN and (x["auc"] or 0) >= AUC)
        friction = d2[w]["mean_r"]["S1"] - p3["signal"][f"{MODEL}/{w}"]["tiered"]["expectancy_r"]
        per[w] = {"top_decile_r": x["top_decile_r"], "spearman": x["spearman_decile_r"], "auc": x["auc"],
                  "signal_present": bool(signal), "total_friction_r": round(friction, 4),
                  "top_decile_exceeds_friction": bool(x["top_decile_r"] > friction)}
    n_signal = sum(v["signal_present"] for v in per.values())
    n_econ = sum(v["top_decile_exceeds_friction"] for v in per.values())
    if n_signal < 2:
        result = "A"
    elif n_econ >= 2:
        result = "C"
    else:
        result = "B"
    return {"outcome": result, "windows_with_signal": n_signal, "windows_top_decile_above_friction": n_econ,
            "per_window": per, "rule": "ADR 0006 section 6"}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scalp_eval.p3d_outcome")
    for k in ("d1", "d2", "p3", "out"):
        ap.add_argument(f"--{k}", required=True)
    a = ap.parse_args(argv)
    d1 = json.loads(Path(a.d1).read_text())["result"]
    d2 = json.loads(Path(a.d2).read_text())["d2"]
    p3 = json.loads(Path(a.p3).read_text())
    res = outcome(d1, d2, p3)
    Path(a.out).write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
