"""Fundamental calculators over SEC XBRL facts (docs/screener/metric-spec.md). Pure functions.

Facts of one concept for one company: {"q": [[start, end, val], ...] (~3-month durations), "a": [[start, end, val],
...] (~12-month durations), "i": [[end, val], ...] (instants)}; dates ISO. A missing quarter inside a fiscal year
whose other three quarters are known is derived as FY - 9M (the usual case: Q4 is only in the 10-K)."""
import datetime as dt

TOL = 20  # days of slack when matching period ends


def _d(s):
    return dt.date.fromisoformat(s[:10])


def _days(a, b):
    return (_d(b) - _d(a)).days


def quarters(f):
    """Quarterly values with Q4 = FY - 9M filled in -> [(start, end, val)] ascending by end, one per end date."""
    q = {}
    for start, end, val in (f or {}).get("q") or []:
        if 70 <= _days(start, end) <= 110 and val is not None:
            q[end] = (start, end, float(val))
    for a0, a1, aval in (f or {}).get("a") or []:
        if not (340 <= _days(a0, a1) <= 380) or aval is None:
            continue
        inside = [x for x in q.values() if _days(a0, x[0]) >= -TOL and _days(x[1], a1) >= -TOL]
        if len(inside) == 3 and not any(abs(_days(x[1], a1)) <= TOL for x in inside):
            last = max(inside, key=lambda x: x[1])
            start = (_d(last[1]) + dt.timedelta(1)).isoformat()
            if 70 <= _days(start, a1) <= 110:
                q[a1] = (start, a1, float(aval) - sum(x[2] for x in inside))
    return sorted(q.values(), key=lambda x: x[1])


def _consecutive(qs):
    return all(70 <= _days(a[1], b[1]) <= 110 for a, b in zip(qs, qs[1:]))


def _annual(f):
    return sorted(((a1, float(v)) for a0, a1, v in (f or {}).get("a") or []
                   if v is not None and 340 <= _days(a0, a1) <= 380), key=lambda x: x[0])


def ttm(f):
    """Trailing twelve months -> (value, end) or (None, None): the latest 4 consecutive quarters, or the latest
    fiscal year when it ends later (annual-only filers: 20-F / 40-F, or quarters too sparse)."""
    qs, annual = quarters(f), _annual(f)
    best = (None, None)
    for k in range(len(qs) - 4, -1, -1):
        win = qs[k:k + 4]
        if _consecutive(win) and 330 <= _days(win[0][0], win[-1][1]) <= 400:
            best = (sum(x[2] for x in win), win[-1][1])
            break
    if annual and (best[1] is None or annual[-1][0] > best[1]):
        return annual[-1][1], annual[-1][0]
    return best


def ttm_at(f, end):
    """The twelve months ending near `end` (ISO, within TOL days) -> (value, end) or (None, None): 4 consecutive
    quarters ending there, else a fiscal year ending there."""
    qs = quarters(f)
    for k in range(len(qs) - 4, -1, -1):
        win = qs[k:k + 4]
        if abs(_days(win[-1][1], end)) <= TOL and _consecutive(win) and 330 <= _days(win[0][0], win[-1][1]) <= 400:
            return sum(x[2] for x in win), win[-1][1]
    for a1, v in reversed(_annual(f)):
        if abs(_days(a1, end)) <= TOL:
            return v, a1
    return None, None


def latest(f):
    """The newest instant value -> (value, end) or (None, None)."""
    rows = sorted((e, v) for e, v in (f or {}).get("i") or [] if v is not None)
    return (float(rows[-1][1]), rows[-1][0]) if rows else (None, None)


def growth(cur, base):
    """cur / base - 1, None when either is missing or base <= 0."""
    if cur is None or base is None or base <= 0:
        return None
    return cur / base - 1


def quarter_yoy(f):
    """Latest quarter vs the quarter ending a year earlier -> fraction or None."""
    qs = quarters(f)
    if not qs:
        return None
    last = qs[-1]
    prior = [x for x in qs if abs(_days(x[1], last[1]) - 365) <= TOL]
    return growth(last[2], prior[-1][2]) if prior else None


def ttm_yoy(f):
    """TTM vs the twelve months ending a year earlier -> fraction or None."""
    cur, end = ttm(f)
    if cur is None:
        return None
    base, _ = ttm_at(f, (_d(end) - dt.timedelta(365)).isoformat())
    return growth(cur, base)


def annual_series(f):
    """[(end, val)] of ~12-month facts, ascending, one per fiscal year."""
    by = {}
    for a0, a1, v in (f or {}).get("a") or []:
        if v is not None and 340 <= _days(a0, a1) <= 380:
            by[a1] = float(v)
    return sorted(by.items())


def cagr(f, years):
    """Annual growth over `years` fiscal years: CAGR of the latest annual value vs the one `years` earlier; when that
    base is <= 0 (or the latest is), the mean of the year-over-year changes with a positive base (DECISION) ->
    fraction or None."""
    s = annual_series(f)
    if len(s) < years + 1:
        return None
    last = s[-1]
    base = [x for x in s if abs(_days(x[0], last[0]) - 365 * years) <= TOL * years]
    if not base:
        return None
    b = base[-1]
    if b[1] > 0 and last[1] > 0:
        return (last[1] / b[1]) ** (1 / years) - 1
    window = [x for x in s if x[0] >= b[0]]
    yoy = [growth(c[1], p[1]) for p, c in zip(window, window[1:]) if p[1] > 0]
    return sum(yoy) / len(yoy) if yoy else None


def streak(f, max_years=10):
    """Consecutive fiscal years (newest first) whose annual value rose on the year before -> int (0 when none)."""
    s = annual_series(f)[-(max_years + 1):]
    n = 0
    for p, c in reversed(list(zip(s, s[1:]))):
        if c[1] > p[1]:
            n += 1
        else:
            break
    return n


def ratio(a, b, positive_den=True):
    """a / b, None when either is missing or b is zero (or <= 0 with positive_den)."""
    if a is None or b is None or b == 0 or (positive_den and b < 0):
        return None
    return a / b
