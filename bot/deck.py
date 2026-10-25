"""Alert rules from the study deck "קריאת נרות ופקודות Bracket" (slides 01–58).

Pure functions over completed 5-minute candles. A candle is
{"start", "end", "o", "h", "l", "c", "v", "vwap"?}. Long only: a bearish pattern never becomes a buy.
The bracket is the deck's: one tick above the trigger, one tick under the invalidation, target at twice
that distance, size = floor(risk / R). Interpretation choices are listed in the pull request.
"""
from bot import ticket

TICK = 0.01  # slide 16: the price step above $1
TARGET_R = 2.0  # every worked order in the deck is 2R
LIMIT_OFFSET = 0.05  # slide 55: the worked ticket's limit sits 0.05 above the trigger
RISK_PCT = 0.01  # slide 51 and checklist item 5: 1% of the account
ZONE = 0.003  # a level is a zone, not a line (slide 27); the same proximity the screener already used for VWAP
RVOL = 2.0  # slide 29: RVOL of 2 means double the usual volume
RETRACE = 0.5  # slide 37: a healthy flag gives back less than half the pole
COST_MAX_R = 0.60  # slide 52: an intraday trade costs about 0.2R–0.6R; above that, commissions eat R
COMM_PER_SHARE, COMM_MIN = 0.005, 1.0  # owner's IBKR Pro Fixed; two filled orders (entry and the exit)
STOP_ATR_FLOOR, STOP_ATR_CAP, STOP_MIN_PCT = 0.10, 1.0, 0.003  # slide 36: the live bot's volatility band
VWAP_FROM, VWAP_UNTIL = "09:40", "11:30"  # slide 36
TIME_EXIT = "15:55"  # slide 36
POLE_BARS = (2, 4)  # "a few" consecutive bullish candles
FLAG_BARS = (2, 4)
_EPS = 1e-9


def cents(x):
    """A price on the one-cent grid (stocks above $1)."""
    return round(x + _EPS, 2)


def body(b):
    return abs(b["c"] - b["o"])


def is_bull(b):
    return b["c"] > b["o"]


def is_bear(b):
    return b["c"] < b["o"]


def is_doji(b):
    """Slide 15: open and close almost the same price. One tick is 'almost' for these stocks.
    A doji is not an entry by itself."""
    return body(b) <= TICK


def _range(b):
    return b["h"] - b["l"]


def is_hammer(b):
    """Slide 13: lower wick at least twice the body, small upper wick, body in the upper third.
    The thirds are the deck's definition of 'small upper wick'. A doji is not a hammer (slide 11's
    candle is only 'almost' one: its body is not in the upper third)."""
    rng = _range(b)
    if rng <= 0 or is_doji(b):
        return False
    lower = min(b["o"], b["c"]) - b["l"]
    return lower + _EPS >= 2 * body(b) and min(b["o"], b["c"]) + _EPS >= b["l"] + 2 * rng / 3


def is_shooting_star(b):
    """Slide 15: the hammer's mirror — long upper wick, small body in the lower third."""
    rng = _range(b)
    if rng <= 0 or is_doji(b):
        return False
    upper = b["h"] - max(b["o"], b["c"])
    return upper + _EPS >= 2 * body(b) and max(b["o"], b["c"]) <= b["l"] + rng / 3 + _EPS


def is_bull_engulfing(prev, b):
    """Slide 14: a bullish candle whose body covers the previous bearish body completely. Wicks do not count."""
    if not (is_bear(prev) and is_bull(b) and body(prev) > TICK):
        return False
    return b["o"] <= prev["c"] + _EPS and b["c"] + _EPS >= prev["o"]


def is_inside(mother, b):
    """Slide 20: the whole range sits inside the mother bar. Color does not matter. The inequalities are strict."""
    return b["h"] < mother["h"] and b["l"] > mother["l"]


