"""L7 fill simulation of one long bracket recommendation on SIP 1-minute bars (ADR 0005 section 7).

Bars: (opens, o, h, l, c, v) lists of one symbol and session, sorted by open time, regular session only."""
import bisect
import datetime as dt
from dataclasses import dataclass, field

from scalp_eval.costs import order_cost

TICK = 0.01
LATENCY = dt.timedelta(seconds=3)
ONE_MIN = dt.timedelta(minutes=1)
LIQUIDITY_SHARE = 0.01


@dataclass
class Trade:
    status: str  # filled | expired | rejected | skipped
    shares: int = 0
    fill_at: object = None
    entry: float = None
    exit_at: object = None
    exit_price: float = None
    exit_reason: str = None  # target | stop | gap | time
    entry_gross: float = None
    exit_gross: float = None
    pnl_gross: float = None
    pnl: dict = field(default_factory=dict)  # plan -> net P&L
    costs: dict = field(default_factory=dict)
    risk: float = None  # shares x (limit - stop)
    expires_at: object = None


def signal_shares(equity, limit, stop):
    """floor(min(1% x equity / (limit - stop), equity / limit)) (ADR 0005 section 8)."""
    if limit <= stop:
        return 0
    return int(min(0.01 * equity / (limit - stop), equity / limit) // 1)


def simulate(t, limit, stop, target, ttl_s, horizon_min, shares, bars, close_at, half_spread, half_spread_delayed, day):
    """Simulate one recommendation decided at t. half_spread: from the real NBBO sample at t (exit costs);
    half_spread_delayed: from the delayed sample (before-cost entry = limit - half_spread_delayed)."""
    expires = t + dt.timedelta(seconds=ttl_s)
    if shares <= 0:
        return Trade("skipped", expires_at=expires)
    opens, o, h, lo, c, v = bars
    i = bisect.bisect_right(opens, t + LATENCY)  # first bar opening after t + 3 s
    fill = None
    while i < len(opens) and opens[i] < expires and opens[i] < close_at:
        if lo[i] < limit:  # strictly below
            fill = i
            break
        i += 1
    if fill is None:
        return Trade("expired", shares=shares, expires_at=expires)
    if shares > LIQUIDITY_SHARE * v[fill]:
        return Trade("rejected", shares=shares, fill_at=opens[fill], expires_at=expires)
    end = min(opens[fill] + dt.timedelta(minutes=horizon_min), close_at)
    j = bisect.bisect_left(opens, end)
    reason = price = gross = when = None
    for k in range(fill, j):
        if k > fill and o[k] <= stop:
            reason, price, gross, when = "gap", o[k] - TICK, o[k], opens[k]
            break
        if lo[k] <= stop:
            reason, price, gross, when = "stop", stop - TICK, stop, opens[k]
            break
        if k > fill and h[k] > target:
            reason, price, gross, when = "target", target, target, opens[k]
            break
    if reason is None:
        last = j - 1
        reason, price, gross, when = "time", c[last] - half_spread, c[last], opens[last]
    exit_at = when + ONE_MIN
    tr = Trade("filled", shares=shares, fill_at=opens[fill], entry=limit, exit_at=exit_at, exit_price=price,
               exit_reason=reason, entry_gross=limit - half_spread_delayed, exit_gross=gross, risk=shares * (limit - stop),
               expires_at=expires)
    tr.pnl_gross = shares * (gross - tr.entry_gross)
    for plan in ("tiered", "fixed"):
        cost = order_cost(plan, "buy", shares, limit, day) + order_cost(plan, "sell", shares, price, day)
        tr.costs[plan] = cost
        tr.pnl[plan] = shares * (price - limit) - cost
    return tr
