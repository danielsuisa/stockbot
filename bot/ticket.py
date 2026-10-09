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


# ---------- the two rule sets ----------
RISK, MIN_SHARES = 100, 10  # dollars at risk per trade (results are in R, so this only sets share rounding)
PRE_OPEN, PRE_FIRST, PRE_LAST = "04:00", "04:55", "09:25"  # pre-market: bars from, first check, last check / expiry
# ORIGINAL - the owner's rules as written
O_FIRST, O_LAST, O_OR_END = "09:50", "15:00", "09:45"  # REGULAR checks from / to the bar closes; opening range end
O_SPREAD, O_PAD, O_STOP_PAD, O_MIN_STOP = 0.05, 0.05, 0.10, 0.20
O_VOL_MULT, O_VOL_BARS = 2.0, 10
# IMPROVED - the research report's baseline (opening-range breakout, ZBA 2024)
I_OR_END, I_VALID, I_D_ATR, I_REL_VOL = "09:35", 60, 0.10, 1.0
I_SPREAD_MID, I_SPREAD_D, I_FLAT = 0.01, 0.10, 5  # spread <= 1% of the price and <= 0.1 x D; legs flat 5 min early
# how far a check got: a NO TICKET reports the attempt that got furthest (the earliest of equals)
ORDER = {"ORIGINAL": ("no signal", "trigger", "volume", "vwap", "halt", "no higher low", "size below minimum",
                      "spread"),
         "IMPROVED": ("no signal", "trigger", "vwap", "halt", "spread", "size below minimum")}


def _r(x):
    return round(x, 4)


def _shares(per_share):
    return int(RISK / per_share + 1e-9) if per_share > 0 else 0


def _legs(shares, target, exit_at):
    a = shares // 2
    return [{"qty": a, "target": target, "exit_at": exit_at}, {"qty": shares - a, "target": None, "exit_at": exit_at}]


def _no(reason, at=None):
    return {"ok": False, "reason": reason, "at": at}


def signal(rules, session, bars1, ctx, spread):
    """The first order ticket of a session, or why there is none.

    rules "ORIGINAL" | "IMPROVED"; session "REGULAR" | "PREMARKET"; bars1: the day's 1-minute bars; ctx {"open",
    "close" ("HH:MM", early closes included), "atr" (ATR14 before the day), "open_rel_vol", "from" (the minute the
    stock entered the list: a check needs that minute closed), optional "spread_d" (IMPROVED's spread bound as a
    fraction of D, default I_SPREAD_D; None: no bound)}; spread("HH:MM" New York) -> ask - bid or None, asked
    only when every other entry check has passed.
    -> {"ok": True, "rules", "session", "at", "level", "entry_type" ("LMT" | "STP_LMT"), "entry", "limit", "stop",
    "R", "shares", "legs": [{"qty", "target", "exit_at"}], "valid_until", "spread"} or {"ok": False, "reason", "at"}.
    IMPROVED: entry / stop / target are set off the trigger price `level`; the simulation re-bases them on the fill."""
    lo, hi = (ctx["open"], ctx["close"]) if session == "REGULAR" else (PRE_OPEN, ctx["open"])
    sess = [b for b in bars1 if lo <= b[0] < hi]
    run = _original if rules == "ORIGINAL" else _improved
    return run(session, sess, bars1, ctx, spread)


def _deeper(rules, best, reason, at):
    order = ORDER[rules]
    return (reason, at) if order.index(reason) > order.index(best[0]) else best


def _original(session, sess, bars1, ctx, spread):
    open_, close = ctx["open"], ctx["close"]
    regular = session == "REGULAR"
    b5 = to5(sess)
    sw = swings(b5)
    t0 = open_ if regular else PRE_OPEN
    first = O_FIRST if regular else PRE_FIRST
    last = hhmm(min(mins(O_LAST), mins(close) - 60)) if regular else PRE_LAST
    orh = max((b[2] for b in sess if b[0] < O_OR_END), default=None)
    best = ("no signal", None)
    for k, bar in enumerate(b5):
        at = bar["end"]
        if not first <= at <= last or at <= ctx["from"]:
            continue
        if regular:
            level = max((x for x in (orh, last_swing_high(sw, k - 1)) if x is not None), default=None)
        else:
            level = max((b["h"] for b in b5[:k]), default=None)
        reason, t = _original_checks(session, bar, k, b5, sw, level, sess, bars1, t0, ctx, spread)
        if t:
            return t
        best = _deeper("ORIGINAL", best, reason, at)
    return _no(*best)


