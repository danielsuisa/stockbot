"""Noise-area bands and one session of the QQQ/SPY rule. No network and no orders.

The replay in bot.noise_area_backtest is the locked test. This module does not place or transmit an order.
"""
COMMISSION, MIN_ORDER, SLIP = 0.0035, 0.35, 0.01
MULT = 1.0  # band width, fixed. Not a search.
LOOKBACK = 14
NOTIONAL, ACCOUNT = 10_000.0, 460.0
DECISIONS = tuple(f"{t // 60:02d}:{t % 60:02d}" for t in range(10 * 60, 15 * 60 + 31, 30))


def minute_before(hhmm):
    """10:00 -> 09:59. The bar labeled one minute earlier is the one that closes at hhmm."""
    h, m = (int(x) for x in hhmm.split(":")[:2])
    t = h * 60 + m - 1
    return f"{t // 60:02d}:{t % 60:02d}"


def regular_bars(minutes, close_hhmm):
    """09:30 through the last minute before the session close, in order."""
    end = minute_before(close_hhmm)
    return [b for b in minutes if "09:30" <= b[0] <= end]


def trailing_sigma(moves, n=LOOKBACK):
    """Mean absolute move of the last n observations, or None when there are fewer. `moves` are oldest first
    and must already exclude today."""
    if len(moves) < n:
        return None
    window = moves[-n:]
    return sum(abs(x) for x in window) / n


def bands(open_930, prev_close, sigma, mult=MULT):
    """Upper and lower noise bands. Width is mult x sigma, and mult is 1 in the locked test."""
    width = mult * sigma
    return max(open_930, prev_close) * (1 + width), min(open_930, prev_close) * (1 - width)


def session_vwap(bars, through):
    """Volume-weighted price from 09:30 through `through` inclusive. A minute's own vw is used when it is there;
    otherwise (high + low + close) / 3. None when the window has no volume."""
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


def commission(shares):
    return max(MIN_ORDER, shares * COMMISSION)


def _slip(px, side, action):
    """One cent against the trade. Long buys higher and sells lower; short does the opposite."""
    sign = 1 if (side == "long") == (action == "enter") else -1
    return px + sign * SLIP


def simulate_day(bars, prev_close, sigmas, side="both", notional=NOTIONAL):
    """One session -> trades and the day's account return.

    `sigmas` maps a decision time ("10:00" ... "15:30") to that minute's sigma. A missing sigma does not open
    and does not move the stop. The signal is the close of the bar that ends at the decision time. The fill is
    the next bar's open. One entry per side. Long-only ignores the lower band. Whatever is still open exits at
    the session's last close. Returns are pnl / notional, after commission and the one-cent slippage.
    """
    by = {b[0]: b for b in bars}
    first, last = by.get("09:30"), bars[-1] if bars else None
    if not first or not last or not prev_close or prev_close <= 0 or first[1] <= 0:
        return {"trades": [], "daily": 0.0, "benchmark": None, "status": "no open"}
    open_930, last_m = first[1], last[0]
    benchmark = last[4] / open_930 - 1
    position = entry = None
    used, trades = set(), []

    def close_trade(exit_px, exit_minute, kind):
        nonlocal position, entry
        fill = _slip(exit_px, position, "exit")
        shares = entry["shares"]
        gross = (fill - entry["fill"]) * shares if position == "long" else (entry["fill"] - fill) * shares
        pnl = gross - commission(shares) - commission(shares)
        trades.append({"side": position, "entry": entry["fill"], "exit": fill, "entry_minute": entry["minute"],
                       "exit_minute": exit_minute, "shares": shares, "pnl": pnl, "ret": pnl / notional, "kind": kind})
        position = entry = None

    for decision in DECISIONS:
        sig_m = minute_before(decision)
        if sig_m > last_m:
            break
        bar = by.get(sig_m)
        sigma = sigmas.get(decision)
        if bar is None or sigma is None:
            continue
        upper, lower = bands(open_930, prev_close, sigma)
        vwap = session_vwap(bars, sig_m)
        stop_long = upper if vwap is None else max(upper, vwap)
        stop_short = lower if vwap is None else min(lower, vwap)
        close = bar[4]
        fill_bar = by.get(decision)  # the bar that opens at the decision time is the next bar after sig_m
        fill_px = fill_bar[1] if fill_bar else None
        if position == "long" and close < stop_long:
            close_trade(fill_px if fill_px is not None else last[4], decision if fill_px is not None else last_m, "stop")
        elif position == "short" and close > stop_short:
            close_trade(fill_px if fill_px is not None else last[4], decision if fill_px is not None else last_m, "stop")
        if position is not None or fill_px is None:
            continue
        want = "long" if close > upper and "long" not in used else None
        if want is None and side == "both" and close < lower and "short" not in used:
            want = "short"
        if want is None:
            continue
        shares = int(notional / fill_px)
        if shares < 1:
            continue
        used.add(want)
        position = want
        entry = {"fill": _slip(fill_px, want, "enter"), "shares": shares, "minute": decision}
    if position:
        close_trade(last[4], last_m, "close")
    return {"trades": trades, "daily": sum(t["ret"] for t in trades), "benchmark": benchmark, "status": "ok"}
