"""Throwaway analysis (not product code): is the list's hit rate explained by volatility alone, and does short
interest add anything beyond the volume/momentum trigger? Reuses bot.backtest's cache and bot.squeeze's functions.
Run from the repo root: python <this file> | grep -v "^yahoo " > out.md"""
import bisect
import math
import sys

sys.path.insert(0, ".")
from bot import backtest, common, shorts, squeeze  # noqa: E402

BUCKETS = (0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0)  # annualized 20-session realized vol edges
DOWN = 1 / 1.5 - 1  # symmetric "crash": low <= open / 1.5


def vol20(b, i):
    rets = [math.log(b["c"][k] / b["c"][k - 1]) for k in range(i - 19, i + 1) if b["c"][k - 1] > 0 and b["c"][k] > 0]
    if len(rets) < 15:
        return None
    m = sum(rets) / len(rets)
    return math.sqrt(sum((x - m) ** 2 for x in rets) / (len(rets) - 1) * 252)


def bucket(v):
    return bisect.bisect_right(BUCKETS, v)


def main():
    primary = {t: cik for cik, t in common.cik_tickers().items()}
    dates = [d for d in shorts.settlement_dates() if d >= "2017-12-01"]
    raw = backtest.load_reports(dates, 4)
    cal = backtest.load_bars(["SPY"])["SPY"]["d"]
    reports = [(u, shorts.remap(raw[d], primary)) for d in dates if raw.get(d) and (u := shorts.usable_from(d, cal))]
    shares = shorts.shares_index(backtest.load_frames(), backtest.load_filings())
    syms = sorted({s for _, rep in reports for s in rep if s in primary})
    bars = backtest.load_bars(syms, 8)
    usable = [u for u, _ in reports]
    lists = {
        "selected w0.7/si0.2/rv2": lambda rows: squeeze.top(squeeze.scored(rows, 0.2), 0.7, 2.0),
        "trigger-only (no SI, no rvol gate)": lambda rows: squeeze.top(squeeze.scored(rows, -1.0), 0.0, 0.0),
        "trigger, rvol>=2, SI < 5%": lambda rows: squeeze.top(squeeze.scored([r for r in rows if r["si_pct"] < 0.05], -1.0), 0.0, 2.0),
        "trigger, rvol>=2, SI >= 20%": lambda rows: squeeze.top(squeeze.scored(rows, 0.2), 0.0, 2.0),
    }
    for period, lo, hi in (("in-sample", backtest.START, backtest.SPLIT), ("out-of-sample", backtest.SPLIT, "9999")):
        base = {}  # bucket -> [n, hits, downs]
        picks = {n: [] for n in lists}  # (bucket, hit, down)
        last = {n: {} for n in lists}
        for k, day in enumerate(cal):
            if not (lo <= day < hi) or k < 1 or k + squeeze.WINDOW - 1 >= len(cal):
                continue
            j = bisect.bisect_right(usable, day) - 1
            if j < 0:
                continue
            rows, outs = backtest.day_rows(day, cal[k - 1], cal[k + squeeze.WINDOW - 1], reports[j][1], shares, primary, bars)
            vb = {}
            for r in rows:
                b = bars[r["t"]]
                v = vol20(b, bisect.bisect_left(b["d"], day) - 1)
                if v is None:
                    continue
                vb[r["t"]] = bucket(v)
                o = outs[r["t"]]
                if o is not None:
                    x = base.setdefault(vb[r["t"]], [0, 0, 0])
                    x[0] += 1
                    x[1] += o["hit"]
                    x[2] += o["dd"] <= DOWN
            for name, fn in lists.items():
                for r in fn(rows):
                    o = outs.get(r["t"])
                    if o is None or r["t"] not in vb or k - last[name].get(r["t"], -99) < squeeze.WINDOW:
                        continue
                    last[name][r["t"]] = k
                    picks[name].append((vb[r["t"]], o["hit"], o["dd"] <= DOWN))
        print(f"\n### {period}\n")
        print("| List | Picks | +50% hit rate | Vol-matched expected | Lift vs vol-matched | −33% crash rate | "
              "Vol-matched expected | Lift |")
        print("|---|---:|---:|---:|---:|---:|---:|---:|")
        for name, ps in picks.items():
            n = len(ps)
            if not n:
                print(f"| {name} | 0 | — | — | — | — | — | — |")
                continue
            hr, dr = sum(p[1] for p in ps) / n, sum(p[2] for p in ps) / n
            eh = sum(base[p[0]][1] / base[p[0]][0] for p in ps) / n
            ed = sum(base[p[0]][2] / base[p[0]][0] for p in ps) / n
            print(f"| {name} | {n} | {hr:.1%} | {eh:.1%} | {hr / eh:.2f}× | {dr:.1%} | {ed:.1%} | {dr / ed:.2f}× |")
        print("\n| Vol bucket (annualized) | Stock-days | +50% hit rate | −33% crash rate |\n|---|---:|---:|---:|")
        edges = ("0",) + tuple(f"{e:.0%}" for e in BUCKETS)
        for bk in sorted(base):
            n, h, d = base[bk]
            label = f"{edges[bk]}–{edges[bk + 1]}" if bk + 1 < len(edges) else f"> {edges[bk]}"
            print(f"| {label} | {n} | {h / n:.2%} | {d / n:.2%} |")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
