"""Intraday candlestick screener: the long setups the most consistently profitable US day traders screen for, on
5-minute candles, each with a full bracket-order ticket (research and parameter sources:
docs/research/intraday-strategies-2026-10-17.md). Long only; nothing is sent to a broker.

Stage 1, 09:38 New York: "stocks in play" (universe: Alpaca assets on NYSE/Nasdaq/Arca, $5-$500, 20-session
average volume >= 1M shares and $20M, ATR14 >= 2% of the close, gap >= 2% either way or pre-market volume >= 10% of
the average day); the top WATCH by pre-market relative volume are the watchlist. From 09:55 the watchlist is
re-ranked by the 09:30-09:35 SIP bar's relative volume (Zarattini & Aziz 2024, the top 20 by this measure).

Stage 2, every 5 minutes 09:40-11:30 and 15:00-15:45: completed 5-minute candles (IEX feed, no lag) of the
watchlist -> setups: opening-range breakout (5 and 15 minutes), VWAP pullback with a bullish reversal candle
(hammer or bullish engulfing), bull flag (micro pullback after an impulse), red-to-green. Each setup gives a
bracket: buy stop-limit above the trigger candle, stop loss below it (floored at 10% of ATR14, capped at 1 x ATR14),
take profit at TARGET_R x risk, time exit 15:55 New York. Size = risk per trade (equity x TICKET_RISK_PCT, as
/ticket) / risk per share. Every ticket is journaled and its outcome filled in after the close (paper only).

    python -m bot.intraday --mode morning|power   # loop over the window, send new tickets
    python -m bot.intraday --mode once            # one scan now (the /intraday now command)
    python -m bot.intraday --mode followup        # outcomes of today's tickets, stats
    python -m bot.intraday --symbol AAPL          # one symbol against the setups now
    --dry: print instead of sending, write nothing"""
import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

from bot import alpaca, common, equity, levels, list_tickets, ticket
from bot.common import code, il_date, price

NY = alpaca.NY
STATE = "intraday_state.json"
SOURCE = ("⚠️ כרטיס מחושב בלבד, לא ייעוץ השקעות: הבוט לא שולח פקודות. ניירת בלבד.\n"
          f"מקור: נרות של {code('Alpaca')} — {code('IEX')} בזמן אמת לנרות, {code('SIP')} (באיחור 15 דקות) לנפח "
          "· סורק הנרות היפניים")

# ---------- parameters (docs/research/intraday-strategies-2026-10-17.md, section "screener") ----------
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


# ---------- files ----------
def path(name):
    return Path(common.env("INTRADAY_DIR", str(common.DATA))) / name


def load(default):
    p = path(STATE)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def save(obj):
    p = path(STATE)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def fresh_state(day):
    return {"day": day, "watch": [], "sent": {}, "journal": [], "stats": None}


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


def sizing():
    """Risk per trade: E x p (as /ticket) -> (risk, max position value, E) or the fixed default without E."""
    s = list_tickets.load_sizing()
    if not s:
        return 100.0, None, None
    p, _ = levels.p_used(s["p_raw"])
    return s["E"] * p, levels.POSITION_CAP * s["E"], s["E"]


# ---------- stage 1: stocks in play ----------
def universe():
    out = []
    for a in alpaca.assets():
        sym, name = a["symbol"], (a.get("name") or "").upper()
        if a.get("status") == "active" and a.get("exchange") in EXCHANGES and sym.isalpha() and len(sym) <= 5 \
                and not any(w in name.split() for w in FUND_WORDS):
            out.append(sym)
    return sorted(set(out))


def daily_stats(symbols, prev_day, start):
    """{symbol: {"close", "atr", "adv", "advd"}} for symbols with the liquidity and range the screen asks for."""
    out = {}
    for sym, rows in alpaca.daily(symbols, start, prev_day).items():
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


def in_play(stats, day):
    """Gap and pre-market volume from the live feeds -> [{symbol, gap, pre_vol, pre_rel, open}] sorted by pre_rel."""
    syms = sorted(stats)
    pre = alpaca.volume_since(syms, day, "04:00", "09:20")  # SIP 15-minute bars: older than 15 minutes by 09:36
    opens = alpaca.live_bars(syms, day, "09:30", "09:30", "5Min")  # IEX: the open print's price, live
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


def open_relvol(symbols, day, sessions_before):
    """The 09:30-09:35 SIP bar's volume vs its mean over `sessions_before` -> {symbol: rel vol} (None: no history)."""
    today = alpaca.opening_bars(symbols, day)
    hist = [alpaca.opening_bars(symbols, d) for d in sessions_before]
    out = {}
    for s in symbols:
        past = [h[s][4] for h in hist if h.get(s)]
        now = today.get(s)
        out[s] = (now[4] / (sum(past) / len(past))) if now and past and sum(past) else None
    return out


