"""I1 report: minimum detectable effect for gate (i) (ADR 0008 section 8.5), from event COUNTS and the 20-session
return dispersion of the universe (b) POPULATION in the development window only. No event or control return is
computed, and no event outcome enters the dispersion: every issuer-day within an event's outcome reach is excluded.

Method (fixed before it was run):
1. Population days: development sessions; stocks passing the universe (b) pre-filter (close >= $1, DV20 >= $100K)
   and the close - 5 min spread <= 3% that day. Excluded (orchestrator correction 2026-10-20): every issuer-day in
   [entry - 20, entry + 60] sessions of any cluster or big event of that issuer. This contains [entry, entry + 60]
   as required; the 20 sessions before entry are added because a population day's return runs from d+1 to d+20 and
   would otherwise reach into the event's first sessions. Applied to the drawn stock and to its 5 comparison stocks.
2. Pseudo-abnormal return of (stock, day d): split-adjusted return from the open of session d+1 to the close of
   session d+20, minus the mean of the same return of 5 other population stocks of day d (seeded draw).
3. On a seeded sample of days and stocks: sigma = SD of the pseudo-AR; rho = within-ISO-week correlation, estimated
   as (Var(week means) * m - sigma^2) / (sigma^2 * (m - 1)) from equal-size week groups of m draws.
4. For each window: n events (universe (b) cluster events after the I2 overlap rule at H = 20), m_eff = sum(m_w^2)/n
   over ISO entry weeks, design effect 1 + (m_eff - 1) * rho, SE = sigma * sqrt(DE / n),
   MDE = (t_gate + z_0.80) * SE = (2.0 + 0.8416) * SE = 2.8416 * SE: gate (i) passes when the clustered t >= 2.0, so
   a true effect of 2.8416 SE passes with 80% probability (orchestrator correction 2026-10-20; not 1.645 + 0.842).

    python -m scalp_engine.insider_power
"""
import datetime as dt
import hashlib
import json
import random
import statistics
import sys

import polars as pl

from scalp_engine import insider_data as D
from scalp_engine import insider_prices as P
from scalp_engine import storage

DEV = (dt.date(2022, 2, 1), dt.date(2024, 6, 28))
WINDOWS = {"development": DEV, "confirmation": (dt.date(2024, 7, 1), dt.date(2026, 7, 20)),
           "post_s1": (dt.date(2026, 4, 1), dt.date(2026, 7, 20))}
H, K, WEEK_DRAWS, SEED = 20, 5, 8, "i1-power-v1"
T_GATE, Z_POWER = 2.0, 0.8416   # gate (i): clustered t >= 2.0; z of 80% power
Z = T_GATE + Z_POWER             # 2.8416
REACH_BEFORE, REACH_AFTER = H, 60


def rng():
    return random.Random(int.from_bytes(hashlib.sha256(SEED.encode()).digest()[:8], "big"))


def overlap_first(events, sessions, horizon=H):
    """I2 overlap rule (ADR 0008 section 8.1): per issuer and trigger, keep the first event; drop later ones whose
    entry is within `horizon` sessions of the last kept one."""
    idx = {d: i for i, d in enumerate(sessions)}
    kept, last = [], {}
    for e in sorted(events, key=lambda e: (e["entry_date"], e["accepted_at"])):
        k = (e["cik"], e["trigger"])
        i = idx[dt.date.fromisoformat(e["entry_date"])]
        if k in last and i - last[k] < horizon:
            continue
        last[k] = i
        kept.append(e)
    return kept


def window_counts(events, sessions):
    out = {}
    cl = [e for e in events if e["trigger"] == "cluster" and e.get("universe_b") and not e.get("fpi")]
    for name, (lo, hi) in WINDOWS.items():
        w = [e for e in cl if lo.isoformat() <= e["entry_date"] <= hi.isoformat()]
        w = overlap_first(w, sessions)
        weeks = {}
        for e in w:
            y, wk, _ = dt.date.fromisoformat(e["entry_date"]).isocalendar()
            weeks[(y, wk)] = weeks.get((y, wk), 0) + 1
        n = len(w)
        out[name] = {"n": n, "weeks": len(weeks), "m_eff": (sum(v * v for v in weeks.values()) / n) if n else None}
    return out


