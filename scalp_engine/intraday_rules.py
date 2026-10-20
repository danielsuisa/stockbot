"""The live screener's stage-1 and stage-2 rules, copied from stockbot `bot/intraday.py` (and the helpers it uses
from `bot/ticket.py` and `bot/levels.py`) at origin/main 09bfa55c7445762cf021e6a3a1d8128527f07eba (2026-10-17),
ADR 0016 section 1. Copied, never imported: the engine does not import stockbot.

Blocks between "# >>> verbatim" and "# <<< verbatim" are the bot's text byte for byte (tests/test_intraday_rules.py
checks their SHA-256 against the bot's at that SHA). The adapters at the end replace the bot's Alpaca calls with
inputs the replay passes in. Pure functions only: no I/O."""
from types import SimpleNamespace

SOURCE_SHA = "09bfa55c7445762cf021e6a3a1d8128527f07eba"

# >>> verbatim levels.EPS
EPS = 1e-9
# <<< verbatim levels.EPS

# >>> verbatim levels.px
def px(x, ref=None):
    """An order price or amount: a cent for a stock from $1 up, $0.0001 below (plan decision 3). `ref` is the stock's
    price when x is an amount (a trail), so the amount follows the stock's scale (review m1)."""
    scale = x if ref is None else ref
    return round(x + (EPS if x >= 0 else -EPS), 2 if abs(scale) >= 1 else 4)
# <<< verbatim levels.px

MIN_SHARES = 2  # bot/levels.py: MIN_BARS, MIN_SHARES, MAX_ADV_SHARE, ADV_SESSIONS = 15, 2, 0.01, 20

# >>> verbatim ticket.mins
def mins(hhmm):
    """"HH:MM" -> minutes after midnight."""
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])
# <<< verbatim ticket.mins

# >>> verbatim ticket.hhmm
def hhmm(n):
    return f"{n // 60:02d}:{n % 60:02d}"
# <<< verbatim ticket.hhmm

# >>> verbatim ticket.to5
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
# <<< verbatim ticket.to5

# >>> verbatim ticket.vwap
def vwap(bars1, start, upto):
    """Volume-weighted average price of the minutes start <= m < upto; None without volume."""
    pv = vol = 0
    for t, o, h, l, c, v, vw in bars1:
        if start <= t < upto:
            pv += (c if vw is None else vw) * v
            vol += v
    return pv / vol if vol else None
# <<< verbatim ticket.vwap

# >>> verbatim ticket.atr14
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
# <<< verbatim ticket.atr14

levels = SimpleNamespace(px=px, EPS=EPS, MIN_SHARES=MIN_SHARES)
ticket = SimpleNamespace(mins=mins, hhmm=hhmm, to5=to5, vwap=vwap, atr14=atr14)

# >>> verbatim intraday.constants
PRICE = (5.0, 500.0)  # liquid names with real spreads; below $5 the costs ate the ZBA edge (docs/backtest)
ADV_SHARES, ADV_DOLLARS, ADV_SESSIONS = 1_000_000, 20_000_000.0, 20
ATR_PCT, ATR_ABS = 0.02, 0.50  # ATR14: >= 2% of the close (a 2:1 target fits a session) and >= $0.50 (Z&A 2024)
GAP_PCT = 0.02  # |open - previous close| / previous close: a catalyst that was priced over night
PRE_VOL_SHARE = 0.10  # pre-market volume >= 10% of the average day: in play without a gap
OPEN_RELVOL, STRONG_RELVOL = 1.0, 5.0  # 09:30-09:35 volume / its mean over RELVOL_SESSIONS: in play (Z&A), strong (Cameron)
RELVOL_SESSIONS = 14
WATCH = 20  # the watchlist (Zarattini & Aziz: the top 20 stocks in play)
TARGET_R, STOP_ATR_FLOOR, STOP_ATR_CAP = 2.0, 0.10, 1.0
STOP_MIN_PCT = 0.003  # R >= 0.3% of the entry: spread plus slippage (~0.1% on liquid $5+ names) stays under a third of R
LIMIT_ATR = 0.05  # the stop-limit's limit: entry + 5% of ATR14 (no chasing beyond it)
MAX_ADV_SHARE, INTRADAY_ADV = 0.01, 0.85  # shares <= 1% of the intraday volume (the close auction's ~12% is not there by 15:55)
LATE_FROM = "15:00"  # the afternoon window: only names trending up all day (close-to-15:30 trend beats the opening bar, Baltussen 2021)
TICK = 0.01
TIME_EXIT = "15:55"
MAX_TICKETS = 5  # a scan sends at most this many, by score
EXCHANGES = {"NYSE", "NASDAQ", "ARCA"}
FUND_WORDS = ("ETF", "ETN", "FUND", "TRUST", "INDEX", "PORTFOLIO", "SHARES")
WINDOWS = {"morning": ("09:40", "11:30"), "power": ("15:00", "15:45")}
STEP = 5  # minutes between scans
CANDLE_RELVOL = 1.5  # the trigger candle's volume vs the session's mean 5-minute volume so far (a real breakout)
HAMMER_WICK, ENGULF = 2.0, 1.0  # hammer: lower wick >= 2 x body and close in the upper third; engulfing: body covers
VWAP_NEAR = 0.003  # a pullback "touches" VWAP when its low is within 0.3% of it
FLAG_PULLBACK, FLAG_MAX_BARS = 0.5, 4  # a flag keeps more than half of the impulse, over 2-4 small candles
IMPULSE_ATR = 0.5  # the impulse: a rise of at least half an ATR14 over 1-3 candles
# <<< verbatim intraday.constants