def watchlist(day, cal):
    """Stage 1 -> [{symbol, gap, pre_rel, open, atr, prev_close, adv}] (the top WATCH)."""
    before = [d for d in sorted(cal) if d < day]
    prev_day, start = before[-1], before[-max(ADV_SESSIONS, 30) - 1]
    stats = daily_stats(universe(), prev_day, start)
    rows = in_play(stats, day)[:WATCH * 3]
    for r in rows:
        r.update(atr=stats[r["symbol"]]["atr"], prev_close=stats[r["symbol"]]["close"], adv=stats[r["symbol"]]["adv"])
    return rows[:WATCH], before


def rerank(watch, day, before):
    """From 09:50: re-rank the watchlist by the opening 5-minute bar's relative volume (stocks in play, Z&A 2024)."""
    rel = open_relvol([w["symbol"] for w in watch], day, before[-RELVOL_SESSIONS:])
    for w in watch:
        w["open_relvol"] = rel.get(w["symbol"])
    kept = [w for w in watch if w["open_relvol"] is None or w["open_relvol"] >= OPEN_RELVOL]
    return sorted(kept, key=lambda w: -(w.get("open_relvol") or 0))


# ---------- stage 2: the scan ----------
def scan(watch, day, now_hhmm, risk, max_pos):
    """One pass over the watchlist -> [tickets] by score, best first."""
    syms = [w["symbol"] for w in watch]
    bars = alpaca.live_bars(syms, day, "09:30", now_hhmm)
    out = []
    for w in watch:
        b5 = candles(bars.get(w["symbol"], []), now_hhmm)
        if not b5:
            continue
        if now_hhmm >= LATE_FROM and not (b5[-1]["c"] > b5[0]["o"] and b5[-1]["c"] > (b5[-1].get("vwap") or 0)):
            continue  # the afternoon: only a day that trended up and still holds above VWAP
        for s in setups(b5, w["atr"], w["prev_close"]):
            t = bracket(w["symbol"], s, w["atr"], risk, max_pos, w.get("adv"))
            if t["ok"]:
                t.update(gap=w["gap"], pre_rel=w["pre_rel"], open_relvol=w.get("open_relvol"), at=now_hhmm,
                         last=b5[-1]["c"], vwap=b5[-1].get("vwap"))
                rv = w.get("open_relvol") or w["pre_rel"] * 10  # relative volume is the measured edge; the gap is not
                t["score"] += min(rv, STRONG_RELVOL) / STRONG_RELVOL
                out.append(t)
                break  # one ticket per symbol per scan: its best setup
    return sorted(out, key=lambda t: -t["score"])


# ---------- messages ----------
SETUP_NAMES = {"orb5": "פריצת טווח פתיחה 5 דק׳", "orb15": "פריצת טווח פתיחה 15 דק׳", "vwap_hammer": "פטיש על VWAP",
               "vwap_engulf": "בליעה שורית על VWAP", "flag": "דגל שורי", "red_to_green": "מאדום לירוק"}


def _pct(x):
    return code(f"{abs(x) * 100:.1f}%")


def il_time(day, hhmm):
    t = dt.datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=NY).astimezone(common.IL)
    return f"{t:%H:%M}"


def ticket_text(t, day, E=None):
    P = t["entry_stop"]
    rel = f" · נפח פתיחה יחסי {code(format(t['open_relvol'], '.1f'))}" if t.get("open_relvol") else ""
    gap = ("פער " + ("מעלה " if t["gap"] >= 0 else "מטה ") + _pct(t["gap"]))
    lines = [
        f"🎫 כרטיס ל-{code(t['symbol'])} · {SETUP_NAMES[t['setup']]} · נר {code(t['at'])} ניו־יורק ({code(il_time(day, t['at']))} ישראל)",
        f"למה: {t['note']} · {gap} · נפח לפני פתיחה {_pct(t['pre_rel'])} מיום ממוצע{rel}",
        f"מחיר אחרון {code(price(t['last']))}" + (f" · VWAP {code(price(t['vwap']))}" if t.get("vwap") else ""),
        f"כניסה: קניית פריצה — סטופ {code(price(P))} · לימיט {code(price(t['limit']))} (תקף להיום בלבד)",
        f"סטופ לוס: {code(price(t['stop_loss']))} (סיכון {code(price(t['R']))} למניה"
        + (", הורחב למינימום: 10% מה־ATR או 0.3% מהמחיר" if t["r_floored"] else "") + ")",
        f"יעד: {code(price(t['target']))} (פי {code(format(TARGET_R, 'g'))} מהסיכון) · אחרי רווח של יחידת סיכון אחת: "
        "סטופ לנקודת הכניסה או ל־VWAP, הגבוה מביניהם",
        f"יציאה בזמן: {code(t['time_exit'])} ניו־יורק ({code(il_time(day, t['time_exit']))} ישראל) אם לא נסגר קודם",
        f"כמות: {code(t['shares'])} מניות · סיכון {code(price(t['risk']))}"
        + (f" ({code(format(t['risk'] / E * 100, '.2f') + '%')} מההון)" if E else " (סיכון קבוע: לא נקבע הון, /equity)"),
        "ב־IBKR: פקודת bracket אחת — BUY STP LMT (סטופ ולימיט שלמעלה), ילדים: SELL STP סטופ לוס ו־SELL LMT יעד, "
        "OCA, תוקף DAY · סטופ מתחת לנר ההדק ולא מתחת ליעד",
    ]
    return "\n".join(lines)


