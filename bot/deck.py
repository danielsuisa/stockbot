"""The deck screener's rules: the user's presentation "נרות יפניים ופקודות Bracket" (Slides artifact
Tc8VHK4HKcsPNhZwXp1pro) as pure functions over 5-minute candles. Spec, with the slide id of every rule and whether
each number is the presentation's, the bot's or a default: docs/deck-screener-spec.md. Long only; nothing here does
I/O and nothing is sent to a broker.

A candle is {"start", "end", "o", "h", "l", "c", "v", "n" (minutes with trades), "usable", "vwap"}; a slot without
any minute is {"start", "end", "usable": False, "o": None}. Every setup is checked against the deck's 8-item
checklist (slide c7-checklist): a single "no" means no ticket."""
import hashlib
import json
import math

from bot import ticket

# ---------- parameters (docs/deck-screener-spec.md lists each with its slide and its source) ----------
TICK = 0.01  # c2-entry
TARGET_R = 2.0  # c2-entry, c5-flag-order
STOP_ATR_FLOOR, STOP_ATR_CAP, STOP_MIN_PCT = 0.10, 1.0, 0.003  # c5-pullback "min and max distance" = the bot's rule
LIMIT_ATR = 0.05  # the bot's stop-limit offset (x ATR14)
RISK_USD, CASH = 25.0, 2500.0  # c7-size: 1% of a $2,500 cash account; shares never above what the cash buys
COST_MAX_R = 0.3  # c7-checklist item 6, at the user's 0.3R (c7-winrate's example cost)
HAMMER_WICK, UPPER_WICK, ENGULF = 2.0, 0.5, 1.0  # c2-hammer (2 x body), default (upper wick), the bot's engulf ratio
DOJI_BODY = 0.05  # default: body <= 5% of the range (the bot's minimum hammer body)
NEAR = 0.003  # the bot's VWAP_NEAR: "on a level" tolerance
TOUCHES = 2  # c4-levels: "twice or more"
DECLINE_BARS, DECLINE_WINDOW, DECLINE_ATR, ATR5_N = 3, 6, 1.0, 14  # default: "end of a decline"
CANDLE_RELVOL = 1.5  # the bot's breakout-candle volume ratio
RETEST_BARS = 6  # default
COMPRESS_N, COMPRESS_K, COMPRESS_LOWS = 5, 3, 3  # default: >= 3 of the last 5 highs under the level, 3 rising lows
IMPULSE_ATR, IMPULSE_MAX = 0.5, 3  # the bot's flag pole: >= 0.5 x ATR14 over 1-3 up candles
FLAG_PULLBACK, FLAG_MIN, FLAG_MAX = 0.5, 2, 4  # c5-flag (< half the pole); the bot's 2-4 flag candles
NR_N = 7  # c3-nr7
PRE_MIN_TRADES = 3  # default: a pre-market candle needs trades in 3 of its 5 minutes
ROUND_STEPS = ((20.0, 1.0), (100.0, 5.0), (math.inf, 10.0))  # default: round numbers by price
WY_RANGE_N, WY_RANGE_PCT, WY_DECLINE_N, WY_DECLINE_PCT, WY_SPRING_N = 20, 0.15, 60, 0.20, 5  # default (c6-*)
OPEN, FIRST, LAST = "09:30", "08:00", "10:30"  # VWAP from the open (c5-vwap); new tickets 08:00-10:30 (user)

# IBKR Pro Tiered, US stocks (docs/deck-screener-spec.md section "Cost", with the URLs): per share, minimum and
# maximum per order; third-party fees per share; regulatory fees on the sale.
IB_PER_SHARE, IB_MIN, IB_MAX_PCT = 0.0035, 0.35, 0.01
EXCHANGE_FEE, CLEARING_FEE, CAT_FEE = 0.0030, 0.00020, 0.000003  # per share and leg; both legs remove liquidity
PASS_THROUGH = 0.000175 + 0.00056  # NYSE and FINRA pass-through, x the commission
SEC_FEE, TAF, TAF_MAX = 0.0000206, 0.000195, 9.79  # SEC per $ sold; FINRA TAF per share sold (2026 rate), cap a trade
STOP_LIMIT_ATR = LIMIT_ATR  # before 09:30 the stop loss is a SELL STP LMT (a plain STP does not work): limit offset