# >>> verbatim intraday.pure
# ---------- candle shapes (pure) ----------
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


# ---------- setups (pure functions over completed 5-minute candles of the regular session) ----------
def _ticket(kind, trigger, stop, note, score):
    return {"setup": kind, "trigger_high": trigger, "stop_ref": stop, "note": note, "score": score}


def orb(bars5, minutes):
    """Opening-range breakout: the last candle closes above the high of the first `minutes` of the session, on
    volume, above VWAP; the breakout candle is the trigger. None before the range or when the break is stale."""
    n = minutes // 5
    if len(bars5) < n + 1 or bars5[0]["start"] != "09:30":
        return None
    rng, last, prev = bars5[:n], bars5[-1], bars5[-2]
    hi, lo = max(b["h"] for b in rng), min(b["l"] for b in rng)
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


# ---------- the bracket ----------
def bracket(symbol, s, atr, risk, max_position_value=None, adv=None):
    """Entry, stop, target and size for setup `s` -> ticket dict, or {"ok": False, "reason"}."""
    entry = levels.px(s["trigger_high"] + TICK)
    r_raw = entry - s["stop_ref"] + TICK if s["stop_ref"] is not None else STOP_ATR_FLOOR * atr
    floor = max(STOP_ATR_FLOOR * atr, STOP_MIN_PCT * entry) if atr else STOP_MIN_PCT * entry
    R = min(max(r_raw, floor), max(STOP_ATR_CAP * atr, floor)) if atr else max(r_raw, floor)
    if R <= 0:
        return {"ok": False, "symbol": symbol, "reason": "stop not below entry"}
    shares = int(risk / R + levels.EPS)
    if max_position_value:
        shares = min(shares, int(max_position_value / entry + levels.EPS))
    if adv:
        shares = min(shares, int(MAX_ADV_SHARE * INTRADAY_ADV * adv + levels.EPS))
    if shares < levels.MIN_SHARES:
        return {"ok": False, "symbol": symbol, "reason": "shares below 2"}
    return {"ok": True, "symbol": symbol, "setup": s["setup"], "note": s["note"], "score": s["score"],
            "entry_stop": entry, "limit": levels.px(entry + LIMIT_ATR * atr) if atr else levels.px(entry * 1.002),
            "stop_loss": levels.px(entry - R), "target": levels.px(entry + TARGET_R * R), "R": levels.px(R, ref=entry),
            "r_floored": r_raw < floor, "shares": shares, "risk": round(shares * R, 2),
            "time_exit": TIME_EXIT, "atr": atr}
# <<< verbatim intraday.pure

# ---------------------------------------------------------------- adapters (not verbatim)
# The bot's stage-1/2 functions with their Alpaca calls replaced by inputs the replay passes in. Every other line is
# the bot's logic at the SHA; differences are noted inline.

STUDY_RISK = 1e12  # study mode: the bot's "ok" test reduces to R > 0 (ADR 0016 section 1)


def daily_stats_from(daily_rows):
    """daily_stats() over {symbol: [(date, o, h, l, c, v)]} already limited to [before[-31], prev_day]."""
    out = {}
    for sym, rows in daily_rows.items():
        if len(rows) < 16:
            continue
        recent = rows[-ADV_SESSIONS:]
        adv = sum(r[5] for r in recent) / len(recent)
        advd = sum(r[4] * r[5] for r in recent) / len(recent)
        close, atr = rows[-1][4], ticket.atr14(rows, "9999")  # every row is before "9999"
        if (PRICE[0] <= close <= PRICE[1] and adv >= ADV_SHARES and advd >= ADV_DOLLARS and atr
                and atr / close >= ATR_PCT and atr >= ATR_ABS):
            out[sym] = {"close": close, "atr": atr, "adv": adv, "advd": advd}
    return out