def is_nr7(bars):
    """Slide 21: the last candle's range is strictly the smallest of the seven. Not a bracket by itself
    (slide 03: a technique that does not give both levels is not a bracket — the stop is never stated)."""
    if len(bars) < 7:
        return False
    last = _range(bars[-1])
    return last > 0 and all(last < _range(b) for b in bars[-7:-1])


def geometry(trigger_high, stop_anchor):
    """Slide 16 / 24 / 40 / 55: trigger and stop are one tick beyond the pattern, target is twice the distance."""
    entry = cents(trigger_high + TICK)
    stop = cents(stop_anchor - TICK)
    r = cents(entry - stop)
    return {"entry": entry, "stop": stop, "R": r, "target": cents(entry + TARGET_R * r),
            "limit": cents(entry + LIMIT_OFFSET)}


def inside_orders(mother_high, mother_low, inside_high, inside_low):
    """Slide 22–25. The regular order is the one the alerts use; the aggressive order is only for the arithmetic."""
    return {"regular": geometry(mother_high, mother_low), "aggressive": geometry(inside_high, inside_low)}


def _zone(level):
    return max(ZONE * abs(level), TICK)


def tags_support(b, level):
    """The candle reaches the zone and does not close through it."""
    return b["l"] <= level + _zone(level) and b["c"] >= level - _zone(level)


def tags_resistance(b, level):
    """The upper wick reaches the level and the close does not stay above it."""
    z = _zone(level)
    return b["h"] + _EPS >= level - z and max(b["o"], b["c"]) <= level + z and b["c"] <= level + z


def round_levels(price):
    """Slide 27's examples are 50 and 100: multiples of 10 near the price."""
    if not price or price <= 0:
        return []
    base = round(price / 10.0) * 10.0
    return [p for p in (base - 10.0, base, base + 10.0) if p > 0]


def _swings(bars, kind, last):
    out = []
    for s in ticket.swings(bars):
        if s["kind"] == kind and s["known_at"] <= last:
            out.append(s["price"])
    return out


def support_levels(bars, ctx):
    """Slide 13 and 27: VWAP, a prior low, yesterday's low or close. A level must be known before the signal."""
    out = [ctx[k] for k in ("prev_low", "prev_close") if ctx.get(k)]
    vw = bars[-1].get("vwap")
    if vw:
        out.append(vw)
    out.extend(_swings(bars, "low", len(bars) - 2))
    return out


def resistance_levels(bars, ctx):
    out = [ctx[k] for k in ("prev_high",) if ctx.get(k)]
    out.extend(round_levels(bars[-1]["c"]))
    out.extend(_swings(bars, "high", len(bars) - 2))
    return out


def _nearest(b, levels, tag):
    hits = [lv for lv in levels if tag(b, lv)]
    if not hits:
        return None
    ref = b["l"] if tag is tags_support else b["h"]
    return min(hits, key=lambda lv: abs(ref - lv))


def after_decline(prior):
    """Slide 13, 'after a decline': the three candles before the signal make strictly lower lows."""
    if len(prior) < 3:
        return False
    a, b, c = prior[-3], prior[-2], prior[-1]
    return a["l"] > b["l"] > c["l"]


def after_rise(prior):
    if len(prior) < 3:
        return False
    a, b, c = prior[-3], prior[-2], prior[-1]
    return a["h"] < b["h"] < c["h"]


def window_uptrend(bars):
    """Slide 09, over the last six candles: the later half has a higher high and a higher low than the earlier half.
    Swing by swing, not one print per candle — five-minute bars rarely step up every bar."""
    bars = bars[-6:]
    if len(bars) < 4:
        return False
    mid = len(bars) // 2
    early, late = bars[:mid], bars[mid:]
    return (max(x["h"] for x in late) > max(x["h"] for x in early)
            and min(x["l"] for x in late) > min(x["l"] for x in early))


def _mean(xs):
    xs = [x for x in xs if x > 0]
    return sum(xs) / len(xs) if xs else 0.0