def _original_checks(session, bar, k, b5, sw, level, sess, bars1, t0, ctx, spread):
    """One 5-minute bar close -> (failed check, None) or (None, ticket)."""
    at = bar["end"]
    if level is None or not bar["c"] > level:
        return "trigger", None
    prev = b5[max(0, k - O_VOL_BARS):k]
    if not prev or bar["v"] < O_VOL_MULT * sum(b["v"] for b in prev) / len(prev):
        return "volume", None
    vw = vwap(sess, t0, at)
    if vw is None or not bar["c"] > vw:
        return "vwap", None
    if halted(bars1, at, ctx["open"], ctx["close"]):
        return "halt", None
    hl = last_higher_low(sw, k)
    if hl is None:
        return "no higher low", None
    entry = _r(level + O_PAD)
    stop = _r(min(hl - O_STOP_PAD, entry - O_MIN_STOP))
    R = _r(entry - stop)
    shares = _shares(R)
    if shares < MIN_SHARES:
        return "size below minimum", None
    sp = spread(at)
    if sp is None or _r(sp) > O_SPREAD:
        return "spread", None
    return None, {"ok": True, "rules": "ORIGINAL", "session": session, "at": at, "level": level,
                  "entry_type": "LMT", "entry": entry, "limit": entry, "stop": stop, "R": R, "shares": shares,
                  "legs": _legs(shares, _r(entry + 2 * R), None), "valid_until": ctx["close"], "spread": sp}


def _improved(session, sess, bars1, ctx, spread):
    open_, close, atr = ctx["open"], ctx["close"], ctx.get("atr")
    regular = session == "REGULAR"
    if not atr:
        return _no("no atr")
    after_from = hhmm(mins(ctx["from"]) + 1)
    if regular:
        if (ctx.get("open_rel_vol") or 0) < I_REL_VOL:
            return _no("opening volume")
        orb = [b[2] for b in sess if b[0] < I_OR_END]
        if not orb:
            return _no("no signal")
        place = max(I_OR_END, after_from)
        until = hhmm(min(mins(place) + I_VALID, mins(close)))
    else:
        place, until = max(PRE_FIRST, after_from), PRE_LAST
    t0 = open_ if regular else PRE_OPEN
    hi, seen = None, False  # the pre-market high so far
    for b in sess:
        t = b[0]
        if t >= until:
            break
        level = max(orb) if regular else hi
        hi = b[2] if hi is None else max(hi, b[2])
        if t < place:
            continue
        seen = True
        if level is None or not b[2] > level:
            continue
        return _improved_checks(session, max(b[1], level), level, t, until, sess, bars1, t0, ctx, spread)
    return _no("trigger" if seen else "no signal")


def _improved_checks(session, price, level, at, until, sess, bars1, t0, ctx, spread):
    """The first 1-minute high above the level (a stop-limit order triggers once) -> ticket or NO TICKET."""
    vw = vwap(sess, t0, at)
    if vw is None or not price > vw:
        return _no("vwap", at)
    if halted(bars1, at, ctx["open"], ctx["close"]):
        return _no("halt", at)
    D = _r(I_D_ATR * ctx["atr"])
    sd = ctx.get("spread_d", I_SPREAD_D)  # the spread's bound as a fraction of D; None: no bound
    sp = spread(at)
    if sp is None or _r(sp) > _r(I_SPREAD_MID * price) or (sd is not None and _r(sp) > _r(sd * D)):
        return _no("spread", at)
    shares = _shares(_r(D + sp))
    if shares < MIN_SHARES:
        return _no("size below minimum", at)
    flat = hhmm(mins(ctx["close"]) - I_FLAT)
    return {"ok": True, "rules": "IMPROVED", "session": session, "at": at, "level": level, "entry_type": "STP_LMT",
            "entry": level, "limit": _r(level + D), "stop": _r(level - D), "R": D, "shares": shares,
            "legs": _legs(shares, _r(level + 2 * D), flat), "valid_until": until, "spread": sp}
