"""Index program: the three frozen rules (docs/superpowers/specs/2026-10-12-index-program-design.md section 4, with
D1). "Trading days" are the days in the SPY file. Pure functions over dates and adjusted closes; they compute no
return and print nothing.

- T (4.1): on each month's last trading day, ON if the close is above the mean of the last 10 month-end closes
  (this month included); a change of state executes at the next trading day's open.
- TOM (4.2): buy at the close of the trading day before the month's last trading day L (the exchange's planned
  calendar: if that day does not trade, at the next close); sell at the close of the third traded day of the next
  month.
- D (4.3): with no D position open, at the close of day t, C > SMA200 and RSI2 < 10: buy at the open of t+1; exit
  at the open after the first close on or after the entry day with C > SMA5."""
import datetime as dt

from bot import index_probe

T_MONTHS, D_RSI_MAX, D_LONG, D_SHORT = 10, 10.0, 200, 5


def sma(closes, n):
    """The mean of the last n closes, day t included (None before n closes)."""
    out, s = [], 0.0
    for k, c in enumerate(closes):
        s += c
        if k >= n:
            s -= closes[k - n]
        out.append(s / n if k >= n - 1 else None)
    return out


def rsi2(closes):
    """Wilder's RSI with period 2 from the file's start (4.3): the first averages are the simple means of the first
    two changes; then avg = (previous avg + today's value) / 2; 100 when the average loss is 0."""
    out = [None] * len(closes)
    ch = [closes[k] - closes[k - 1] for k in range(1, len(closes))]
    if len(ch) < 2:
        return out
    g = (max(ch[0], 0) + max(ch[1], 0)) / 2
    lo = (max(-ch[0], 0) + max(-ch[1], 0)) / 2
    for k in range(1, len(ch)):
        if k > 1:
            g, lo = (g + max(ch[k], 0)) / 2, (lo + max(-ch[k], 0)) / 2
        out[k + 1] = 100.0 if lo == 0 else 100 - 100 / (1 + g / lo)
    return out


def month_ends(days):
    """The last trading day of each calendar month in the file."""
    return [d for d, nxt in zip(days, days[1:] + [None]) if nxt is None or nxt[:7] != d[:7]]


def trend_states(rows, months=T_MONTHS):
    """4.1: [(month-end day, on)] from the first month-end with `months` month-end closes."""
    close = {r[0]: r[4] for r in rows}
    ends = month_ends([r[0] for r in rows])
    out = []
    for k in range(months - 1, len(ends)):
        avg = sum(close[d] for d in ends[k - months + 1:k + 1]) / months
        out.append((ends[k], close[ends[k]] > avg))
    return out


def trend_changes(rows, months=T_MONTHS):
    """T's change points [(day, "open", on)]: each month-end state takes effect at the next trading day's open; a
    state equal to the one before makes no change point (the state before the first month-end is OFF)."""
    days = [r[0] for r in rows]
    nxt = dict(zip(days, days[1:]))
    out, state = [], False
    for d, on in trend_states(rows, months):
        if on != state and d in nxt:
            out.append((nxt[d], "open", on))
            state = on
    return out


def _planned_days(first, last):
    """The exchange's planned calendar (weekdays less the regular holidays; the special closures were not known in
    advance), over whole months."""
    s = dt.date.fromisoformat(first[:8] + "01")
    e = dt.date.fromisoformat(last[:8] + "01") + dt.timedelta(40)
    hol = {d for y in range(s.year, e.year + 1) for d in index_probe.nyse_holidays(y)}
    out, d = [], s
    while d <= e:
        if d.weekday() < 5 and d.isoformat() not in hol:
            out.append(d.isoformat())
        d += dt.timedelta(1)
    return out


def tom_positions(days):
    """4.2: [(entry day, exit day)], both at the close; the exit is None when the file ends first."""
    planned = _planned_days(days[0], days[-1])
    by_month = {}
    for d in planned:
        by_month.setdefault(d[:7], []).append(d)
    out = []
    for month in sorted({d[:7] for d in days}):
        p = by_month.get(month, [])
        if len(p) < 2:
            continue
        want = p[-2]  # the planned trading day before the planned last trading day L
        entry = next((d for d in days if d >= want), None)  # that day, or the next close if it did not trade
        if entry is None:
            continue
        nxt = (dt.date.fromisoformat(month + "-01") + dt.timedelta(32)).strftime("%Y-%m")
        later = [d for d in days if d[:7] == nxt]
        exit_ = later[2] if len(later) >= 3 else None
        if exit_ is None or exit_ > entry:
            out.append((entry, exit_))
    return out


def dip_positions(rows, rsi_max=D_RSI_MAX, long=D_LONG, short=D_SHORT):
    """4.3: [(signal day, entry day, exit signal day, exit day)]; entry and exit at the open; the exit fields are
    None while the position is open at the file's end."""
    days, closes = [r[0] for r in rows], [r[4] for r in rows]
    s_long, s_short, rsi = sma(closes, long), sma(closes, short), rsi2(closes)
    out, t = [], 0
    while t < len(days) - 1:
        if s_long[t] is not None and rsi[t] is not None and closes[t] > s_long[t] and rsi[t] < rsi_max:
            entry = t + 1
            x = next((k for k in range(entry, len(days)) if s_short[k] is not None and closes[k] > s_short[k]), None)
            if x is None or x + 1 >= len(days):
                out.append((days[t], days[entry], days[x] if x is not None else None, None))
                return out
            out.append((days[t], days[entry], days[x], days[x + 1]))
            t = x + 1  # the position closes at this open: the next signal can come at this day's close
            continue
        t += 1
    return out


def held_after_close(positions, days):
    """7.1: the days on which a rule holds SPY after the close, from a position's entry day to the day before its
    exit day (to the file's last day when the exit is None)."""
    out = set()
    for entry, exit_ in positions:
        out.update(d for d in days if entry <= d and (exit_ is None or d < exit_))
    return out
