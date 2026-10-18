"""P2 pre-registered screener measurement (ADR 0003 section 9). Run once on committed code.

Reads screener_snapshots, calendar and SIP raw_bars_1m through scalp_eval.data_access (own loaders; R4: imports
nothing from scalp_engine).

    python -m scalp_eval.measure_p2 --commit <git hash> --out <json path>
"""
import argparse
import bisect
import datetime as dt
import hashlib
import json
import os
import random
import statistics
import sys
from pathlib import Path

import polars as pl

from scalp_eval.data_access import ET, day_bounds, partition_dates, read_frame

UTC = dt.timezone.utc
ONE_MIN, HORIZON = dt.timedelta(minutes=1), dt.timedelta(minutes=90)
VARIANTS = ("iex_live", "sip_research")


def data_root():
    return Path(os.environ.get("SCALP_DATA_ROOT") or r"D:\scalp-data")


def seed(d, t, tag):
    return int(hashlib.sha256(f"{d:%Y-%m-%d}|{t.astimezone(ET):%H:%M}|{tag}".encode()).hexdigest(), 16)


def random_draw(d, t, members, k):
    """k symbols uniformly without replacement from the sorted members (ADR 0003 section 9)."""
    return random.Random(seed(d, t, "p2-random-v1")).sample(sorted(members), k)


