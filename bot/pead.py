"""Post-earnings announcement drift. Locked 2026-10-15 before any data request for this test.

Long only, cash, $460, one position, settled the session after the exit. This module does not place or transmit an order.

There is no earnings calendar. The earnings day is a day with an Alpaca headline in the 24 hours ending
09:30 New York whose headline matches earnings, results, quarter, EPS, or revenue. The headline field
is the only field. The source is not filtered.

The day also has to gap up at least 5 percent against the previous session's close, trade at least 3
times the median daily volume of the prior 20 sessions, close in the top half of its range, close above
$5, and have prior dollar ADV above $20 million. Prices and volume are split-adjusted, so a reverse
split is not a 5 percent gap. A split ex-date on the event day or the entry day is skipped.

The entry is the next session's open. The disaster stop is the event day's low. A next open already at
or through that low is skipped. The gated hold is 20 sessions. Holds of 10 and 40 are the same entries,
reported and not gated. There is no profit target. The exit is the stop or the close of the last session
in the window.

The gate is the 20-session book with IBKR Pro fees ($0.0035 a share, $0.35 minimum, both sides) plus
the spread. IBKR Lite is the same trades with the commission set to zero, plus the same spread, and it
is reported. The out-of-sample mean net return also has to beat buy-and-hold SPY over the same entry
open to exit close. That SPY return has no commission and no spread.
"""
import datetime as dt
import re
import statistics
from zoneinfo import ZoneInfo

from bot.desk_gate import ACCOUNT, friction, judge, shares_for, walk

NY = ZoneInfo("America/New_York")
PRICE_MIN = 5.0
GAP_MIN = 0.05
RVOL_MIN = 3.0
RVOL_DAYS, RVOL_MIN_HISTORY = 20, 10
ADV_MIN = 20_000_000.0
HOLDS = (10, 20, 40)
GATE_HOLD = 20
EARNINGS_RE = re.compile(r"\bearnings\b|\bresults\b|\bquarter|\bEPS\b|\brevenue\b", re.I)


def headline_matches(headline):
    return bool(EARNINGS_RE.search(headline or ""))


def in_prior_24h(created_at, day):
    """True when `created_at` is in the 24 hours ending 09:30 New York on `day`."""
    if not created_at:
        return False
    end = dt.datetime.fromisoformat(f"{day}T09:30").replace(tzinfo=NY)
    start = end - dt.timedelta(hours=24)
    try:
        t = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return start <= t < end


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def event_ok(prev_close, bar, prior_vols, prior_dollars):
    """`bar` is the event day `(date, o, h, l, c, v)`. Priors are the previous 20 sessions, None if no print."""
    if not prev_close or prev_close <= 0 or not bar:
        return False
    _d, o, h, l, c, v = bar
    if o is None or c is None or h is None or l is None or h <= l:
        return False
    if c <= PRICE_MIN or o / prev_close - 1 < GAP_MIN:
        return False
    if (c - l) / (h - l) < 0.5:
        return False
    usable = [x for x in prior_vols if x]
    if v is None or v <= 0 or len(usable) < RVOL_MIN_HISTORY:
        return False
    med = statistics.median(usable)
    if med <= 0 or v / med < RVOL_MIN:
        return False
    dollars = [x for x in prior_dollars if x]
    adv = _mean(dollars)
    return adv is not None and len(dollars) >= RVOL_MIN_HISTORY and adv > ADV_MIN


def mark(future, entry, stop, n, side):
    """Stop-only path over `n` sessions starting at the entry. No profit target.

    `future` rows are `(date, o, h, l, c)` or None. A halt uses up one session. The window ending
    without a stop exits at the last close. Running out of bars first is `last`.
    """
    risk = entry - stop
    if risk <= 0 or n < 1:
        return None
    if side == "long":
        stop_px, target = stop, entry + 1e9
    else:
        stop_px, target = entry + risk, entry - 1e9
    last = None
    window = list(future[:n])
    for bar in window:
        if bar is None:
            continue
        last = bar
        hit = walk([(bar[0], bar[1], bar[2], bar[3], bar[4])], stop_px, target, side, True)
        if hit and hit[1] != "time":
            return hit[0], hit[1], hit[2]
    if last is None:
        return None
    # A halt before the last session still counts. Prints that never reach the last session,
    # or a calendar that runs out, exit at the last close.
    kind = "time" if len(window) >= n and window[-1] is not None else "last"
    return last[4], kind, last[0]


def pnl_r(entry, exit_px, stop, shares, spread, side, pro):
    """Pro keeps the IBKR fixed fee. Lite zeros it. The spread or the haircut stays either way."""
    slip, fee, mode = friction(entry, exit_px, shares, spread)
    if not pro:
        fee = 0.0
    gross = (exit_px - entry) if side == "long" else (entry - exit_px)
    risk = abs(entry - stop) * shares
    if not risk:
        return None
    pnl = gross * shares - slip - fee
    return {"pnl": pnl, "net_r": pnl / risk, "net_return": pnl / (entry * shares), "cost_mode": mode,
            "shares": shares}


def score(entry, exit_px, short_px, stop, spread):
    """Pro and Lite results for one long, plus the Pro short used by the random-direction check."""
    shares = shares_for(entry, ACCOUNT)
    if shares < 1 or short_px is None or entry <= stop:
        return None
    pro = pnl_r(entry, exit_px, stop, shares, spread, "long", True)
    lite = pnl_r(entry, exit_px, stop, shares, spread, "long", False)
    short = pnl_r(entry, short_px, stop, shares, spread, "short", True)
    if not pro or not lite or not short or pro["net_r"] is None or short["net_r"] is None:
        return None
    return {"shares": shares, "pnl": pro["pnl"], "net_r": pro["net_r"], "net_r_short": short["net_r"],
            "net_return": pro["net_return"], "lite_pnl": lite["pnl"], "lite_r": lite["net_r"],
            "lite_return": lite["net_return"], "cost_mode": pro["cost_mode"]}


def schedule(candidates):
    """One position. Next entry is strictly after the exit day. Highest ADV, then the symbol."""
    rows = sorted(candidates, key=lambda c: (c["entry_day"], -c["adv"], c["t"]))
    taken, free_after = [], ""
    for c in rows:
        if c["entry_day"] <= free_after:
            continue
        if taken and taken[-1]["entry_day"] == c["entry_day"]:
            continue
        taken.append(c)
        free_after = c["exit_day"]
    return taken


def spy_return(entry_open, exit_close):
    """Buy-and-hold SPY from the entry open to the exit close. No cost."""
    if not entry_open or not exit_close or entry_open <= 0:
        return None
    return exit_close / entry_open - 1


def beats_spy(trades):
    """Strictly greater mean net return than the matched SPY windows. A missing window fails."""
    if not trades or any(t.get("spy") is None or t.get("net_return") is None for t in trades):
        return False
    strat = sum(t["net_return"] for t in trades) / len(trades)
    bench = sum(t["spy"] for t in trades) / len(trades)
    return strat > bench


def gated(trades):
    """The desk checks on Pro net R, plus the SPY window. `trades` are already the out-of-sample rows."""
    g = judge(trades)
    spy_ok = beats_spy(trades)
    checks = list(g["checks"]) + [("mean net return above buy-and-hold SPY over the same holding windows", spy_ok)]
    return {"n": g["n"], "mean": g["mean"], "t": g["t"], "p95": g["p95"], "years": g["years"],
            "checks": checks, "go": g["go"] and spy_ok, "spy_ok": spy_ok}