def excluded_days(events, sessions):
    """{symbol: set of session indices} in [entry - REACH_BEFORE, entry + REACH_AFTER] of any cluster or big event."""
    idx = {d: i for i, d in enumerate(sessions)}
    out = {}
    for e in events:
        if not e.get("symbol") or not e.get("entry_date"):
            continue
        i = idx[dt.date.fromisoformat(e["entry_date"])]
        out.setdefault(e["symbol"], set()).update(range(max(0, i - REACH_BEFORE), i + REACH_AFTER + 1))
    return out


def population_dispersion(events, sessions):
    lo, hi = DEV
    i0, i1 = sessions.index(min(d for d in sessions if d >= lo)), sessions.index(max(d for d in sessions if d <= hi))
    b = P.bars(sessions[max(0, i0 - 25)], sessions[min(len(sessions) - 1, i1 + H + 1)])
    factors = P.price_factors(P.actions())
    sp = P.spreads(P.CLOSE_RULE, sessions[i0], sessions[i1])
    plan = P.prefilter_plan(b, sessions, factors)
    excl = excluded_days(events, sessions)
    idx = {d: i for i, d in enumerate(sessions)}
    eligible = excluded = 0
    pop_of = {}
    for d in sessions[i0:i1 + 1]:
        cand = [s for s in plan.get(d, ()) if sp.get((s, d), 9) <= P.MAX_SPREAD_B]
        keep = [s for s in cand if idx[d] not in excl.get(s, ())]
        eligible += len(cand)
        excluded += len(cand) - len(keep)
        pop_of[d] = sorted(keep)
    opens, closes = {}, {}
    for (sym,), g in b.partition_by("symbol", as_dict=True).items():
        opens[sym] = dict(zip(g["d"].to_list(), g["open"].to_list()))
        closes[sym] = dict(zip(g["d"].to_list(), g["close"].to_list()))

    def ret(sym, d):
        i = idx[d]
        if i + H >= len(sessions):
            return None
        a, z = sessions[i + 1], sessions[i + H]
        o, c = opens.get(sym, {}).get(a), closes.get(sym, {}).get(z)
        if not o or not c:
            return None
        return c / P.adjust_to(o, a, z, factors.get(sym, ())) - 1  # open put on the share basis of the exit day

    r = rng()
    days = [d for d in sessions[i0:i1 + 1] if d in plan]
    by_week = {}
    for d in days:
        by_week.setdefault(d.isocalendar()[:2], []).append(d)
    ars, week_means = [], []
    for wk, ds in sorted(by_week.items()):
        draws = []
        for _ in range(WEEK_DRAWS):
            d = r.choice(ds)
            pop = pop_of[d]
            if len(pop) < K + 1:
                continue
            s, *ctrl = r.sample(pop, K + 1)
            rs, rc = ret(s, d), [ret(c, d) for c in ctrl]
            rc = [x for x in rc if x is not None]
            if rs is None or not rc or abs(rs) > 5:
                continue
            draws.append(rs - statistics.fmean(rc))
        if len(draws) == WEEK_DRAWS:
            week_means.append(statistics.fmean(draws))
        ars += draws
    sigma = statistics.pstdev(ars)
    vm = statistics.pvariance(week_means)
    rho = max(0.0, (vm * WEEK_DRAWS - sigma ** 2) / (sigma ** 2 * (WEEK_DRAWS - 1)))
    return {"draws": len(ars), "weeks": len(week_means), "sigma": sigma, "rho": rho,
            "stock_days_eligible": eligible, "stock_days_excluded_event_reach": excluded,
            "exclusion": f"[entry - {REACH_BEFORE}, entry + {REACH_AFTER}] sessions, cluster and big events"}


def main():
    sessions = P.session_list()
    ev = json.loads(P.derived("i1_events_flagged.json").read_text(encoding="utf-8"))["events"]
    sic = {r["cik"]: r["annual_form"] for r in D.sic_frame().iter_rows(named=True)}
    for e in ev:
        e["fpi"] = sic.get(e["cik"]) in ("20-F", "40-F")
    ev = [e for e in ev if e.get("entry_date")]
    disp = population_dispersion(ev, sessions)
    counts = window_counts(ev, sessions)
    out = {"population": disp, "multiplier": {"t_gate": T_GATE, "z_power_80": Z_POWER, "mde_over_se": Z}, "windows": {}}
    for name, c in counts.items():
        if not c["n"]:
            out["windows"][name] = {**c, "mde": None}
            continue
        de = 1 + (c["m_eff"] - 1) * disp["rho"]
        se = disp["sigma"] * (de / c["n"]) ** 0.5
        out["windows"][name] = {**c, "design_effect": de, "se": se, "mde": Z * se}
    P.derived("i1_power.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