def _setup(kind, trigger, anchor, note, score, end, mode, band=False, level=None, spring=False):
    return {"setup": kind, "trigger_high": trigger, "stop_anchor": anchor, "note": note, "score": score,
            "candle_end": end, "valid_mode": mode, "band": band, "level": level, "spring": spring, "warn": False}


def vwap_pullback(bars):
    """Slide 36. Above VWAP and rising, a dip back to VWAP, a hammer or a bullish engulfing there.
    Only a candle that closes between 09:40 and 11:30. The stop later has to sit inside the volatility band."""
    if len(bars) < 4:
        return None
    last, end = bars[-1], bars[-1]["end"]
    if not (VWAP_FROM <= end <= VWAP_UNTIL):
        return None
    vw = last.get("vwap")
    prior = bars[:-1]
    if not vw or last["c"] < vw or last["l"] > vw * (1 + ZONE):
        return None
    above = [b for b in prior if b.get("vwap") and b["c"] > b["vwap"]]
    if len(above) * 2 <= len(prior):
        return None
    recent = prior[-6:]
    if not (recent[-1]["h"] > recent[0]["h"] and recent[-1]["l"] > recent[0]["l"]):
        return None
    if is_hammer(last):
        return _setup("vwap_hammer", last["h"], last["l"], "פטיש על VWAP אחרי תיקון במגמה", 5.0, end, "next",
                      band=True, level=vw)
    prev = bars[-2]
    if is_bull_engulfing(prev, last):
        # slide 14, not the generic 'its low': the stop is under the lower of the two candles
        return _setup("vwap_engulf", last["h"], min(prev["l"], last["l"]), "בליעה שורית על VWAP אחרי תיקון במגמה",
                      5.0, end, "next", band=True, level=vw)
    return None


def _hammer_or_engulf(bars, ctx):
    """Slides 13–14 and 16. Shape plus a decline plus a level. The buy-stop is the confirmation."""
    out = []
    if len(bars) < 4:
        return out
    last, end = bars[-1], bars[-1]["end"]
    if not after_decline(bars[:-1]):
        return out
    level = _nearest(last, support_levels(bars, ctx), tags_support)
    if level is None:
        return out
    spring = last["l"] < level - _EPS and last["c"] > level + _EPS
    extra = " · ספרינג: השפל חדר מתחת לרמה ונסגר מעליה" if spring else ""
    if is_hammer(last):
        out.append(_setup("hammer", last["h"], last["l"], "פטיש אחרי ירידה על רמה" + extra, 2.0, end, "next",
                          level=level, spring=spring))
    prev = bars[-2]
    if is_bull_engulfing(prev, last):
        out.append(_setup("engulf", last["h"], min(prev["l"], last["l"]), "בליעה שורית אחרי ירידה על רמה" + extra,
                          2.2, end, "next", level=level, spring=spring))
    return out


def _flag_ok(pole, flag, brk):
    if not all(is_bull(b) for b in pole):
        return False
    for i in range(1, len(pole)):
        if pole[i]["c"] <= pole[i - 1]["c"]:
            return False
    pole_high = max(b["h"] for b in pole)
    rise = pole_high - pole[0]["o"]
    if rise <= TICK:
        return False
    flag_low = min(b["l"] for b in flag)
    flag_high = max(b["h"] for b in flag)
    if (pole_high - flag_low) / rise >= RETRACE - _EPS:  # strictly less than half (slide 40)
        return False
    if flag_high > pole_high + _EPS or flag[-1]["c"] > flag[0]["o"] + _EPS:
        return False
    if max(_range(b) for b in flag) >= max(_range(b) for b in pole):
        return False
    vols = [b["v"] for b in flag]
    if any(vols[i] >= vols[i - 1] for i in range(1, len(vols))):
        return False
    if _mean(vols) >= _mean(b["v"] for b in pole):
        return False
    # slide 38: the breakout candle passes the flag high and volume rises again
    if brk["h"] <= flag_high or brk["v"] <= _mean(vols):
        return False
    if brk["l"] <= flag_low - TICK:  # the invalidation already traded
        return False
    return True