def list_text(tickets, day, now_hhmm, E):
    head = f"🕯️ סורק נרות יומי · יום {common.weekday(day)} {code(il_date(day))} · סריקה ב־{code(il_time(day, now_hhmm))} ישראל"
    if not tickets:
        return head + "\nאין תבנית חדשה בנר הזה."
    return head + "\n\n" + "\n\n".join(ticket_text(t, day, E) for t in tickets)


def watch_text(watch, day):
    lines = [f"👀 מניות במשחק היום · יום {common.weekday(day)} {code(il_date(day))} · {code(len(watch))} מניות"]
    for w in watch:
        rel = f" · נפח פתיחה יחסי {code(format(w['open_relvol'], '.1f'))}" if w.get("open_relvol") else ""
        lines.append(f"מניה {code(w['symbol'])} · פער {'מעלה' if w['gap'] >= 0 else 'מטה'} {_pct(w['gap'])} · "
                     f"נפח לפני פתיחה {_pct(w['pre_rel'])} מיום ממוצע{rel} · ATR {_pct(w['atr'] / w['prev_close'])}")
    return "\n".join(lines)


# ---------- the journal and its outcomes ----------
def outcome(t, bars1):
    """Paper outcome of ticket `t` from the session's SIP 1-minute bars after its candle: filled at the stop-limit
    (first minute whose high >= entry stop, fill = max(entry, open) if open <= limit), then target / stop / time
    exit by minute highs and lows (both touched in one minute = stop: conservative)."""
    after = [b for b in bars1 if b[0] >= t["at"] and b[0] < "16:00"]
    fill = None
    for m, o, h, l, c, v, vw in after:
        if fill is None:
            if h >= t["entry_stop"]:
                f = max(t["entry_stop"], o)
                if f <= t["limit"]:
                    fill = f
                else:
                    return {"result": "not filled (gapped past limit)", "r": 0.0}
            continue
        if l <= t["stop_loss"]:
            return {"result": "stop", "fill": fill, "r": round((t["stop_loss"] - fill) / t["R"], 2)}
        if h >= t["target"]:
            return {"result": "target", "fill": fill, "r": round((t["target"] - fill) / t["R"], 2)}
        if m >= t["time_exit"]:
            return {"result": "time", "fill": fill, "r": round((c - fill) / t["R"], 2)}
    if fill is None:
        return {"result": "not filled", "r": 0.0}
    return {"result": "time", "fill": fill, "r": round((after[-1][4] - fill) / t["R"], 2)}


def followup(state):
    """Fill in today's outcomes (SIP bars: complete after 16:15) and return the stats text."""
    day = state["day"]
    todo = [j for j in state["journal"] if not j.get("outcome")]
    if todo:
        bars = alpaca.minute_bars(sorted({j["symbol"] for j in todo}), day)
        for j in todo:
            j["outcome"] = outcome(j, bars.get(j["symbol"], []))
    return stats_text(state)


def stats_text(state):
    js = [j for j in state["journal"] if j.get("outcome")]
    if not js:
        return "אין עדיין תוצאות לכרטיסי הסורק היומי."
    filled = [j for j in js if j["outcome"].get("fill")]
    wins = [j for j in filled if j["outcome"]["r"] > 0]
    tot = sum(j["outcome"]["r"] for j in filled)
    lines = [f"📊 סורק הנרות · יום {code(il_date(state['day']))}: {code(len(js))} כרטיסים, {code(len(filled))} מולאו, "
             f"{code(len(wins))} רווחיים",
             f"סך הכול {code(format(tot, '+.2f').replace('+', '').replace('-', ''))} יחידות סיכון "
             + ("רווח" if tot >= 0 else "הפסד") + " (כל כרטיס סיכן יחידה אחת)"]
    for j in js:
        o = j["outcome"]
        word = {"stop": "סטופ", "target": "יעד", "time": "יציאה בזמן"}.get(o["result"], "לא מולא")
        r = f" · {code(format(abs(o['r']), '.2f'))} יחידות " + ("רווח" if o["r"] >= 0 else "הפסד") if o.get("fill") else ""
        lines.append(f"מניה {code(j['symbol'])} · {SETUP_NAMES[j['setup']]} · {word}{r}")
    return "\n".join(lines)