def in_play_from(stats, pre, opens):
    """in_play(): pre = {symbol: (SIP volume 04:00-09:29, last pre-market close)} (symbols with trades only, as
    volume_since); opens = {symbol: [("09:30", o, ...)]} the IEX 09:30 5-minute bar (empty list: no trades)."""
    syms = sorted(stats)
    rows = []
    for s in syms:
        o = opens.get(s)
        open_ = o[0][1] if o else (pre.get(s) or (0, None))[1]
        if not open_:
            continue
        gap = open_ / stats[s]["close"] - 1
        pv = (pre.get(s) or (0, 0))[0]
        pre_rel = pv / stats[s]["adv"]
        if abs(gap) >= GAP_PCT or pre_rel >= PRE_VOL_SHARE:
            rows.append({"symbol": s, "gap": round(gap, 4), "pre_vol": pv, "pre_rel": round(pre_rel, 4), "open": open_})
    return sorted(rows, key=lambda r: (-r["pre_rel"], -abs(r["gap"])))


def watchlist_from(stats, pre, opens):
    """watchlist() without the calendar and the Alpaca calls -> the top WATCH rows."""
    rows = in_play_from(stats, pre, opens)[:WATCH * 3]
    for r in rows:
        r.update(atr=stats[r["symbol"]]["atr"], prev_close=stats[r["symbol"]]["close"], adv=stats[r["symbol"]]["adv"])
    return rows[:WATCH]


def open_relvol_from(symbols, today, hist):
    """open_relvol(): today = {symbol: (o, h, l, c, v) or None}, hist = [same, one per prior session]."""
    out = {}
    for s in symbols:
        past = [h[s][4] for h in hist if h.get(s)]
        now = today.get(s)
        out[s] = (now[4] / (sum(past) / len(past))) if now and past and sum(past) else None
    return out


def rerank_from(watch, rel):
    for w in watch:
        w["open_relvol"] = rel.get(w["symbol"])
    kept = [w for w in watch if w["open_relvol"] is None or w["open_relvol"] >= OPEN_RELVOL]
    return sorted(kept, key=lambda w: -(w.get("open_relvol") or 0))


def scan_from(watch, bars, now_hhmm, risk, max_pos):
    """scan() with bars = {symbol: [1-minute bars from 09:30]} passed in. Also returns every setup detected on the
    last candle of each symbol (signal candles, for the control draw)."""
    out, detected = [], {}
    for w in watch:
        b5 = candles(bars.get(w["symbol"], []), now_hhmm)
        if not b5:
            continue
        if now_hhmm >= LATE_FROM and not (b5[-1]["c"] > b5[0]["o"] and b5[-1]["c"] > (b5[-1].get("vwap") or 0)):
            continue  # the afternoon: only a day that trended up and still holds above VWAP
        found = setups(b5, w["atr"], w["prev_close"])
        if found and b5[-1]["end"] == now_hhmm:
            detected[w["symbol"]] = [s["setup"] for s in found]
        for s in found:
            t = bracket(w["symbol"], s, w["atr"], risk, max_pos, w.get("adv"))
            if t["ok"]:
                t.update(gap=w["gap"], pre_rel=w["pre_rel"], open_relvol=w.get("open_relvol"), at=now_hhmm,
                         last=b5[-1]["c"], vwap=b5[-1].get("vwap"), trigger_high=s["trigger_high"],
                         stop_ref=s["stop_ref"], candle=b5[-1]["start"])
                rv = w.get("open_relvol") or w["pre_rel"] * 10  # relative volume is the measured edge; the gap is not
                t["score"] += min(rv, STRONG_RELVOL) / STRONG_RELVOL
                out.append(t)
                break  # one ticket per symbol per scan: its best setup
    return sorted(out, key=lambda t: -t["score"]), detected


def fresh_state(day):
    return {"day": day, "watch": [], "sent": {}, "journal": [], "detected": []}


def run_scan_from(state, now_hhmm, bars, rel_fn, risk=STUDY_RISK, max_pos=None):
    """run_scan() without messages: rel_fn(symbols) -> {symbol: opening relative volume} (the 09:55 re-rank)."""
    if now_hhmm >= "09:55" and state["watch"] and not state["watch"][0].get("open_relvol"):
        state["watch"] = rerank_from(state["watch"], rel_fn([w["symbol"] for w in state["watch"]]))
    got, detected = scan_from(state["watch"], bars, now_hhmm, risk, max_pos)
    tickets = [t for t in got if f"{t['symbol']}:{t['setup']}" not in state["sent"]][:MAX_TICKETS]
    for t in tickets:
        state["sent"][f"{t['symbol']}:{t['setup']}"] = now_hhmm
        state["journal"].append(t)
    state["detected"] += [(now_hhmm, s, k) for s, ks in detected.items() for k in ks]
    return tickets


def scan_times(window):
    """The bot's scan closes for a window: start .. end every STEP minutes."""
    start, end = WINDOWS[window]
    t, out = ticket.mins(start), []
    while t <= ticket.mins(end):
        out.append(ticket.hhmm(t))
        t += STEP
    return out


def rules_sha256():
    """SHA-256 of this module's source: written into every signed set (ADR 0016 section 1)."""
    import hashlib
    from pathlib import Path
    return hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