def bull_flag(bars):
    """Slides 37–40. Pole, then a short flag, then the breakout candle. Trigger and stop are the flag, not the pole."""
    if len(bars) < 5:
        return None
    brk, end = bars[-1], bars[-1]["end"]
    for n_flag in range(FLAG_BARS[0], FLAG_BARS[1] + 1):
        for n_pole in range(POLE_BARS[0], POLE_BARS[1] + 1):
            need = 1 + n_flag + n_pole
            if len(bars) < need:
                continue
            pole = bars[-need:-n_flag - 1]
            flag = bars[-n_flag - 1:-1]
            if _flag_ok(pole, flag, brk):
                return _setup("flag", max(b["h"] for b in flag), min(b["l"] for b in flag),
                              "דגל שורי: תיקון מתחת לחצי התורן, מחזור יורד, פריצה עם מחזור", 4.0, end, "window")
    return None


def _near(high, level):
    return abs(high - level) <= _zone(level)


def cluster_levels(prior):
    """Slide 27: price stopped in the same area at least twice."""
    window = prior[-8:]
    found = []
    for b in window:
        group = [x for x in window if _near(x["h"], b["h"]) and x["c"] < b["h"]]
        if len(group) >= 2:
            found.append(max(x["h"] for x in group))
    return found


def breakout_at(bars, level):
    """Slides 29–30. A true breakout closes above the level on at least double the volume, after rising lows
    pressed into a flat ceiling. A wick that returns below the level is a bull trap and is not an alert."""
    if len(bars) < 4 or not level:
        return None
    last, prior = bars[-1], bars[:-1]
    if len(prior) < 3 or not (prior[-1]["c"] <= level < last["c"]):
        return None
    if not (prior[-3]["l"] < prior[-2]["l"] < prior[-1]["l"]):
        return None
    if any(b["c"] >= level for b in prior[-3:]):
        return None
    window = prior[-8:]
    if sum(1 for b in window if _near(b["h"], level) and b["c"] < level) < 2:
        return None
    comp = prior[-6:]
    base_vol = _mean(b["v"] for b in comp)
    if base_vol <= 0 or last["v"] + _EPS < RVOL * base_vol:
        return None
    return _setup("breakout", level, min(b["l"] for b in prior[-3:]),
                  "פריצה אמיתית: סגירה מעל הרמה עם מחזור ועם שפלים עולים", 3.0, last["end"], "window", level=level)


def breakout(bars, ctx):
    levels = []
    if ctx.get("prev_high"):
        levels.append(ctx["prev_high"])
    levels.extend(round_levels(bars[-1]["c"]))
    levels.extend(cluster_levels(bars[:-1]))
    found, seen = [], set()
    for level in levels:
        key = cents(level)
        if key in seen:
            continue
        seen.add(key)
        hit = breakout_at(bars, level)
        if hit:
            found.append(hit)
    if not found:
        return None
    return max(found, key=lambda s: s["level"])  # the ceiling actually closed over


def inside_bar(bars, ctx):
    """Slide 20–22, regular version only: trigger one tick above the mother high, stop one tick under its low.
    Slide 09: a long entry wants an uptrend, or the end of a decline sitting on a level."""
    if len(bars) < 5:
        return None
    mother, ins = bars[-2], bars[-1]
    if not is_inside(mother, ins):
        return None
    prior = bars[:-1]
    on_level = _nearest(ins, support_levels(bars, ctx), tags_support) is not None
    if not (window_uptrend(prior) or (after_decline(prior) and on_level)):
        return None
    return _setup("inside", mother["h"], mother["l"], "נר פנימי, הגרסה הרגילה: טריגר וסטופ על נר האם",
                  1.0, ins["end"], "window")