def atr_matched_draw(d, t, atr_by_symbol, candidates):
    """One member per candidate, uniformly with replacement from the candidate's atr14_pct decile."""
    valid = sorted(((a, s) for s, a in atr_by_symbol.items() if a is not None))
    n = len(valid)
    decile = {s: (10 * i) // n for i, (a, s) in enumerate(valid)}
    pools = {}
    for s, dec in decile.items():
        pools.setdefault(dec, []).append(s)
    rng = random.Random(seed(d, t, "p2-atr-matched-v1"))
    out = []
    for c in sorted(candidates):
        if c not in decile:
            out.append(None)  # candidate without atr14_pct: no match (counted)
            continue
        out.append(rng.choice(sorted(pools[decile[c]])))
    return out


class DayBars:
    """SIP minute bars of one session: decision price and forward ranges per (symbol, t)."""

    def __init__(self, bars, close_at):
        self.close_at = close_at
        self.by = {}
        for (s,), g in bars.sort(["symbol", "event_at"]).partition_by("symbol", as_dict=True, maintain_order=True).items():
            self.by[s] = (g["event_at"].to_list(), g["high"].to_list(), g["low"].to_list(), g["close"].to_list())

    def outcome(self, s, t):
        """(primary range, secondary range) or None when no decision price / no forward bar."""
        if s not in self.by:
            return None
        opens, hi, lo, cl = self.by[s]
        i = bisect.bisect_right(opens, t - ONE_MIN) - 1  # latest bar with open <= t - 1 min
        if i < 0:
            return None
        price = cl[i]
        a = bisect.bisect_left(opens, t + ONE_MIN)
        b1 = bisect.bisect_left(opens, min(t + HORIZON, self.close_at))
        b2 = bisect.bisect_left(opens, self.close_at)
        if b1 <= a:
            return None
        r1 = (max(hi[a:b1]) - min(lo[a:b1])) / price
        r2 = (max(hi[a:b2]) - min(lo[a:b2])) / price
        return r1, r2


def measure(root):
    cal = read_frame(root / "calendar", *_span(partition_dates(root / "calendar")))
    close_at = dict(cal.select("trading_date", "close_at").iter_rows())
    res = {v: {"cand": [], "rand": [], "matched": [], "counts": [], "dropped": {"cand": 0, "rand": 0, "matched": 0,
                                                                                    "no_atr": 0},
               "quarters": {}} for v in VARIANTS}
    days = sorted(partition_dates(root / "screener_snapshots"))
    for d in days:
        lo, hi = day_bounds(d)
        snaps = read_frame(root / "screener_snapshots", lo, hi,
                           columns=["symbol", "variant", "candidate", "atr14_pct"])
        bars = read_frame(root / "raw_bars_1m", lo, hi, columns=["symbol", "feed", "high", "low", "close"]).filter(
            pl.col("feed") == "sip")
        db = DayBars(bars, close_at[d])
        q = f"{d.year}Q{(d.month - 1) // 3 + 1}"
        for (variant, t), g in snaps.partition_by(["variant", "event_at"], as_dict=True, maintain_order=True).items():
            r = res[variant]
            members = g["symbol"].to_list()
            cands = sorted(g.filter(pl.col("candidate"))["symbol"].to_list())
            r["counts"].append(len(cands))
            if not cands:
                continue
            atr = dict(g.select("symbol", "atr14_pct").iter_rows())
            draws = {"cand": cands, "rand": random_draw(d, t, members, len(cands)),
                     "matched": atr_matched_draw(d, t, atr, cands)}
            qq = r["quarters"].setdefault(q, {"cand": [], "rand": []})
            for kind, syms in draws.items():
                for s in syms:
                    if s is None:
                        r["dropped"]["no_atr"] += 1
                        continue
                    o = db.outcome(s, t)
                    if o is None:
                        r["dropped"][kind] += 1
                        continue
                    r[kind].append(o)
                    if kind in qq:
                        qq[kind].append(o[0])
    return summarize(res, days)


def _span(days):
    return day_bounds(min(days))[0], day_bounds(max(days))[1]


def _med(xs):
    return statistics.median(xs) if xs else None


def _ratio(a, b):
    return round(a / b, 4) if a is not None and b else None


def summarize(res, days):
    out = {"sessions": len(days), "first": str(days[0]), "last": str(days[-1]), "variants": {}}
    for v, r in res.items():
        c1, r1, m1 = [x[0] for x in r["cand"]], [x[0] for x in r["rand"]], [x[0] for x in r["matched"]]
        c2, r2, m2 = [x[1] for x in r["cand"]], [x[1] for x in r["rand"]], [x[1] for x in r["matched"]]
        counts = sorted(r["counts"])
        n = len(counts)
        q = lambda p: counts[min(n - 1, int(p * (n - 1) + 0.5))] if n else None
        out["variants"][v] = {
            "R_primary": _ratio(_med(c1), _med(r1)),
            "median_range_candidates": _med(c1), "median_range_random": _med(r1),
            "R_atr_matched": _ratio(_med(c1), _med(m1)), "median_range_atr_matched": _med(m1),
            "R_secondary_to_close": _ratio(_med(c2), _med(r2)),
            "R_atr_matched_secondary": _ratio(_med(c2), _med(m2)),
            "per_quarter": {k: {"R": _ratio(_med(x["cand"]), _med(x["rand"])), "candidates": len(x["cand"])}
                            for k, x in sorted(r["quarters"].items())},
            "pairs": {"candidates": len(c1), "random": len(r1), "atr_matched": len(m1)},
            "dropped": r["dropped"],
            "candidate_count_per_snapshot": {
                "snapshots": n, "zero_candidate_snapshots": sum(1 for x in counts if x == 0),
                "zero_share": round(sum(1 for x in counts if x == 0) / n, 4) if n else None,
                "mean": round(sum(counts) / n, 3) if n else None,
                "p10": q(0.10), "p25": q(0.25), "p50": q(0.50), "p75": q(0.75), "p90": q(0.90), "max": counts[-1] if n else None,
                "total_candidate_rows": sum(counts)},
        }
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scalp_eval.measure_p2")
    ap.add_argument("--commit", required=True, help="git hash of the committed measurement code")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    started = dt.datetime.now(UTC)
    result = measure(data_root())
    result["run"] = {"started_utc": started.isoformat(), "finished_utc": dt.datetime.now(UTC).isoformat(),
                     "commit": a.commit, "spec": "ADR 0003 section 9 (p2-random-v1, p2-atr-matched-v1)"}
    Path(a.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
