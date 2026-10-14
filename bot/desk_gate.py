"""The desk GO gate for the 2026-10-14 penny and swing tests. Locked before either run.

Out of sample must have at least 100 trades, a mean net R above 0 with t > 2, a mean above the 95th
percentile of 20 random-direction means, and a positive mean in 2023 and in 2024 and in 2025 and in 2026.
A year with no trades does not pass. This module does not place or transmit an order.
"""
import random
import statistics

MIN_TRADES, SEEDS = 100, 20
COMMISSION, MIN_ORDER = 0.0035, 0.35
HAIRCUT = 0.01  # round trip when no usable SIP quote: half of this on the way in, half on the way out
ACCOUNT = 460.0
YEARS = ("2023", "2024", "2025", "2026")


def tstat(xs):
    if len(xs) < 2:
        return None
    sd = statistics.stdev(xs)
    if sd == 0:
        return None
    return (sum(xs) / len(xs)) / (sd / len(xs) ** 0.5)


def pctl(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    pos = q * (len(xs) - 1)
    lo = int(pos)
    return xs[lo] if lo + 1 >= len(xs) else xs[lo] + (xs[lo + 1] - xs[lo]) * (pos - lo)


def random_means(trades, seeds=SEEDS):
    """Each seed coin-flips every trade between its long net R and its short net R."""
    out = []
    for seed in range(seeds):
        xs = []
        for x in trades:
            flip = random.Random(f"{seed}|{x['day']}|{x['t']}|{x.get('variant', '')}").random()
            xs.append(x["net_r"] if flip < 0.5 else x["net_r_short"])
        out.append(sum(xs) / len(xs) if xs else 0.0)
    return out


def walk(bars, stop, target, side, filled_at_open):
    """(minute, o, h, l, c) from the fill bar through the time-stop bar.

    A bar that reaches both the stop and the target exits at the stop. When the fill is inside the first
    bar rather than at its open, that open is not an exit (it printed before the fill); the bar's low and
    high still count. The last bar's close is the time stop when neither side was reached.
    """
    if not bars:
        return None
    for i, (minute, o, h, l, c) in enumerate(bars):
        check_open = filled_at_open or i > 0
        if side == "long":
            if check_open and o <= stop:
                return o, "stop", minute
            if check_open and o >= target:
                return o, "target", minute
            hit_stop, hit_target = l <= stop, h >= target
        else:
            if check_open and o >= stop:
                return o, "stop", minute
            if check_open and o <= target:
                return o, "target", minute
            hit_stop, hit_target = h >= stop, l <= target
        if hit_stop:
            return (stop, "stop", minute)
        if hit_target:
            return (target, "target", minute)
    minute, o, h, l, c = bars[-1]
    return c, "time", minute


def shares_for(entry, account=ACCOUNT):
    if entry is None or entry <= 0:
        return 0
    return int(account / entry)


def friction(entry, exit_px, shares, spread):
    """Dollar spread cost and dollar commission. `spread` None uses the 1% round-trip haircut."""
    if spread is None:
        slip = (HAIRCUT / 2) * (entry + exit_px) * shares
        mode = "haircut"
    else:
        slip = max(spread, 0.0) * shares
        mode = "quote"
    fee = 2 * max(MIN_ORDER, COMMISSION * shares)
    return slip, fee, mode


def net_r(entry, exit_px, stop, shares, spread, side):
    """Net R after the spread and both commissions. Risk is |entry - stop| x shares, from the printed prices."""
    slip, fee, mode = friction(entry, exit_px, shares, spread)
    gross = (exit_px - entry) if side == "long" else (entry - exit_px)
    risk = abs(entry - stop) * shares
    pnl = gross * shares - slip - fee
    return pnl, (pnl / risk if risk else None), mode


def judge(trades, years=YEARS):
    """The locked out-of-sample checks. `trades` need day, t, net_r, net_r_short."""
    xs = [x["net_r"] for x in trades]
    m = sum(xs) / len(xs) if xs else None
    t = tstat(xs)
    rm = random_means(trades) if trades else []
    p95 = pctl(rm, 0.95) if rm else None
    by = {}
    for x in trades:
        by.setdefault(x["day"][:4], []).append(x["net_r"])
    year_means = {y: (sum(by[y]) / len(by[y]) if by.get(y) else None) for y in years}
    checks = [
        (f"at least {MIN_TRADES} trades ({len(trades)})", len(trades) >= MIN_TRADES),
        ("mean net R > 0 with t > 2", m is not None and m > 0 and (t or 0) > 2),
        (f"mean net R above the 95th percentile of {SEEDS} random-direction means",
         m is not None and p95 is not None and m > p95),
        ("mean net R > 0 in 2023 and in 2024 and in 2025 and in 2026",
         all(year_means[y] is not None and year_means[y] > 0 for y in years)),
    ]
    return {"n": len(trades), "mean": m, "t": t, "p95": p95, "years": year_means,
            "checks": checks, "go": all(ok for _, ok in checks)}
