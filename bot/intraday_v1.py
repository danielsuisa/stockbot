"""The retired v1 setups of the intraday screener (opening-range breakout 5 and 15, VWAP pullback, bull flag,
red-to-green), as shipped on 2026-10-17 and measured by the engine (T2, closed by ADR 0018). The live screener uses
the deck's rules instead (bot/deck.py, 2026-10-21). This module is frozen: bot/intraday_count.py replays these exact
detectors for its pre-registered 2024 count, so nothing here may change."""
from bot import ticket

TICK = 0.01
LATE_FROM = "15:00"  # the afternoon window: only names trending up all day
CANDLE_RELVOL = 1.5  # the trigger candle's volume vs the session's mean 5-minute volume so far (a real breakout)
HAMMER_WICK, ENGULF = 2.0, 1.0  # hammer: lower wick >= 2 x body and close in the upper third; engulfing: body covers
VWAP_NEAR = 0.003  # a pullback "touches" VWAP when its low is within 0.3% of it
FLAG_PULLBACK, FLAG_MAX_BARS = 0.5, 4  # a flag keeps more than half of the impulse, over 2-4 small candles
IMPULSE_ATR = 0.5  # the impulse: a rise of at least half an ATR14 over 1-3 candles


def body(b):
    return abs(b["c"] - b["o"])


def is_bull(b):
    return b["c"] > b["o"]


def is_hammer(b):
    rng = b["h"] - b["l"]
    if rng <= 0:
        return False
    lower = min(b["o"], b["c"]) - b["l"]
    return lower >= HAMMER_WICK * max(body(b), rng * 0.05) and b["c"] >= b["h"] - rng / 3


def is_bull_engulfing(prev, b):
    return (is_bull(b) and not is_bull(prev) and b["o"] <= prev["c"] and b["c"] >= prev["o"] * (1 + 1e-9)
            and body(b) >= ENGULF * body(prev) and body(prev) > 0)


def mean_volume(bars5):
    vols = [b["v"] for b in bars5 if b["v"] > 0]
    return sum(vols) / len(vols) if vols else 0.0


def _ticket(kind, trigger, stop, note, score):
    return {"setup": kind, "trigger_high": trigger, "stop_ref": stop, "note": note, "score": score}


def orb(bars5, minutes):
    """Opening-range breakout: the last candle closes above the high of the first `minutes` of the session, on
    volume, above VWAP; the breakout candle is the trigger. None before the range or when the break is stale."""
    n = minutes // 5
    if len(bars5) < n + 1 or bars5[0]["start"] != "09:30":
        return None
    rng, last, prev = bars5[:n], bars5[-1], bars5[-2]
    hi = max(b["h"] for b in rng)
    if not (last["c"] > hi and prev["c"] <= hi):  # the first close above the range: not a chase
        return None
    if last["v"] < CANDLE_RELVOL * mean_volume(bars5[:-1]) or last.get("vwap") and last["c"] < last["vwap"]:
        return None
    return _ticket(f"orb{minutes}", last["h"], None if minutes == 5 else last["l"],  # ORB 5: the 10% ATR stop (Z&A)
                   f"פריצת טווח הפתיחה של {minutes} דקות", 3.0)


def vwap_pullback(bars5):
    """Price above VWAP pulls back to it and prints a hammer or a bullish engulfing candle at VWAP."""
    if len(bars5) < 4:
        return None
    last, prev = bars5[-1], bars5[-2]
    vw = last.get("vwap")
    if not vw or last["c"] < vw or last["l"] > vw * (1 + VWAP_NEAR):
        return None
    if not (all(b["c"] > b.get("vwap", b["c"]) for b in bars5[-4:-1]) or prev["c"] > prev.get("vwap", 0)):
        return None
    if is_hammer(last):
        return _ticket("vwap_hammer", last["h"], last["l"], "פטיש על VWAP", 2.5)
    if is_bull_engulfing(prev, last):
        return _ticket("vwap_engulf", last["h"], min(prev["l"], last["l"]), "בליעה שורית על VWAP", 2.5)
    return None


def bull_flag(bars5, atr):
    """An impulse of >= IMPULSE_ATR x ATR over 1-3 candles, then 2-4 smaller candles that keep more than half of it
    on falling volume; the last pullback candle is the trigger (buy above its high, stop under the flag's low)."""
    if len(bars5) < 4 or not atr:
        return None
    for k in range(2, FLAG_MAX_BARS + 1):  # k pullback candles
        if len(bars5) < k + 2:
            break
        flag, before = bars5[-k:], bars5[:-k]
        for m in (1, 2, 3):  # m impulse candles
            if len(before) < m:
                break
            imp = before[-m:]
            rise = max(b["h"] for b in imp) - imp[0]["o"]
            if rise < IMPULSE_ATR * atr or not all(is_bull(b) for b in imp):
                continue
            top, flag_low = max(b["h"] for b in imp), min(b["l"] for b in flag)
            if flag_low < top - FLAG_PULLBACK * rise:
                continue
            if max(body(b) for b in flag) > 0.5 * rise or mean_volume(flag) >= mean_volume(imp):
                continue
            if bars5[-1]["h"] >= top:  # already broke out: the trigger would be a chase
                continue
            return _ticket("flag", top, flag_low, "דגל שורי אחרי דחיפה", 2.0)
    return None


def red_to_green(bars5, prev_close):
    """Opened below the previous close and the last candle is the first close back above it, on volume."""
    if len(bars5) < 3 or bars5[0]["o"] >= prev_close:
        return None
    last, prev = bars5[-1], bars5[-2]
    if not (last["c"] > prev_close >= prev["c"]) or last["v"] < CANDLE_RELVOL * mean_volume(bars5[:-1]):
        return None
    return _ticket("red_to_green", max(last["h"], prev_close + TICK), min(b["l"] for b in bars5[-3:]),
                   "מאדום לירוק: חזרה מעל הסגירה הקודמת", 2.0)


def with_vwap(bars1, bars5):
    """Each completed 5-minute candle gets the session VWAP at its close."""
    for b in bars5:
        b["vwap"] = ticket.vwap(bars1, "09:30", b["end"])
    return bars5


def candles(bars1, now_hhmm):
    """Completed regular-session 5-minute candles by `now_hhmm` (a candle whose end is at or before now)."""
    bars5 = ticket.to5([b for b in bars1 if "09:30" <= b[0] < "16:00"])
    return with_vwap(bars1, [b for b in bars5 if b["end"] <= now_hhmm])


def setups(bars5, atr, prev_close):
    """Every setup present on the last completed candle -> [ticket dict] by score, best first."""
    found = [orb(bars5, 5), orb(bars5, 15), vwap_pullback(bars5), bull_flag(bars5, atr), red_to_green(bars5, prev_close)]
    return sorted((f for f in found if f), key=lambda f: -f["score"])
