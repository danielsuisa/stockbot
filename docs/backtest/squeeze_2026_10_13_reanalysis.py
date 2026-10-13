"""Recomputes the numbers of docs/backtest/squeeze-2026-10-13-reanalysis.md from docs/backtest/squeeze-2026-09-27-picks.csv
(the 1,330 out-of-sample picks of the 2026-09-27 squeeze list). Before costs; biased upward by survivorship.

    python docs/backtest/squeeze_2026_10_13_reanalysis.py

A pick with (stop, target): if its dd reaches the stop (dd <= -stop) it takes -stop; else if its maxup reaches the
target (maxup >= target) it takes +target; else its r10. "none" means no such level."""
import csv
import os
import statistics

PAIRS = ((None, 0.5), (0.10, None), (0.10, 0.5), (0.20, 0.5), (0.15, 0.30), (0.08, 0.24))


def load(path):
    with open(path, encoding="utf-8") as fh:
        return [{k: float(r[k]) for k in ("maxup", "dd", "r10")} for r in csv.DictReader(fh)]


def with_levels(p, stop, target):
    if stop is not None and p["dd"] <= -stop:
        return -stop
    if target is not None and p["maxup"] >= target:
        return target
    return p["r10"]


def numbers(picks):
    r = [p["r10"] for p in picks]
    out = {"n": len(r), "mean": statistics.mean(r), "median": statistics.median(r),
           "share_above_zero": sum(1 for v in r if v > 0) / len(r),
           "mean_without_13_largest": statistics.mean(sorted(r)[:-13])}
    out["pairs"] = [(s, t, statistics.mean(with_levels(p, s, t) for p in picks)) for s, t in PAIRS]
    return out


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    got = numbers(load(os.path.join(here, "squeeze-2026-09-27-picks.csv")))
    print(f"n {got['n']}; mean {got['mean']:+.4f}; median {got['median']:+.4f}; "
          f"above zero {got['share_above_zero']:.1%}; mean without the 13 largest {got['mean_without_13_largest']:+.4f}")
    for s, t, v in got["pairs"]:
        print(f"stop {'none' if s is None else s}, target {'none' if t is None else t}: {v:+.4f}")


if __name__ == "__main__":
    main()