SETUPS = ("hammer", "engulf", "vwap_hammer", "vwap_engulf", "inside", "nr7", "breakout", "retest", "flag")
CHAPTER = {"hammer": 2, "engulf": 2, "inside": 3, "nr7": 3, "breakout": 4, "retest": 4, "vwap_hammer": 5,
           "vwap_engulf": 5, "flag": 5}
VWAP_SETUPS = ("vwap_hammer", "vwap_engulf")
CHECKS = ("context", "level", "shape", "levels", "size", "cost", "time", "rule")


def rules():
    """Every parameter above, by name -> the frozen rule set item 8 compares against."""
    return {k: v for k, v in globals().items() if k.isupper() and k not in ("RULES_ID",)
            and isinstance(v, (int, float, str, tuple, dict))}


def rules_id(r=None):
    return hashlib.sha256(json.dumps(r or rules(), sort_keys=True, default=str).encode()).hexdigest()[:12]


RULES_ID = "5bbe40952f6f"  # the id of the rule set written in docs/deck-screener-spec.md (item 8: "fixed rule")


# ---------- candles ----------
def candles(bars1, now, start="04:00"):
    """Today's 1-minute bars ("HH:MM", o, h, l, c, v, vw) -> the completed 5-minute candles (end <= `now`) from the
    first minute at or after `start`, one per slot. The VWAP is the session's from 09:30 (None before)."""
    by = {}
    for b in bars1:
        if start <= b[0]:
            by.setdefault(ticket.mins(b[0]) // 5 * 5, []).append(b)
    if not by:
        return []
    out, pv, vol, open_ = [], 0.0, 0.0, ticket.mins(OPEN)
    for s in range(min(by), ticket.mins(now) - 4, 5):
        rows = by.get(s, [])
        if s >= open_:
            for _, o, h, l, c, v, vw in rows:
                pv += (c if vw is None else vw) * v
                vol += v
        vwap = pv / vol if s >= open_ and vol else None
        if not rows:
            out.append({"start": ticket.hhmm(s), "end": ticket.hhmm(s + 5), "o": None, "usable": False, "vwap": vwap})
            continue
        n = sum(1 for r in rows if r[5] > 0)
        out.append({"start": ticket.hhmm(s), "end": ticket.hhmm(s + 5), "o": rows[0][1], "h": max(r[2] for r in rows),
                    "l": min(r[3] for r in rows), "c": rows[-1][4], "v": sum(r[5] for r in rows), "n": n,
                    "usable": n >= (PRE_MIN_TRADES if s < open_ else 1), "vwap": vwap})
    return out


def body(c):
    return abs(c["c"] - c["o"])


def rng(c):
    return c["h"] - c["l"]


def up(c):
    return c["c"] > c["o"]


def down(c):
    return c["c"] < c["o"]


def session_mean(cs, i):
    """The mean volume of the usable candles before `i` in the same part of the day (pre-market or the session)."""
    pre = cs[i]["start"] < OPEN
    vols = [c["v"] for c in cs[:i] if c["usable"] and (c["start"] < OPEN) == pre]
    return sum(vols) / len(vols) if vols else None


# ---------- shapes (c2-hammer, c2-engulf, c2-doji, c3-inside, c3-nr7) ----------
def is_hammer(c):
    """Lower wick >= 2 x body, a small upper wick (<= half the body), the body in the upper third of the range."""
    r = rng(c)
    if r <= 0:
        return False
    lo, hi = min(c["o"], c["c"]), max(c["o"], c["c"])
    return lo - c["l"] >= HAMMER_WICK * body(c) and c["h"] - hi <= UPPER_WICK * body(c) and lo >= c["h"] - r / 3


def is_shooting_star(c):
    """The Hammer's mirror: upper wick >= 2 x body, lower wick <= half the body, the body in the lower third."""
    r = rng(c)
    if r <= 0:
        return False
    lo, hi = min(c["o"], c["c"]), max(c["o"], c["c"])
    return c["h"] - hi >= HAMMER_WICK * body(c) and lo - c["l"] <= UPPER_WICK * body(c) and hi <= c["l"] + r / 3


def is_doji(c):
    return body(c) <= DOJI_BODY * rng(c)


def is_engulfing(c1, c2):
    """Candle 1 down; candle 2 up, its body covering candle 1's body."""
    return (down(c1) and up(c2) and c2["o"] <= c1["c"] and c2["c"] >= c1["o"]
            and body(c2) >= ENGULF * body(c1))


def is_inside(mother, c):
    return c["h"] < mother["h"] and c["l"] > mother["l"]


# ---------- context (c1-trend, c2-hammer) ----------
def atr5(series):
    """ATR14 of usable 5-minute candles (yesterday's session then today's), None with fewer than 15."""
    rows = [c for c in series if c["usable"]][-(ATR5_N + 1):]
    if len(rows) < ATR5_N + 1:
        return None
    return sum(max(rng(b), abs(b["h"] - a["c"]), abs(b["l"] - a["c"])) for a, b in zip(rows, rows[1:])) / ATR5_N


def uptrend(cs):
    """The last two swing highs and the last two swing lows (2 candles on each side, known by now) both rising."""
    sw = ticket.swings([c for c in cs if c["usable"]])
    hi = [s["price"] for s in sw if s["kind"] == "high"]
    lo = [s["price"] for s in sw if s["kind"] == "low"]
    return len(hi) >= 2 and len(lo) >= 2 and hi[-1] > hi[-2] and lo[-1] > lo[-2]


def end_of_decline(cs, p, a5):
    """Before pattern candle `p`: 3 candles each with a lower low, or a fall >= 1 ATR14(5m) over the last 6 candles
    (the highest high of the 6 down to the pattern's low)."""
    four = cs[max(0, p - DECLINE_BARS - 1):p]
    if len(four) == DECLINE_BARS + 1 and all(c["usable"] for c in four) and \
            all(b["l"] < a["l"] for a, b in zip(four, four[1:])):
        return True
    six = cs[max(0, p - DECLINE_WINDOW):p]
    lows = [c["l"] for c in cs[p:] if c["o"] is not None]  # the pattern's candles (usability is checklist item 3)
    return bool(a5) and bool(lows) and len(six) == DECLINE_WINDOW and all(c["usable"] for c in six) and \
        max(c["h"] for c in six) - min(lows) >= DECLINE_ATR * a5


# ---------- levels (c4-levels, c2-hammer, c5-vwap) ----------
def round_levels(c):
    step = next(s for top, s in ROUND_STEPS if c["c"] < top)
    lo, hi = c["l"] * (1 - NEAR), c["h"] * (1 + NEAR)
    return [k * step for k in range(math.ceil(lo / step), math.floor(hi / step) + 1)]


def levels(cs, p, yday, prev):
    """The levels known before candle `p` -> {"support": [(price, name)], "resistance": [(price, name)]}: yesterday's
    high, low and close; the pre-market high and low so far; repeated touches (>= 2 earlier candle highs or lows within
    NEAR, today and yesterday); today's earlier swing lows ("a previous low", c2-hammer). VWAP and round numbers are
    added per candle (on_level)."""
    both = []
    if prev:
        both += [(prev["h"], "yday_high"), (prev["l"], "yday_low"), (prev["c"], "yday_close")]
    pre = [c for c in cs[:p] if c["o"] is not None and c["start"] < OPEN]
    if pre:
        both += [(max(c["h"] for c in pre), "pre_high"), (min(c["l"] for c in pre), "pre_low")]
    earlier = [c for c in list(yday) + cs[:p] if c["usable"]]
    highs, lows = [c["h"] for c in earlier], [c["l"] for c in earlier]
    res = sorted({(h, "touches") for h in highs if sum(abs(x - h) <= NEAR * h for x in highs) >= TOUCHES})
    sup = sorted({(x, "touches") for x in lows if sum(abs(y - x) <= NEAR * x for y in lows) >= TOUCHES})
    sw = ticket.swings([c for c in cs[:p] if c["usable"]])
    sup += [(s["price"], "previous_low") for s in sw if s["kind"] == "low"]
    return {"support": both + sup, "resistance": both + res}


def touches(c, lv, side):
    """`c` is on level `lv`: its low (support) or high (resistance) within NEAR of it, or its range contains it."""
    x = c["l"] if side == "support" else c["h"]
    return abs(x - lv) <= NEAR * lv or c["l"] <= lv <= c["h"]


def on_level(c, lv, side):
    """The first level `c` is on -> (price, name), or None. VWAP (from 09:30) and round numbers count both ways."""
    extra = [(r, "round") for r in round_levels(c)] + ([(c["vwap"], "vwap")] if c.get("vwap") else [])
    for price, name in lv[side] + extra:
        if touches(c, price, side):
            return price, name
    return None


# ---------- setups: each -> {"setup", "p" (first pattern candle), "k" (pattern candles), "trigger", "stop", ...} ----
def _pat(setup, cs, p, trigger, stop, level=None, note=""):
    return {"setup": setup, "p": p, "k": len(cs) - p, "trigger": round(trigger + TICK, 2), "stop": round(stop - TICK, 2),
            "level": level, "note": note}


def hammer(cs, lv, a5):
    """c2-hammer / c2-entry: a Hammer at the end of a decline, on support; buy above its high, stop under its low."""
    c, p = cs[-1], len(cs) - 1
    if c["o"] is None or not is_hammer(c) or not end_of_decline(cs, p, a5):
        return None
    at = on_level(c, lv, "support")
    return _pat("hammer", cs, p, c["h"], c["l"], at) if at else None


def engulf(cs, lv, a5):
    """c2-engulf: a bullish Engulfing at the end of a decline, on support; buy above candle 2, stop under the lower low."""
    if len(cs) < 2 or cs[-2]["o"] is None or cs[-1]["o"] is None or not is_engulfing(cs[-2], cs[-1]):
        return None
    p = len(cs) - 2
    if not end_of_decline(cs, p, a5):
        return None
    at = on_level(cs[-2], lv, "support") or on_level(cs[-1], lv, "support")
    return _pat("engulf", cs, p, cs[-1]["h"], min(cs[-1]["l"], cs[-2]["l"]), at) if at else None


def vwap_pullback(cs):
    """c5-pullback: above VWAP and rising, a pullback to VWAP, a Hammer or an Engulfing on it (from 09:30 only)."""
    c = cs[-1]
    if c["o"] is None or not c.get("vwap") or c["start"] < OPEN:
        return None
    vw = {"support": [(c["vwap"], "vwap")], "resistance": []}
    two = len(cs) >= 2 and cs[-2]["o"] is not None and cs[-2]["start"] >= OPEN and is_engulfing(cs[-2], c)
    k = 2 if two else 1 if is_hammer(c) else 0
    if not k or c["c"] < c["vwap"]:
        return None
    before = cs[-k - 3:-k]
    if len(before) < 3 or any(b["o"] is None or b["start"] < OPEN or b["c"] <= b["vwap"] for b in before):
        return None
    pat = cs[-k:]
    if not any(touches(b, b["vwap"], "support") for b in pat):
        return None
    return _pat("vwap_engulf" if two else "vwap_hammer", cs, len(cs) - k, c["h"], min(b["l"] for b in pat),
                (c["vwap"], "vwap"))


def inside(cs):
    """c3-inside, c3-bracket (regular version): buy above the mother bar's high, stop under its low."""
    if len(cs) < 2 or cs[-2]["o"] is None or cs[-1]["o"] is None or not is_inside(cs[-2], cs[-1]):
        return None
    return _pat("inside", cs, len(cs) - 2, cs[-2]["h"], cs[-2]["l"])


def nr7(cs):
    """c3-nr7: the narrowest range of the last 7 candles; buy above its high, stop under its low."""
    last = cs[-NR_N:]
    if len(last) < NR_N or any(c["o"] is None for c in last):
        return None
    c = last[-1]
    if not all(rng(c) < rng(b) for b in last[:-1]):
        return None
    return _pat("nr7", cs, len(cs) - 1, c["h"], c["l"])


def breakout(cs, lv):
    """c4-breakout, c4-entries: compression under a resistance level (>= 3 of the last 5 highs within NEAR below it,
    none above it, the last 3 lows rising) -> a pending buy stop at the level + a tick, stop under the lowest of the
    last 3 lows."""
    last = cs[-COMPRESS_N:]
    if len(last) < COMPRESS_N or any(c["o"] is None for c in last):
        return None
    lows = [c["l"] for c in last[-COMPRESS_LOWS:]]
    if not all(b > a for a, b in zip(lows, lows[1:])):
        return None
    top = max(c["h"] for c in last)
    for price, name in sorted(lv["resistance"] + [(r, "round") for r in round_levels(last[-1])]):
        if price < top or last[-1]["c"] >= price:
            continue
        near = sum(price * (1 - NEAR) <= c["h"] <= price for c in last)
        if near >= COMPRESS_K:
            return _pat("breakout", cs, len(cs) - COMPRESS_N, price, min(lows), (price, name))
    return None


def retest(cs, lv_at):
    """c4-flip, c4-entries: a candle closed above a resistance level on volume (>= CANDLE_RELVOL x the session's mean
    candle volume before it); within 6 candles the price came back to the level from above and printed a Hammer or an
    Engulfing on it -> buy above that candle's high, stop under its low. `lv_at(i)` gives the levels known before i."""
    c = cs[-1]
    if c["o"] is None:
        return None
    two = len(cs) >= 2 and cs[-2]["o"] is not None and is_engulfing(cs[-2], c)
    k = 2 if two else 1 if is_hammer(c) else 0
    if not k:
        return None
    n = len(cs)
    for i in range(n - k - 1, max(0, n - k - 1 - RETEST_BARS), -1):  # the breakout candle, newest first
        b, a = cs[i], cs[i - 1] if i else None
        if b["o"] is None or a is None or a["o"] is None:
            continue
        mean = session_mean(cs, i)
        if not mean or b["v"] < CANDLE_RELVOL * mean:
            continue
        for price, name in lv_at(i)["resistance"]:
            if not (b["c"] > price >= a["c"]):
                continue
            between = cs[i + 1:n - k]
            if any(x["o"] is not None and x["c"] < price for x in between):
                continue  # it broke back down: not a return "from above"
            if c["c"] > price and any(touches(x, price, "support") for x in cs[-k:]):
                return _pat("retest", cs, n - k, c["h"], min(x["l"] for x in cs[-k:]), (price, name))
    return None


def flag(cs, atr):
    """c5-flag, c5-flag-order: a pole of 1-3 up candles rising >= 0.5 x ATR14 on high volume (mean >= CANDLE_RELVOL x
    the session's mean before it), then 2-4 flag candles under the pole's top that give back less than half of it, on
    lower volume -> buy above the flag's high, stop under its low."""
    if not atr:
        return None
    for k in range(FLAG_MIN, FLAG_MAX + 1):
        fl = cs[-k:]
        if len(cs) < k + 2 or any(c["o"] is None for c in fl):
            continue
        for m in range(1, IMPULSE_MAX + 1):
            pole = cs[-k - m:-k]
            if len(pole) < m or any(c["o"] is None or not up(c) for c in pole):
                continue
            top, rise = max(c["h"] for c in pole), max(c["h"] for c in pole) - pole[0]["o"]
            mean = session_mean(cs, len(cs) - k - m)
            pv, fv = sum(c["v"] for c in pole) / m, sum(c["v"] for c in fl) / k
            if rise < IMPULSE_ATR * atr or not mean or pv < CANDLE_RELVOL * mean:
                continue
            hi, lo = max(c["h"] for c in fl), min(c["l"] for c in fl)
            if hi > top or top - lo >= FLAG_PULLBACK * rise or fv >= pv or max(body(c) for c in fl) > 0.5 * rise:
                continue
            return _pat("flag", cs, len(cs) - k, hi, lo, note=f"pole {pole[0]['o']:.2f}-{top:.2f}")
    return None


# ---------- the bracket and its cost (c2-entry, c7-size, c7-stoplimit, c7-winrate) ----------
def bracket(pat, atr):
    """Trigger, limit, stop, target and shares -> dict. The stop distance is the bot's min/max rule (c5-pullback):
    floored at max(10% ATR14, 0.3% of the price), capped at 1 ATR14."""
    entry = pat["trigger"]
    r_raw = round(entry - pat["stop"], 2)
    floor = max(STOP_ATR_FLOOR * atr, STOP_MIN_PCT * entry) if atr else STOP_MIN_PCT * entry
    R = round(min(max(r_raw, floor), max(STOP_ATR_CAP * atr, floor)) if atr else max(r_raw, floor), 2)
    limit = round(entry + (LIMIT_ATR * atr if atr else TICK), 2)
    shares = 0
    if R > 0:
        shares = min(int(RISK_USD / R + 1e-9), int(CASH / limit + 1e-9))
    stop = round(entry - R, 2)
    return {"entry_stop": entry, "limit": limit, "stop_loss": stop, "target": round(entry + TARGET_R * R, 2),
            "stop_limit": round(stop - (STOP_LIMIT_ATR * atr if atr else TICK), 2),
            "R": R, "r_raw": r_raw, "r_floored": r_raw < R, "r_capped": r_raw > R, "shares": shares,
            "risk": round(shares * R, 2), "atr": atr}


def commission(shares, price):
    return min(max(IB_MIN, IB_PER_SHARE * shares), IB_MAX_PCT * shares * price)


def cost_usd(b, spread):
    """One round trip at IBKR Pro Tiered: the buy and the sale (at the target: the larger sale), commissions, exchange,
    clearing and pass-through fees on both legs, SEC and FINRA TAF on the sale, plus the full spread on every share."""
    q, buy, sell = b["shares"], b["entry_stop"], b["target"]
    legs = commission(q, buy) + commission(q, sell)
    third = 2 * q * (EXCHANGE_FEE + CLEARING_FEE + CAT_FEE) + PASS_THROUGH * legs
    reg = SEC_FEE * q * sell + min(TAF * q, TAF_MAX)
    return legs + third + reg + spread * q


# ---------- Wyckoff context (c6-*): a tag, never a ticket ----------
def wyckoff(daily):
    """Daily bars (date, o, h, l, c, v) before today -> tags: "accumulation" (the last 20 sessions in a range <= 15%
    after a fall >= 20% over the 60 before them), "spring" (a session of the last 5 under the low of the 20 before it
    that closed back above it)."""
    tags = []
    if len(daily) >= WY_RANGE_N + WY_DECLINE_N:
        box, prior = daily[-WY_RANGE_N:], daily[-WY_RANGE_N - WY_DECLINE_N:-WY_RANGE_N]
        lo = min(r[3] for r in box)
        if (max(r[2] for r in box) - lo) / lo <= WY_RANGE_PCT and prior[-1][4] / prior[0][4] - 1 <= -WY_DECLINE_PCT:
            tags.append("accumulation")
    for i in range(max(WY_RANGE_N, len(daily) - WY_SPRING_N), len(daily)):
        floor = min(r[3] for r in daily[i - WY_RANGE_N:i])
        if daily[i][3] < floor <= daily[i][4]:
            tags.append("spring")
            break
    return tags


# ---------- the checklist (c7-checklist) ----------
def evaluate(symbol, cs, ctx, now):
    """Every setup on the last completed candle, each checked against the 8 items -> {"tickets": [...], "blocked":
    [(setup, failed item, detail)], "filter": None or why the symbol is skipped this scan}.

    ctx: {"atr" (daily ATR14), "prev" {"h","l","c"} or None, "yday" (yesterday's 5-minute candles), "spread" (dollars,
    None without a usable quote), "time_exit" ("HH:MM"), "daily" (bars for the Wyckoff tag)}."""
    out = {"tickets": [], "blocked": [], "filter": None}
    if not cs or cs[-1]["o"] is None:
        return out
    yday, atr = ctx.get("yday") or [], ctx.get("atr")
    n = len(cs)
    lv_at = lambda i: levels(cs, i, yday, ctx.get("prev"))  # noqa: E731
    last_lv = lv_at(n - 1)
    if is_shooting_star(cs[-1]) and on_level(cs[-1], last_lv, "resistance"):
        out["filter"] = "shooting star on resistance"
        return out
    a5 = atr5(list(yday) + cs)
    found = [hammer(cs, last_lv, a5), engulf(cs, lv_at(n - 2), a5) if n >= 2 else None,
             vwap_pullback(cs) if now >= OPEN else None, inside(cs), nr7(cs), breakout(cs, last_lv), retest(cs, lv_at),
             flag(cs, atr)]
    for pat in sorted((f for f in found if f), key=lambda f: CHAPTER[f["setup"]]):
        items, b = checklist(pat, cs, ctx, now, a5, lv_at(pat["p"]))
        bad = [k for k in CHECKS if not items[k][0]]
        if bad:
            out["blocked"].append((pat["setup"], bad[0], items[bad[0]][1]))
            continue
        out["tickets"].append({"symbol": symbol, "setup": pat["setup"], "note": pat["note"], **b,
                               "level": pat["level"], "checks": {k: items[k][1] for k in CHECKS},
                               "cost_r": items["cost"][2], "wyckoff": wyckoff(ctx.get("daily") or []),
                               "at": now, "last": cs[-1]["c"], "vwap": cs[-1].get("vwap"),
                               "time_exit": ctx["time_exit"], "valid_until": LAST, "premarket": now < OPEN})
    return out


def checklist(pat, cs, ctx, now, a5, lv):
    """The 8 items -> ({item: (passed, detail[, value])}, bracket)."""
    p, pc = pat["p"], cs[pat["p"]:]
    b = bracket(pat, ctx.get("atr"))
    trend = uptrend(cs)
    ctx_ok = trend or end_of_decline(cs, p, a5)
    lvl = pat["level"] or next((x for c in pc for x in (on_level(c, lv, "support"), on_level(c, lv, "resistance")) if x),
                               None)
    usable = all(c["usable"] for c in pc)
    shape_ok = usable and not is_doji(cs[-1])
    two = b["R"] > 0 and b["stop_loss"] < b["entry_stop"] and b["entry_stop"] > cs[-1]["c"]
    spread = ctx.get("spread")
    c_r = round(cost_usd(b, spread) / (b["shares"] * b["R"]), 3) if spread is not None and b["shares"] > 0 else None
    in_time = FIRST <= now < LAST and bool(ctx.get("time_exit"))  # the order needs time left before 10:30 and (now >= OPEN or pat["setup"] not in VWAP_SETUPS)
    fixed = pat["setup"] in SETUPS and rules_id() == RULES_ID
    items = {
        "context": (ctx_ok, "מגמה עולה" if trend else "סוף ירידה" if ctx_ok else "אין מגמה עולה ואין סוף ירידה"),
        "level": (bool(lvl), lvl[1] if lvl else "אין רמה"),
        "shape": (shape_ok, "נר לא שמיש לפני הפתיחה" if not usable else "Doji" if is_doji(cs[-1]) else "התבנית מלאה"),
        "levels": (two, "טריגר וסטופ במספרים"),
        "size": (b["shares"] >= 1, f"{b['shares']} shares"),
        "cost": (c_r is not None and c_r <= COST_MAX_R + 1e-9, "אין ציטוט" if c_r is None else f"{c_r:.2f}", c_r),
        "time": (in_time, f"{FIRST}-{LAST}"),
        "rule": (fixed, RULES_ID),
    }
    if pat["level"] is None and lvl:
        pat["level"] = lvl
    return items, b
