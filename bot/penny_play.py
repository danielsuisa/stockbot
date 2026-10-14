"""Penny stocks in play, two entries. Locked 2026-10-14 before any result.

Long only, cash, $460, one name a day. This module does not place or transmit an order.

Screen, all required, known by 09:35 New York. Prior close is at least $0.50 and under $5. The gap is the
09:30 five-minute bar's open divided by that prior close, minus 1, and it has to be at least 10%. Relative
volume is that same five-minute bar's volume divided by the median opening-bar volume of the prior 20
sessions. At least 10 of those 20 sessions must have a bar, and the ratio must be at least 5. Full-day
volume is not used. A headline is required only when Alpaca's news history reaches 2016-01-01; otherwise
the replay says so and runs without it. The window is 09:35 New York on the previous calendar date through
09:35 on the trade date.

Opening-range breakout. The range is 09:30 through 09:34. The first later one-minute bar whose high trades
above that high is the break. If the bar opens above the high, the fill is that open. Otherwise the fill is
the high. The stop is one cent under the opening-range low.

VWAP pullback. From 09:35, the first one-minute close under the session VWAP starts the pullback. Session
VWAP starts at 09:30 and uses each bar's vw, or (high + low + close) / 3 when vw is missing. The first later
close back above VWAP is the signal, and the fill is the next bar's open. The stop is one cent under the
lowest low from the pullback bar through the signal bar.

Both exit at 2R or at the 15:55 close (or the last minute before an early close, when that is earlier). A
bar that reaches the stop and the target exits at the stop. One name a day: the largest gap, then the
symbol. No second name, because the sale settles the next session.

Costs on the $460 whole-share book: $0.0035 a share with a $0.35 minimum, on the entry and on the exit.
Half the SIP spread at the fill minute on the way in and half on the way out when the quote is usable.
Otherwise a 1% round-trip haircut, half on each side. The gate is this $460 book. The same trades with the
commission removed are reported beside it and are not gated.
"""
import statistics

from bot.desk_gate import ACCOUNT, net_r, shares_for, walk

PRICE_LO, PRICE_HI = 0.50, 5.0
GAP_MIN, RVOL_MIN = 0.10, 5.0
RVOL_DAYS, RVOL_MIN_HISTORY = 20, 10
TARGET_R = 2.0
TICK = 0.01


def flat_minute(close_hhmm):
    """15:55, or the minute before an early close when that minute is earlier."""
    h, m = (int(x) for x in close_hhmm.split(":")[:2])
    t = h * 60 + m - 1
    earlier = f"{t // 60:02d}:{t % 60:02d}"
    return earlier if earlier < "15:55" else "15:55"


def gap_ok(prev_close, open_px):
    if not prev_close or not open_px or prev_close <= 0:
        return False
    return PRICE_LO <= prev_close < PRICE_HI and open_px / prev_close - 1 >= GAP_MIN


def rvol_ok(today_vol, prior_vols):
    """`prior_vols` is the prior 20 sessions, a missing bar stored as None."""
    usable = [v for v in prior_vols if v]
    if today_vol is None or today_vol <= 0 or len(usable) < RVOL_MIN_HISTORY:
        return False
    med = statistics.median(usable)
    return med > 0 and today_vol / med >= RVOL_MIN


def _vwap(bars, through):
    num = den = 0.0
    for b in bars:
        if b[0] > through:
            break
        vol = b[5] or 0.0
        if vol <= 0:
            continue
        px = b[6] if len(b) > 6 and b[6] else (b[2] + b[3] + b[4]) / 3
        num += px * vol
        den += vol
    return num / den if den else None


def _stop(level):
    stop = level - TICK
    return stop if stop > 0 else None


def orb_entry(bars, flat_at):
    """The opening-range break, or None. `bars` are regular-session minutes through `flat_at`."""
    ors = [b for b in bars if "09:30" <= b[0] <= "09:34"]
    if not ors or ors[0][0] != "09:30":
        return None
    or_high, or_low = max(b[2] for b in ors), min(b[3] for b in ors)
    stop = _stop(or_low)
    if stop is None:
        return None
    later = [b for b in bars if "09:35" <= b[0] <= flat_at]
    for b in later:
        if b[2] <= or_high:
            continue
        entry = b[1] if b[1] > or_high else or_high
        if entry <= stop:
            return None
        return {"entry": entry, "stop": stop, "fill_minute": b[0], "filled_at_open": b[1] > or_high,
                "or_high": or_high, "or_low": or_low}
    return None


def vwap_entry(bars, flat_at):
    """The first pullback under VWAP and the first close back above it, or None."""
    seq = [b for b in bars if "09:35" <= b[0] <= flat_at]
    dipped = False
    plow = None
    for i, b in enumerate(seq):
        vw = _vwap(bars, b[0])
        if vw is None:
            continue
        if not dipped:
            if b[4] < vw:
                dipped, plow = True, b[3]
            continue
        plow = min(plow, b[3])
        if b[4] <= vw or i + 1 >= len(seq):
            continue
        nxt = seq[i + 1]
        stop = _stop(plow)
        if stop is None or nxt[1] <= stop:
            return None
        return {"entry": nxt[1], "stop": stop, "fill_minute": nxt[0], "filled_at_open": True,
                "pullback_low": plow}
    return None


def _levels(entry, stop):
    risk = entry - stop
    return entry + TARGET_R * risk, entry + risk, entry - TARGET_R * risk


def play(bars, flat_at, kind):
    """One setup -> the prices of the long and of the mirrored short, or None."""
    if not any(b[0] == "09:30" for b in bars):
        return None
    setup = orb_entry(bars, flat_at) if kind == "orb" else vwap_entry(bars, flat_at)
    if not setup:
        return None
    path = [(b[0], b[1], b[2], b[3], b[4]) for b in bars if setup["fill_minute"] <= b[0] <= flat_at]
    if not path:
        return None
    target, short_stop, short_target = _levels(setup["entry"], setup["stop"])
    long = walk(path, setup["stop"], target, "long", setup["filled_at_open"])
    short = walk(path, short_stop, short_target, "short", setup["filled_at_open"])
    if not long or not short:
        return None
    if long[2] < flat_at and long[1] == "time":
        long = (long[0], "last", long[2])
    setup.update(exit=long[0], kind_exit=long[1], exit_minute=long[2],
                 short_exit=short[0], target=target)
    return setup


def score(setup, spread, account=ACCOUNT):
    """Add the $460 share count and both net R figures. None when the account cannot buy one share."""
    shares = shares_for(setup["entry"], account)
    if shares < 1:
        return None
    pnl, r, mode = net_r(setup["entry"], setup["exit"], setup["stop"], shares, spread, "long")
    spnl, sr, _ = net_r(setup["entry"], setup["short_exit"], setup["stop"], shares, spread, "short")
    # Commission-free R, same spread, so the $0.35 minimum is visible beside the gated number.
    bare = r + (2 * max(0.35, 0.0035 * shares)) / (abs(setup["entry"] - setup["stop"]) * shares) if r is not None else None
    out = dict(setup)
    out.update(shares=shares, pnl=pnl, net_r=r, net_r_short=sr, short_pnl=spnl, cost_mode=mode,
               net_r_no_commission=bare)
    return out


def pick(rows):
    """The day's trade: largest gap, then the symbol. `rows` already passed the screen and the setup."""
    if not rows:
        return None
    return min(rows, key=lambda r: (-r["gap"], r["t"]))
