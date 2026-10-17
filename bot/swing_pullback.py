"""Swing pullback in an uptrend. Locked 2026-10-14 before any result.

Long only, cash, $460, one open position. This module does not place or transmit an order.

A name qualifies on the signal day when all of these hold. The close is above $5. Dollar ADV, the mean of
close times volume over the prior 20 sessions, is above $20 million. The close is above the 50-day average,
and that average is above the 50-day average from 10 sessions earlier. The low touches the 20-day or the
50-day average: at or below the average, or within 0.5% above it, and the close finishes back above the
average it touched. The candle closes in the top third of its range and above the prior close.

The entry is the next session's open, and that open has to be above $5 and above the stop. The stop is one
cent under the signal day's low. The target is the highest high of the 20 sessions before the signal day.
There is no trade when that high is not above the entry. The position is marked for 15 sessions starting
with the entry session. A missing bar is a halt and still uses up one of the 15. A bar that reaches the
stop and the target exits at the stop. A gap through either price exits at the open. Whatever is still open
exits at the close of the 15th session. If the bars end first, the exit is the last close.

Earnings dates are not in the Alpaca data this replay uses. Both books are reported and each is gated on
its own. One book does not skip earnings. The proxy book skips an entry whose planned 15 sessions overlap
the ten calendar days that start on the second Monday of January, April, July, or October.

After an exit, the next entry is no earlier than the following session. Sale proceeds are not treated as
settled on the exit day. One position at a time. When two names signal for the same entry day, the higher
prior dollar ADV is taken, then the symbol.

Costs match the penny test: $0.0035 a share, $0.35 minimum, both sides, and a 1% round-trip haircut.
A daily test has no entry quote. The gate is the $460 book.
"""
import datetime as dt

from bot.desk_gate import ACCOUNT, net_r, shares_for, walk

PRICE_MIN = 5.0
ADV_MIN = 20_000_000.0
ADV_DAYS, SMA_FAST, SMA_SLOW, SLOPE_LAG = 20, 20, 50, 10
TOUCH = 0.005
HOLD = 15
TICK = 0.01
TARGET_R = None  # the target is the prior swing high, not a multiple of R


def second_monday(year, month):
    first = dt.date(year, month, 1)
    monday = first + dt.timedelta(days=(0 - first.weekday()) % 7)
    return monday + dt.timedelta(days=7)


def blackout_days(first=2016, last=2026):
    """The proxy earnings window: ten calendar days from each second Monday of Jan, Apr, Jul, Oct."""
    out = set()
    for year in range(first, last + 1):
        for month in (1, 4, 7, 10):
            start = second_monday(year, month)
            for k in range(10):
                out.add((start + dt.timedelta(days=k)).isoformat())
    return out


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def signal(hist, entry_open):
    """`hist` ends on the signal day: (date, o, h, l, c, v). `entry_open` is the next session's open.

    Returns the planned trade's prices, or None. The hold itself is applied by the caller.
    """
    if len(hist) < SMA_SLOW + SLOPE_LAG or entry_open is None or entry_open <= PRICE_MIN:
        return None
    closes = [b[4] for b in hist]
    today, prev = hist[-1], hist[-2]
    if today[2] <= today[3] or today[4] <= PRICE_MIN or today[4] <= prev[4]:
        return None
    sma50 = _mean(closes[-SMA_SLOW:])
    sma50_prev = _mean(closes[-SMA_SLOW - SLOPE_LAG:-SLOPE_LAG])
    sma20 = _mean(closes[-SMA_FAST:])
    if sma50 is None or sma50_prev is None or not (today[4] > sma50 and sma50 > sma50_prev):
        return None
    adv = _mean([b[4] * b[5] for b in hist[-ADV_DAYS - 1:-1]])
    if adv is None or adv <= ADV_MIN:
        return None
    touched = []
    for sma in (sma20, sma50):
        if today[3] <= sma * (1 + TOUCH) and today[4] > sma:
            touched.append(sma)
    if not touched:
        return None
    if (today[4] - today[3]) / (today[2] - today[3]) < 2 / 3:
        return None
    swing = max(b[2] for b in hist[-ADV_DAYS - 1:-1])
    stop = today[3] - TICK
    if stop <= 0 or entry_open <= stop or swing <= entry_open:
        return None
    return {"stop": stop, "target": swing, "entry": entry_open, "adv": adv, "sma20": sma20, "sma50": sma50}


def hold_exit(future, entry, stop, target):
    """`future` is up to 15 sessions starting on the entry day: (date, o, h, l, c) or None for a halt.

    A halt consumes one of the 15. No remaining bar exits at the last close the name printed.
    Reaching the 15th session without a stop or a target exits at the last close in that window.
    """
    last = None
    window = list(future[:HOLD])
    for bar in window:
        if bar is None:
            continue
        last = bar
        hit = walk([(bar[0], bar[1], bar[2], bar[3], bar[4])], stop, target, "long", True)
        if hit and hit[1] != "time":
            return hit[0], hit[1], hit[2]
    if last is None:
        return None
    kind = "time" if len(window) >= HOLD else "last"
    return last[4], kind, last[0]


def short_exit(future, entry, stop, target):
    """Mirrored short: the same risk distance and a 2R target, on the same sessions. `target` is the long's
    swing target and is not the short's target."""
    del target
    risk = entry - stop
    s_stop, s_target = entry + risk, entry - 2 * risk
    last = None
    window = list(future[:HOLD])
    for bar in window:
        if bar is None:
            continue
        last = bar
        hit = walk([(bar[0], bar[1], bar[2], bar[3], bar[4])], s_stop, s_target, "short", True)
        if hit and hit[1] != "time":
            return hit[0]
    return None if last is None else last[4]


def score(entry, exit_px, short_px, stop, spread=None, account=ACCOUNT):
    shares = shares_for(entry, account)
    if shares < 1 or short_px is None:
        return None
    pnl, r, mode = net_r(entry, exit_px, stop, shares, spread, "long")
    spnl, sr, _ = net_r(entry, short_px, stop, shares, spread, "short")
    return {"shares": shares, "pnl": pnl, "net_r": r, "net_r_short": sr, "short_pnl": spnl, "cost_mode": mode}


def overlaps(days, banned):
    return any(d in banned for d in days)


def schedule(candidates, banned, use_proxy):
    """Greedy one-position book. `candidates` have entry_day, exit_day, adv, t, and hold (session dates).

    A proxy book drops a candidate whose planned hold overlaps `banned` before the scheduling, so a skipped
    week does not block the cash. The next entry is the session after the exit day.
    """
    rows = [c for c in candidates if not (use_proxy and overlaps(c["hold"], banned))]
    rows.sort(key=lambda c: (c["entry_day"], -c["adv"], c["t"]))
    taken, free_after = [], ""
    for c in rows:
        if c["entry_day"] <= free_after:
            continue
        # One name per entry day: the first row of a day is the highest ADV, and a taken name blocks the day.
        if taken and taken[-1]["entry_day"] == c["entry_day"]:
            continue
        taken.append(c)
        free_after = c["exit_day"]
    return taken
