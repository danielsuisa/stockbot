"""ZBA v2 closing analysis: pooled X5 trades (development + holdout), spec 14.14.

Usage: python zba-v2-closing-analysis.py <development realistic csv> <holdout realistic csv>
Stdlib only, no network. Reads the saved trade rows; changes nothing.
"""
import csv
import math
import random
import sys
from collections import defaultdict
from datetime import date

GATE_MEAN, GATE_T, GATE_YEARS = 0.20, 2.5, 8  # fixed before the files were opened
CUTS = [0.05, 0.075, 0.10, 0.125, 0.15, 0.20, 0.25]
Z = 1.645 + 0.842  # 5% one-sided, 80% power


def load(path, period):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r["variant"] != "X5" or r["filled"] != "True":
                continue
            R, spread = float(r["R"]), float(r["spread"])
            rows.append({"day": r["day"], "year": int(r["day"][:4]), "period": period,
                         "net": float(r["net_r"]), "gross": float(r["gross_r"]), "cost": float(r["cost_r"]),
                         "spread_r": spread / R, "R": R, "notional": int(r["shares"]) * float(r["fill"])})
    return rows


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def clustered(rows, key="net", boot=0, seed=7):
    """Mean, day-clustered standard error and t; optional bootstrap interval over days."""
    n = len(rows)
    m = mean([r[key] for r in rows])
    days = defaultdict(lambda: [0.0, 0])
    for r in rows:
        d = days[r["day"]]
        d[0] += r[key]
        d[1] += 1
    g = len(days)
    se = math.sqrt(g / (g - 1) * sum((s - m * c) ** 2 for s, c in days.values())) / n
    out = {"n": n, "mean": m, "se": se, "t": m / se}
    if boot:
        rng, vals, means = random.Random(seed), list(days.values()), []
        for _ in range(boot):
            pick = rng.choices(vals, k=g)
            means.append(sum(s for s, _ in pick) / sum(c for _, c in pick))
        means.sort()
        out["lo"], out["hi"] = means[int(0.025 * boot)], means[int(0.975 * boot) - 1]
    return out


def years_positive(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["year"]].append(r["net"])
    return sum(1 for v in by.values() if mean(v) > 0), len(by)


def main(dev_path, holdout_path):
    rows = load(dev_path, "dev") + load(holdout_path, "holdout")
    dev = [r for r in rows if r["period"] == "dev"]
    ho = [r for r in rows if r["period"] == "holdout"]

    print("1. Mean R, day-clustered (95% interval: bootstrap over days)")
    for name, sub in [("development", dev), ("holdout", ho), ("pooled", rows),
                      ("pooled without 2026", [r for r in rows if r["year"] < 2026])]:
        for key in ("net", "gross"):
            c = clustered(sub, key, boot=2000)
            print(f"  {name:20} {key:5} n {c['n']:5}  mean {c['mean']:+.3f}  t {c['t']:+.2f}"
                  f"  [{c['lo']:+.3f}, {c['hi']:+.3f}]")

    print("2. Cost in R: total {:.3f} = spread {:.3f} + commission {:.3f}".format(
        mean([r["cost"] for r in rows]), mean([r["spread_r"] for r in rows]),
        mean([r["cost"] - r["spread_r"] for r in rows])))

    print(f"3. Gate: spread / stop distance <= x; pass = mean >= {GATE_MEAN}, t >= {GATE_T}, "
          f">= {GATE_YEARS} positive years")
    for x in CUTS:
        sub = [r for r in rows if r["spread_r"] <= x + 1e-9]
        c, (pos, tot) = clustered(sub), years_positive(sub)
        ok = c["mean"] >= GATE_MEAN and c["t"] >= GATE_T and pos >= GATE_YEARS
        print(f"  <= {x:.3f}  n {c['n']:5}  net {c['mean']:+.3f}  t {c['t']:+.2f}  years {pos}/{tot}"
              f"  dev {mean([r['net'] for r in sub if r['period'] == 'dev']):+.3f}"
              f"  holdout {mean([r['net'] for r in sub if r['period'] == 'holdout']):+.3f}"
              f"  {'PASS' if ok else 'fail'}")

    print("4. Extra slippage a share, each side")
    for s in (0.005, 0.01):
        c = clustered([dict(r, net=r["net"] - 2 * s / r["R"]) for r in rows])
        print(f"  ${s:.3f}: mean {c['mean']:+.3f}  t {c['t']:+.2f}")

    nets = sorted(r["net"] for r in rows)
    print(f"5. Without the top 1% of trades: {mean(nets[:len(nets) - len(nets) // 100]):+.3f}")

    c = clustered(rows)
    sd = c["se"] * math.sqrt(c["n"])
    naive = math.sqrt(sum((x - c["mean"]) ** 2 for x in nets) / (len(nets) - 1))
    span = (date.fromisoformat(max(r["day"] for r in rows)) - date.fromisoformat(min(r["day"] for r in rows))).days
    per_year = len(rows) / (span / 365.25)
    print(f"6. Power: clustered SD {sd:.2f}R (plain {naive:.2f}R, design effect {(sd / naive) ** 2:.2f}), "
          f"{per_year:.0f} trades a year")
    for mu in (0.20, 0.13, 0.10, 0.06):
        n = (Z * sd / mu) ** 2
        print(f"  true edge +{mu:.2f}R: {n:,.0f} trades, {n / per_year:.1f} years")

    notional = sorted(r["notional"] for r in rows)
    print(f"7. Median position for $100 of risk: ${notional[len(notional) // 2]:,.0f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