# ---------- the run ----------
def _now():
    return dt.datetime.now(NY)


def _sleep_until(t_ny):
    while (d := (t_ny - _now()).total_seconds()) > 0:
        time.sleep(min(d, 30))


def chart_buttons(symbols, per_row=4):
    """Inline URL buttons, one per symbol, opening its TradingView chart (no listener round-trip needed)."""
    keys = [{"text": f"📈 {s}", "url": f"https://www.tradingview.com/chart/?symbol={s}"} for s in dict.fromkeys(symbols)]
    return {"inline_keyboard": [keys[i:i + per_row] for i in range(0, len(keys), per_row)]} if keys else None


def send(text, dry, signal=False, symbols=()):
    if dry:
        print(text, end="\n\n")
    else:
        common.send(text, signal=signal, source=SOURCE, buttons=chart_buttons(symbols))


def run_scan(state, day, before, now_hhmm, dry):
    risk, max_pos, E = sizing()
    if now_hhmm >= "09:55" and state["watch"] and not state["watch"][0].get("open_relvol"):
        state["watch"] = rerank(state["watch"], day, before)
        send(watch_text(state["watch"], day), dry, symbols=[w["symbol"] for w in state["watch"]])
    tickets = [t for t in scan(state["watch"], day, now_hhmm, risk, max_pos)
               if f"{t['symbol']}:{t['setup']}" not in state["sent"]][:MAX_TICKETS]
    for t in tickets:
        state["sent"][f"{t['symbol']}:{t['setup']}"] = now_hhmm
        state["journal"].append(t)
    if tickets:
        send(list_text(tickets, day, now_hhmm, E), dry, signal=True, symbols=[t["symbol"] for t in tickets])
    return tickets


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["morning", "power", "once", "followup", "watch"], default="once")
    ap.add_argument("--symbol")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)
    now = _now()
    day = now.date().isoformat()
    cal = alpaca.calendar((now.date() - dt.timedelta(days=60)).isoformat(), day)
    if day not in cal:
        send("היום אין מסחר בניו־יורק, הסורק היומי לא רץ.", a.dry)
        return 0
    state = load(None)
    if not state or state.get("day") != day:
        state = fresh_state(day)
    before = [d for d in sorted(cal) if d < day]

    if a.mode == "followup":
        send(followup(state), a.dry)
        if not a.dry:
            save(state)
        return 0

    if a.symbol:
        sym = a.symbol.upper()
        stats = daily_stats([sym], before[-1], before[-31])
        if sym not in stats:
            send(f"{code(sym)} מחוץ ליקום הסורק (מחיר 5–500$, נפח ממוצע מעל מיליון ו־20M$, ATR מעל 2%).", a.dry)
            return 0
        rows = [r for r in in_play(stats, day) if r["symbol"] == sym] or [{"symbol": sym, "gap": 0.0, "pre_rel": 0.0}]
        rows[0].update(atr=stats[sym]["atr"], prev_close=stats[sym]["close"])
        risk, max_pos, E = sizing()
        got = scan(rows, day, now.strftime("%H:%M"), risk, max_pos)
        send(list_text(got, day, now.strftime("%H:%M"), E), a.dry, signal=bool(got), symbols=[t["symbol"] for t in got])
        return 0

    if not state["watch"]:
        state["watch"], before = watchlist(day, cal)
        send(watch_text(state["watch"], day), a.dry, symbols=[w["symbol"] for w in state["watch"]])
        if not a.dry:
            save(state)
    if a.mode == "watch":
        return 0
    if a.mode == "once":
        run_scan(state, day, before, now.strftime("%H:%M"), a.dry)
        if not a.dry:
            save(state)
        return 0
    start, end = WINDOWS[a.mode]
    t = dt.datetime.fromisoformat(f"{day}T{start}").replace(tzinfo=NY)
    t = max(t, now.replace(second=0, microsecond=0) + dt.timedelta(minutes=(STEP - now.minute % STEP) % STEP))
    while t.strftime("%H:%M") <= end:
        _sleep_until(t + dt.timedelta(seconds=25))  # the candle's last minute bar is in
        try:
            run_scan(state, day, before, t.strftime("%H:%M"), a.dry)
            if not a.dry:
                save(state)
        except Exception as e:  # one failed scan must not end the window
            common.trace(e)
            send(f"⚠️ סריקה ב־{code(t.strftime('%H:%M'))} ניו־יורק נכשלה: {code(str(e)[:120])}", a.dry)
        t += dt.timedelta(minutes=STEP)
    return 0


if __name__ == "__main__":
    sys.exit(main())
