"""The order-ticket rule engine (spec docs/superpowers/specs/2026-10-08-ticket-backtest-design.md): pure functions
over 1-minute bars `(minute "HH:MM" New York, o, h, l, c, v, vw)` of one session, shared by the backtest and, after a
GO, by live tickets. A 5-minute bar is used only after it closes; a swing is used only after the 2 bars that
confirm it have closed."""


def mins(hhmm):
    """"HH:MM" -> minutes after midnight."""
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def hhmm(n):
    return f"{n // 60:02d}:{n % 60:02d}"


def to5(bars1):
    """1-minute bars -> 5-minute bars on the :00/:05 grid, from the minutes that exist: [{"start", "end", "o", "h",
    "l", "c", "v", "vw"}] ascending (a 5-minute slot without minutes has no bar)."""
    out = []
    for t, o, h, l, c, v, vw in bars1:
        start = mins(t) // 5 * 5
        if not out or out[-1]["_s"] != start:
            out.append({"_s": start, "start": hhmm(start), "end": hhmm(start + 5), "o": o, "h": h, "l": l, "c": c,
                        "v": 0, "_pv": 0.0})
        b = out[-1]
        b["h"], b["l"], b["c"] = max(b["h"], h), min(b["l"], l), c
        b["v"] += v
        b["_pv"] += (c if vw is None else vw) * v
    for b in out:
        b["vw"] = b.pop("_pv") / b["v"] if b["v"] else b["c"]
        del b["_s"]
    return out


def vwap(bars1, start, upto):
    """Volume-weighted average price of the minutes start <= m < upto; None without volume."""
    pv = vol = 0
    for t, o, h, l, c, v, vw in bars1:
        if start <= t < upto:
            pv += (c if vw is None else vw) * v
            vol += v
    return pv / vol if vol else None


def swings(bars5):
    """Swing highs / lows: a bar whose high is strictly above the highs of the 2 bars before and the 2 after (a low
    strictly below, likewise) -> [{"i", "kind", "price", "known_at": i + 2 (the bar whose close confirms it)}]."""
    out = []
    for i in range(2, len(bars5) - 2):
        near = bars5[i - 2:i] + bars5[i + 1:i + 3]
        if all(bars5[i]["h"] > b["h"] for b in near):
            out.append({"i": i, "kind": "high", "price": bars5[i]["h"], "known_at": i + 2})
        if all(bars5[i]["l"] < b["l"] for b in near):
            out.append({"i": i, "kind": "low", "price": bars5[i]["l"], "known_at": i + 2})
    return out


def last_swing_high(sw, upto):
    """The most recent swing high known by the close of 5-minute bar `upto`; None when none."""
    highs = [s["price"] for s in sw if s["kind"] == "high" and s["known_at"] <= upto]
    return highs[-1] if highs else None


def last_higher_low(sw, upto):
    """The most recent higher low (a swing low above the swing low before it) known by the close of bar `upto`."""
    lows = [s["price"] for s in sw if s["kind"] == "low" and s["known_at"] <= upto]
    higher = [b for a, b in zip(lows, lows[1:]) if b > a]
    return higher[-1] if higher else None


def atr14(daily, day):
    """Mean true range of the 14 sessions before `day` (daily: [(date, o, h, l, c, v)] ascending); None with fewer
    than 14. The first session without a close before it uses its high - low."""
    prior = [b for b in daily if b[0] < day][-15:]
    if len(prior) < 14:
        return None
    tr = []
    for k, (_, o, h, l, c, v) in enumerate(prior):
        pc = prior[k - 1][4] if k else None
        tr.append(h - l if pc is None else max(h - l, abs(h - pc), abs(l - pc)))
    return sum(tr[-14:]) / 14


def halted(bars1, at, open_, close, lookback=15):
    """A halt, approximated from the bars: a run of >= 5 minutes without a 1-minute bar inside [open_, close) that
    overlaps the `lookback` minutes before `at` (only minutes before `at` are known). Pre-market never counts."""
    have = {mins(b[0]) for b in bars1}
    lo, hi = mins(open_), min(mins(close), mins(at))
    run = 0
    for n in range(lo, hi):
        run = 0 if n in have else run + 1
        if run >= 5 and n >= mins(at) - lookback:
            return True
    return False