def shooting_star(bars, ctx):
    """Slide 15, for buyers only: a shooting star on resistance is a reason not to enter, or to exit.
    A cash account cannot short it, so this is a caution and not a bracket."""
    if len(bars) < 4 or not is_shooting_star(bars[-1]):
        return None
    if not after_rise(bars[:-1]):
        return None
    level = _nearest(bars[-1], resistance_levels(bars, ctx), tags_resistance)
    if level is None:
        return None
    return {"setup": "shooting_star", "warn": True, "score": 0.0, "level": level, "note":
            "כוכב נופל על התנגדות: סיבה לא להיכנס, או לצאת מעסקה פתוחה. לא פקודה — חשבון מזומן לא מוכר בשורט.",
            "candle_end": bars[-1]["end"], "trigger_high": bars[-1]["h"], "stop_anchor": bars[-1]["l"]}


def signals(bars, ctx):
    """Every long setup on the last candle, best first. A VWAP pullback already is the reversal, so it is not
    also reported as a bare hammer. NR7, doji, the aggressive inside bar and ORB are not setups."""
    if not bars:
        return []
    out = []
    vwap = vwap_pullback(bars)
    if vwap:
        out.append(vwap)
    else:
        out.extend(_hammer_or_engulf(bars, ctx))
    for fn in (bull_flag, lambda b: breakout(b, ctx), lambda b: inside_bar(b, ctx)):
        hit = fn(bars)
        if hit:
            out.append(hit)
    return sorted(out, key=lambda s: -s["score"])


def valid_until(candle_end, mode):
    """Slide 16: a reversal order is cancelled if the next candle does not trade the high.
    A breakout, flag or inside bar waits until the end of the window it was found in."""
    if mode == "next":
        return ticket.hhmm(ticket.mins(candle_end) + 5)
    return "11:30" if candle_end <= "11:30" else "15:45"


def build(setup, atr, risk_dollars, equity=None, max_position_value=None, adv_shares=None):
    """The bracket, or {"ok": False}. The stop is never moved off the pattern (slides 16 and 18).
    VWAP only: R must already lie in the volatility band, or there is no ticket (slide 36)."""
    entry = cents(setup["trigger_high"] + TICK)
    stop = cents(setup["stop_anchor"] - TICK)
    if stop >= entry:
        return {"ok": False, "reason": "stop not below entry"}
    r = cents(entry - stop)
    if setup.get("band") and atr:
        floor = max(STOP_ATR_FLOOR * atr, STOP_MIN_PCT * entry)
        cap = max(STOP_ATR_CAP * atr, floor)
        if r < floor - _EPS or r > cap + _EPS:
            return {"ok": False, "reason": "stop outside the volatility band"}
    end, mode = setup.get("candle_end"), setup.get("valid_mode", "window")
    until = valid_until(end, mode) if end else None
    if end and until and not until > end:
        return {"ok": False, "reason": "no time left on the order"}
    limit = cents(entry + LIMIT_OFFSET)
    if equity:
        risk_dollars = equity * RISK_PCT
        cash = equity  # slide 51: a cash account, no leverage, no buying power beyond settled cash
    else:
        cash = max_position_value
    shares = int(risk_dollars / r + _EPS) if r > 0 else 0
    if cash:
        shares = min(shares, int(cash / limit + _EPS))
    if adv_shares:
        shares = min(shares, int(adv_shares + _EPS))
    if shares < 1:
        return {"ok": False, "reason": "shares below 1"}
    commission = 2 * max(COMM_MIN, COMM_PER_SHARE * shares)
    cost_r = commission / (shares * r)
    if cost_r > COST_MAX_R + _EPS:
        return {"ok": False, "reason": "commissions eat the risk"}
    return {"ok": True, "setup": setup["setup"], "note": setup["note"], "score": setup["score"],
            "entry_stop": entry, "limit": limit, "stop_loss": stop, "target": cents(entry + TARGET_R * r),
            "R": r, "r_floored": False, "shares": shares, "risk": round(shares * r, 2),
            "commission": round(commission, 2), "cost_r": cost_r, "time_exit": TIME_EXIT,
            "valid_until": until, "valid_mode": mode, "atr": atr, "spring": setup.get("spring", False),
            "warn": False, "level": setup.get("level")}
