"""The blind report (docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md 6.4): built by ONE function
from the arrays x (net excess per issued ticket) and c (cost in R), holding only the whitelist. It does not change
when every x_i changes sign or is shifted by a constant, so it cannot reveal the direction or the level of the
results. `write` is the only writer; nothing else of a blind run is written, printed, logged or cached.

Never in it: x-bar, A, A_k, any mean, median, quantile or sum of g, a or x; win rates; exit types; fill counts;
holding lengths; any per-ticket, per-cluster or per-signal result."""
import json
import math

from bot import ensemble_backtest as bt, ensemble_stats as st

BLIND_KEYS = ("firings", "refusals", "issued", "pools_dropped_small", "comparability", "cost_r_percentiles", "n",
              "se_t10", "se_t5", "sd_c", "design_effect", "largest_abs_deviation", "mu_min", "K", "r_s", "w_s", "b_R",
              "check_1", "continue")


def percentiles(vals):
    """Nearest rank (C19): the ceil(q x n)-th smallest, for q = 0.50, 0.75, 0.90."""
    v = sorted(vals)
    pick = lambda q: v[max(0, math.ceil(q * len(v)) - 1)] if v else None  # noqa: E731
    return {"p50": pick(0.50), "p75": pick(0.75), "p90": pick(0.90)}


def check_whitelist(rep):
    extra = set(rep) - set(BLIND_KEYS)
    if extra or tuple(rep) != BLIND_KEYS:
        raise ValueError(f"blind report keys outside the whitelist: {sorted(extra)}")
    return rep


def report(x, c, filled, signal_of, blocks10, counts, K, r_s, w_s):
    """The whole blind report from x and c and the run's counts (6.4's whitelist)."""
    s10, s5 = st.ses(x, blocks10)
    se_max, n = max(s10, s5), len(x)
    xb = st.mean(x)
    mu = st.mu_min(s10, s5)
    b = st.b_allowance(K)
    comp = bt.comparability(counts["quote_refused"], counts["passing_quote_free"], counts["pools_dropped_small"],
                            counts["issued_total"])
    costs = {}
    for ci, f, s in zip(c, filled, signal_of):
        if f:
            costs.setdefault(s, []).append(ci)
    check = st.confirmable(mu, b)
    rep = {"firings": counts["firings"], "refusals": counts["refusals"], "issued": counts["issued"],
           "pools_dropped_small": counts["pools_dropped_small"], "comparability": comp,
           "cost_r_percentiles": {s: percentiles(v) for s, v in sorted(costs.items())},
           "n": n, "se_t10": s10, "se_t5": s5, "sd_c": se_max * math.sqrt(n), "design_effect": st.design_effect(x, se_max),
           "largest_abs_deviation": max(abs(v - xb) for v in x), "mu_min": mu, "K": K, "r_s": r_s, "w_s": w_s,
           "b_R": b, "check_1": check, "continue": check and comp["comparable"]}
    return check_whitelist(rep)


def _round(v):
    if isinstance(v, float):
        return float(f"{v:.6g}")
    if isinstance(v, dict):
        return {k: _round(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_round(x) for x in v]
    return v


def serialize(rep):
    """The report as written: every number to 6 significant digits (the only rounding, in the output)."""
    return json.dumps(_round(check_whitelist(rep)), indent=1, sort_keys=False, ensure_ascii=False) + "\n"


def write(rep, path):
    """The only file a blind run writes."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(serialize(rep))
    return path
