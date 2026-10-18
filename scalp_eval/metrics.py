"""Per-window metrics (ADR 0005 section 9) on filled trades: list of (exit_at, day, pnl, risk)."""
import hashlib
import math
import random
import statistics


def profit_factor(pnls):
    win = math.fsum(p for p in pnls if p > 0)
    loss = -math.fsum(p for p in pnls if p < 0)
    if loss == 0:
        return math.inf if win > 0 else None
    return win / loss


def max_drawdown(pnls_by_time):
    peak = cum = mdd = 0.0
    for p in pnls_by_time:
        cum += p
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return mdd


def sharpe_daily(day_pnls):
    xs = list(day_pnls.values())
    if len(xs) < 2 or statistics.pstdev(xs) == 0:
        return None
    return statistics.mean(xs) / statistics.stdev(xs) * math.sqrt(252)


def bootstrap_pf(pnls, tag, n=2000):
    if not pnls:
        return None, None
    rng = random.Random(int(hashlib.sha256(f"p3-bootstrap-v1|{tag}".encode()).hexdigest(), 16))
    pfs = []
    for _ in range(n):
        s = [pnls[rng.randrange(len(pnls))] for _ in pnls]
        pf = profit_factor(s)
        pfs.append(pf if pf is not None else 0.0)
    pfs.sort()
    return pfs[int(0.025 * n)], pfs[int(0.975 * n) - 1]


def summarize(rows, tag, equity0=500.0):
    """rows: list of (exit_at, day, pnl, risk)."""
    rows = sorted(rows, key=lambda r: r[0])
    pnls = [r[2] for r in rows]
    days = {}
    for r in rows:
        days[r[1]] = days.get(r[1], 0.0) + r[2]
    lo, hi = bootstrap_pf(pnls, tag)
    pf = profit_factor(pnls)
    mdd = max_drawdown(pnls)
    return {
        "trades": len(rows),
        "pf": None if pf is None else (round(pf, 4) if pf != math.inf else "inf"),
        "expectancy_r": round(statistics.mean(r[2] / r[3] for r in rows), 4) if rows else None,
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else None,
        "total_pnl": round(math.fsum(pnls), 2),
        "max_drawdown_usd": round(mdd, 2), "max_drawdown_pct_of_equity0": round(100 * mdd / equity0, 2),
        "sharpe_daily": None if sharpe_daily(days) is None else round(sharpe_daily(days), 3),
        "pf_ci95": [None if lo is None else round(lo, 4), None if hi is None else round(hi, 4)],
    }
