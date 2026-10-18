"""P3D fill variants (ADR 0006 sections 3-4), each removing one P3 rule. S4 is fills.simulate itself (unchanged).

R per trade = (exit - entry) / (limit - stop), per share, gross of commissions."""
import bisect
import datetime as dt

from scalp_eval.costs import order_cost
from scalp_eval.fills import LATENCY, LIQUIDITY_SHARE, ONE_MIN, TICK


def first_bar(opens, t, close_at, expires=None):
    """Index of the first bar opening after t + 3 s (before the close, and before expiry if given), or None."""
    i = bisect.bisect_right(opens, t + LATENCY)
    if i >= len(opens) or opens[i] >= close_at or (expires is not None and opens[i] >= expires):
        return None
    return i


def p3_exit(bars, i, stop, target, horizon, close_at, time_exit_spread):
    """P3 exit rules from fill bar i (fills.simulate): the fill bar checks the stop only; later bars gap / stop /
    strict target; time exit at the close minus time_exit_spread. Returns (reason, exit price, gross exit price)."""
    opens, o, h, lo, c, v = bars
    end = min(opens[i] + dt.timedelta(minutes=horizon), close_at)
    j = bisect.bisect_left(opens, end)
    for k in range(i, j):
        if k > i and o[k] <= stop:
            return "gap", o[k] - TICK, o[k]
        if lo[k] <= stop:
            return "stop", stop - TICK, stop
        if k > i and h[k] > target:
            return "target", target, target
    return "time", c[j - 1] - time_exit_spread, c[j - 1]


def frictionless_exit(bars, i, stop, target, horizon, close_at):
    """S1: label-style exits from bar i: stop at the stop exactly (gaps included), target when high >= target
    including bar i, stop wins a same-bar conflict, time exit at the close. Returns (reason, price, conflict)."""
    opens, o, h, lo, c, v = bars
    end = min(opens[i] + dt.timedelta(minutes=horizon), close_at)
    j = bisect.bisect_left(opens, end)
    for k in range(i, j):
        hit_stop, hit_target = lo[k] <= stop, h[k] >= target
        if hit_stop:
            return "stop", stop, hit_target
        if hit_target:
            return "target", target, False
    return "time", c[j - 1], False


def step_r(step, t, limit, stop, target, ttl_s, horizon, shares, bars, close_at, half_spread_delayed):
    """R of one recommendation at step S3, S2 or S1, or (status, None) when it does not trade."""
    opens, o, h, lo, c, v = bars
    i = first_bar(opens, t, close_at, t + dt.timedelta(seconds=ttl_s))
    if i is None:
        return "expired", None, False
    if shares > LIQUIDITY_SHARE * v[i]:
        return "rejected", None, False
    risk = limit - stop
    if step == "S3":
        entry = limit - half_spread_delayed
        _, _, gross = p3_exit(bars, i, stop, target, horizon, close_at, 0.0)
        return "filled", (gross - entry) / risk, False
    if step == "S2":
        _, _, gross = p3_exit(bars, i, stop, target, horizon, close_at, 0.0)
        return "filled", (gross - o[i]) / risk, False
    if step == "S1":
        _, price, conflict = frictionless_exit(bars, i, stop, target, horizon, close_at)
        return "filled", (price - o[i]) / risk, conflict
    raise ValueError(step)


def marketable(t, limit, stop, target, horizon, shares, bars, close_at, half_spread, day, plan="tiered"):
    """Marketable control (ADR 0006 section 4): buy at the open of the first bar after t + 3 s plus half the real
    spread, no limit condition; P3 exits and costs. Returns (status, gross pnl, net pnl, risk)."""
    opens, o, h, lo, c, v = bars
    i = first_bar(opens, t, close_at)
    if i is None:
        return "no_bar", None, None, None
    if shares <= 0:
        return "skipped", None, None, None
    if shares > LIQUIDITY_SHARE * v[i]:
        return "rejected", None, None, None
    entry_net, entry_gross = o[i] + half_spread, o[i]
    reason, price, gross = p3_exit(bars, i, stop, target, horizon, close_at, half_spread)
    cost = order_cost(plan, "buy", shares, entry_net, day) + order_cost(plan, "sell", shares, price, day)
    return "filled", shares * (gross - entry_gross), shares * (price - entry_net) - cost, shares * (limit - stop)
