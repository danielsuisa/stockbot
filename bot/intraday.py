"""Intraday candlestick screener. Alerts follow the study deck "קריאת נרות ופקודות Bracket" (bot/deck.py):
hammer and bullish engulfing after a decline on a level, the regular inside bar, a close-confirmed breakout,
VWAP pullback (09:40–11:30) and bull flag. Each buy alert carries the deck's bracket: buy stop-limit one tick
above the trigger, stop one tick under the invalidation, target at twice that risk. A shooting star on resistance
is a caution only — a cash account does not short — and it is off unless INTRADAY_SHOW_WARN=1. Nothing is sent to a broker.

Stage 1, 09:38 New York: "stocks in play" (universe: Alpaca assets on NYSE/Nasdaq/Arca, $5-$500, 20-session
average volume >= 1M shares and $20M, ATR14 >= 2% of the close, gap up >= 2% or pre-market volume >= 10% of
the average day; a gap down is excluded). The list is sent once, when it is built. From 09:55 the watchlist is
re-ranked by the 09:30-09:35 SIP bar's relative volume (Zarattini & Aziz 2024, the top 20 by this measure)
and that second copy of the list is not sent.

Stage 2, every 5 minutes 09:40-11:30 and 15:00-15:45: completed 5-minute candles (IEX feed, no lag). Size is 1% of
pinned equity (slide 51), else the fixed fallback, and never more than the cash. Time exit 15:55 New York.
Every buy ticket is journaled and its outcome filled in after the close (paper only). The schedule files are unchanged.

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

from bot import alpaca, common, deck, list_tickets, ticket
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
GAP_PCT = 0.02  # (open / previous close) - 1: a gap up priced overnight. A gap down is not in play.
PRE_VOL_SHARE = 0.10  # pre-market volume >= 10% of the average day: in play without a gap
OPEN_RELVOL, STRONG_RELVOL = 1.0, 5.0  # 09:30-09:35 volume / its mean over RELVOL_SESSIONS: in play (Z&A), strong (Cameron)
RELVOL_SESSIONS = 14
WATCH = 20  # the watchlist (Zarattini & Aziz: the top 20 stocks in play)
TARGET_R = deck.TARGET_R
MAX_ADV_SHARE, INTRADAY_ADV = 0.01, 0.85  # shares <= 1% of the intraday volume (the close auction's ~12% is not there by 15:55)
LATE_FROM = "15:00"  # the afternoon window: only names trending up all day (close-to-15:30 trend beats the opening bar, Baltussen 2021)
TICK = deck.TICK
TIME_EXIT = "15:55"
MAX_TICKETS = 5  # a scan sends at most this many, by score
EXCHANGES = {"NYSE", "NASDAQ", "ARCA"}
FUND_WORDS = ("ETF", "ETN", "FUND", "TRUST", "INDEX", "PORTFOLIO", "SHARES")
WINDOWS = {"morning": ("09:40", "11:30"), "power": ("15:00", "15:45")}
STEP = 5  # minutes between scans
# ---------- long-only unified scanner (merged with the "unusual volume" alert) ----------
# A candle setup is sent only when unusual volume CONFIRMS it, the move is up and the trend is up. Volume alone never
# alerts (the standalone screener preset unusual_volume_liquid is disabled by default). Tunable without code changes:
VOL_CONFIRM = float(common.env("INTRADAY_VOL_CONFIRM", "1.5"))  # trigger candle volume / mean of the session's earlier candles
OPEN_RELVOL_MIN = float(common.env("INTRADAY_OPEN_RELVOL_MIN", "1.5"))  # opening-bar relative volume, once measured
SHOW_WARN = common.env("INTRADAY_SHOW_WARN", "0") == "1"  # shooting-star cautions (not a long setup): off by default


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


def _ctx(atr, prev_close, prev_high, prev_low):
    return {"atr": atr, "prev_close": prev_close, "prev_high": prev_high, "prev_low": prev_low}


def setups(bars5, atr, prev_close, prev_high=None, prev_low=None):
    """Deck setups on the last completed candle, best first, plus a shooting-star caution when one is there."""
    ctx = _ctx(atr, prev_close, prev_high, prev_low)
    found = deck.signals(bars5, ctx)
    star = deck.shooting_star(bars5, ctx)
    if star and SHOW_WARN:
        found.append(star)
    return found


def with_vwap(bars1, bars5):
    """Each completed 5-minute candle gets the session VWAP at its close."""
    for b in bars5:
        b["vwap"] = ticket.vwap(bars1, "09:30", b["end"])
    return bars5


def candles(bars1, now_hhmm):
    """Completed regular-session 5-minute candles by `now_hhmm` (a candle whose end is at or before now)."""
    bars5 = ticket.to5([b for b in bars1 if "09:30" <= b[0] < "16:00"])
    return with_vwap(bars1, [b for b in bars5 if b["end"] <= now_hhmm])


# ---------- the bracket ----------
def bracket(symbol, s, atr, risk, max_position_value=None, adv=None, equity=None):
    """The deck's bracket for setup `s`, or {"ok": False, "reason"}. A caution has no order."""
    if s.get("warn"):
        return {"ok": True, "symbol": symbol, "warn": True, "setup": s["setup"], "note": s["note"], "score": 0,
                "level": s.get("level"), "atr": atr}
    adv_shares = MAX_ADV_SHARE * INTRADAY_ADV * adv if adv else None
    got = deck.build(s, atr, risk, equity=equity, max_position_value=max_position_value, adv_shares=adv_shares)
    got["symbol"] = symbol
    return got


def sizing():
    """(dollar risk, cash cap, equity). Pinned equity sizes at 1% of the account (slide 51); otherwise a fixed $100
    and no cash cap, and the card says the equity was not set."""
    s = list_tickets.load_sizing()
    if not s:
        return 100.0, None, None
    return s["E"] * deck.RISK_PCT, s["E"], s["E"]


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
            out[sym] = {"close": close, "high": rows[-1][2], "low": rows[-1][3], "atr": atr, "adv": adv, "advd": advd}
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
        if gap >= 0 and (gap >= GAP_PCT or pre_rel >= PRE_VOL_SHARE):  # long only: a gap down is not in play for a buyer
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
    """Stage 1 -> [{symbol, gap, pre_rel, open, atr, prev_close, prev_high, prev_low, adv}] (the top WATCH)."""
    before = [d for d in sorted(cal) if d < day]
    prev_day, start = before[-1], before[-max(ADV_SESSIONS, 30) - 1]
    stats = daily_stats(universe(), prev_day, start)
    rows = in_play(stats, day)[:WATCH * 3]
    for r in rows:
        st = stats[r["symbol"]]
        r.update(atr=st["atr"], prev_close=st["close"], prev_high=st["high"], prev_low=st["low"], adv=st["adv"])
    return rows[:WATCH], before


def rerank(watch, day, before):
    """From 09:50: re-rank the watchlist by the opening 5-minute bar's relative volume (stocks in play, Z&A 2024)."""
    rel = open_relvol([w["symbol"] for w in watch], day, before[-RELVOL_SESSIONS:])
    for w in watch:
        w["open_relvol"] = rel.get(w["symbol"])
    kept = [w for w in watch if w["open_relvol"] is None or w["open_relvol"] >= OPEN_RELVOL]
    return sorted(kept, key=lambda w: -(w.get("open_relvol") or 0))


# ---------- stage 2: the scan ----------
def long_confirmed(b5, w):
    """The merge rule -> (ok, reason or candle rel vol): uptrend (close above VWAP and above the day's open), and
    unusual volume on an UP candle: the heavier of the last two candles (the trigger, or the mother / pole right
    before a quiet inside or flag candle) must be green and >= VOL_CONFIRM x the mean of the session's earlier
    candles; the opening bar's relative volume, once measured, >= OPEN_RELVOL_MIN. A spike on a red candle never alerts."""
    last = b5[-1]
    if not (last["c"] > (last.get("vwap") or float("inf")) and last["c"] > b5[0]["o"]):
        return False, "no uptrend"
    if len(b5) < 3:
        return False, "no volume confirmation"
    heavy = max(b5[-2:], key=lambda b: b["v"])
    prior = [b["v"] for b in b5[:-2]]
    rv = heavy["v"] / (sum(prior) / len(prior)) if sum(prior) else None
    if heavy["c"] <= heavy["o"]:
        return False, "red candle"
    if rv is None or rv < VOL_CONFIRM:
        return False, "no volume confirmation"
    if w.get("open_relvol") is not None and w["open_relvol"] < OPEN_RELVOL_MIN:
        return False, "weak opening volume"
    return True, rv


def scan(watch, day, now_hhmm, risk, max_pos, equity=None):
    """One pass over the watchlist -> [tickets] by score, best first. One buy per symbol; a shooting-star caution
    can ride along. With pinned equity the size is 1% of cash, not the 20% position cap of the other ticket path."""
    syms = [w["symbol"] for w in watch]
    bars = alpaca.live_bars(syms, day, "09:30", now_hhmm)
    out = []
    for w in watch:
        b5 = candles(bars.get(w["symbol"], []), now_hhmm)
        if not b5:
            continue
        ok, cand_rv = long_confirmed(b5, w)
        if not ok:
            continue  # long only, and unusual volume is a confirmation inside the setup, never an alert by itself
        buys = 0
        for s in setups(b5, w["atr"], w.get("prev_close"), w.get("prev_high"), w.get("prev_low")):
            if buys and not s.get("warn"):
                continue  # one buy per symbol: the best setup that produces an order
            t = bracket(w["symbol"], s, w["atr"], risk, None if equity else max_pos, w.get("adv"), equity)
            if t["ok"]:
                t.update(gap=w.get("gap", 0.0), pre_rel=w.get("pre_rel", 0.0), open_relvol=w.get("open_relvol"),
                         at=now_hhmm, last=b5[-1]["c"], vwap=b5[-1].get("vwap"), candle_relvol=round(cand_rv, 2))
                rv = w.get("open_relvol") or w.get("pre_rel", 0) * 10
                t["score"] = t.get("score", 0) + min(rv, STRONG_RELVOL) / STRONG_RELVOL
                out.append(t)
                buys += not t.get("warn")
    return sorted(out, key=lambda t: -t["score"])


# ---------- messages ----------
# The ticket's label by setup: what the engine measured (T2: VWAP and flag setups; ZBA and T2: ORB 5), or untested.
UNTESTED = "מחוץ למסלול שנבדק"
LABELS = {"vwap_hammer": "נבדק: תוחלת שלילית (T2)", "vwap_engulf": "נבדק: תוחלת שלילית (T2)",
          "flag": "נבדק: תוחלת שלילית (T2)", "orb5": "נבדק: תוחלת שלילית (ZBA, T2)"}


def label(setup):
    return f"תווית: {LABELS.get(setup, UNTESTED)} · כרטיס למחקר בלבד"


SETUP_NAMES = {"orb5": "פריצת טווח פתיחה 5 דק׳", "orb15": "פריצת טווח פתיחה 15 דק׳", "vwap_hammer": "פטיש על VWAP",
               "vwap_engulf": "בליעה שורית על VWAP", "flag": "דגל שורי", "red_to_green": "מאדום לירוק",
               "hammer": "פטיש על תמיכה", "engulf": "בליעה שורית על תמיכה", "inside": "נר פנימי",
               "breakout": "פריצה מעל רמה", "shooting_star": "כוכב נופל על התנגדות"}


def _pct(x):
    return code(f"{abs(x) * 100:.1f}%")


def il_time(day, hhmm):
    t = dt.datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=NY).astimezone(common.IL)
    return f"{t:%H:%M}"


def _validity(t, day):
    until = t.get("valid_until")
    if not until:
        return None
    when = f"{code(until)} ניו־יורק ({code(il_time(day, until))} ישראל)"
    if t.get("valid_mode") == "next":
        return f"תוקף: הנר הבא בלבד, עד {when}. אם המחיר לא עובר את השיא עד אז, מבטלים את הפקודה"
    return f"תוקף: עד {when}, ואז ביטול אם הפקודה לא הופעלה"


def ticket_text(t, day, E=None):
    P = t["entry_stop"]
    rel = f" · נפח פתיחה יחסי {code(format(t['open_relvol'], '.1f'))}" if t.get("open_relvol") else ""
    gap = ("פער " + ("מעלה " if t["gap"] >= 0 else "מטה ") + _pct(t["gap"]))
    cost = ""
    if t.get("commission") is not None:
        cost = (f"עלות עמלות משוערת: {code(price(t['commission']))} לשתי פקודות "
                f"({code(format(t['cost_r'], '.2f'))} מיחידת הסיכון, תקרה {code('0.60')})")
    lines = [
        f"🎫 כרטיס ל-{code(t['symbol'])} · {SETUP_NAMES[t['setup']]} · נר {code(t['at'])} ניו־יורק ({code(il_time(day, t['at']))} ישראל)",
        f"למה: {t['note']} · {gap} · נפח לפני פתיחה {_pct(t['pre_rel'])} מיום ממוצע{rel}",
        *([f"אישור לונג: מעל VWAP ומעל פתיחת היום, נר ירוק בנפח פי {code(format(t['candle_relvol'], '.1f'))} מהנרות הקודמים"]
          if t.get("candle_relvol") else []),
        f"מחיר אחרון {code(price(t['last']))}" + (f" · VWAP {code(price(t['vwap']))}" if t.get("vwap") else ""),
        f"כניסה: קניית פריצה — טריגר {code(price(P))} · לימיט {code(price(t['limit']))}",
        f"סטופ לוס: {code(price(t['stop_loss']))} (סיכון {code(price(t['R']))} למניה"
        + (", הורחב למינימום: 10% מה־ATR או 0.3% מהמחיר" if t.get("r_floored") else "") + ")",
        f"יעד: {code(price(t['target']))} (פי {code(format(TARGET_R, 'g'))} מהסיכון). יחס סיכוי לסיכון: שתיים ליחידה אחת",
        f"יציאה בזמן: {code(t['time_exit'])} ניו־יורק ({code(il_time(day, t['time_exit']))} ישראל) אם לא נסגר קודם",
        f"כמות: {code(t['shares'])} מניות · סיכון {code(price(t['risk']))}"
        + (f" ({code(format(t['risk'] / E * 100, '.2f') + '%')} מההון)" if E else " (סיכון קבוע: לא נקבע הון, /equity)")
        + (f" · שווי עד {code(price(round(t['shares'] * t['limit'], 2)))}" if t.get("limit") else ""),
        ("רשימת הבדיקה: הקשר, רמה, צורה, טריגר וסטופ, כמות, עלות, זמן, וכלל קבוע — כולם עוברים"
         if E else
         "רשימת הבדיקה: הקשר, רמה, צורה, טריגר וסטופ, עלות, זמן, וכלל קבוע — עוברים. "
         "הכמות לפי סיכון קבוע, כי לא נקבע הון"),
        "ב־IBKR: חשבון מזומן, לונג בלבד, בלי מינוף. פקודת bracket אחת — BUY STP LMT (טריגר ולימיט שלמעלה), "
        "ילדים: SELL STP סטופ לוס ו־SELL LMT יעד, OCA, תוקף DAY. הסטופ לא זז אחורה",
        label(t["setup"]),
    ]
    if cost:
        lines.insert(-2, cost)
    valid = _validity(t, day)
    if valid:
        lines.insert(-2, valid)
    return "\n".join(lines)


def warn_text(t, day):
    """A shooting star on resistance: no bracket, not actionable in a cash account (slide 15)."""
    level = f" · רמה {code(price(t['level']))}" if t.get("level") else ""
    return "\n".join([
        f"אזהרה ל-{code(t['symbol'])} · {SETUP_NAMES[t['setup']]} · נר {code(t['at'])} ניו־יורק ({code(il_time(day, t['at']))} ישראל)",
        f"למה: {t['note']}{level}",
        f"מחיר אחרון {code(price(t['last']))}" + (f" · VWAP {code(price(t['vwap']))}" if t.get("vwap") else ""),
        "אין פקודת קנייה ואין פקודת שורט. בחשבון מזומן אי אפשר למכור בשורט.",
    ])


def list_text(tickets, day, now_hhmm, E):
    head = f"🕯️ סורק נרות יומי · יום {common.weekday(day)} {code(il_date(day))} · סריקה ב־{code(il_time(day, now_hhmm))} ישראל"
    if not tickets:
        return head + "\nאין תבנית חדשה בנר הזה."
    parts = [(warn_text(t, day) if t.get("warn") else ticket_text(t, day, E)) for t in tickets]
    return head + "\n\n" + "\n\n".join(parts)


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


def followup(state, now=None):
    """Fill in today's outcomes (SIP bars: complete after 16:15) and return the stats text. Outcomes read only bars
    before 16:00, so they are filled once the free plan serves them (16:16 New York); the request ends 16 minutes ago
    (alpaca.CUTOFF: never cached as a whole day) until 20:16, when the day is final. Before 16:16: nothing is filled."""
    day = state["day"]
    todo = [j for j in state["journal"] if not j.get("outcome")]
    now = (now or _now()).astimezone(NY)
    cut = (now - SIP_DELAY).strftime("%H:%M") if now.date().isoformat() == day else "23:59"
    if todo and cut >= "16:00":
        live = cut < "20:00"
        if live:
            alpaca.CUTOFF[day] = cut
        try:
            bars = alpaca.minute_bars(sorted({j["symbol"] for j in todo}), day)
        finally:
            if live:
                alpaca.CUTOFF.pop(day, None)
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


SIP_DELAY = dt.timedelta(minutes=16)  # the free plan refuses SIP data newer than 15 minutes
READY = "09:36"  # the 04:00-09:20 pre-market SIP bars the watchlist reads are served from here


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
        state["watch"] = rerank(state["watch"], day, before)  # re-rank only; the 09:38 list was already sent
    fresh = [t for t in scan(state["watch"], day, now_hhmm, risk, max_pos, E)
             if not any(k.split(":")[0] == t["symbol"] for k in state["sent"])]  # one message per ticker per day
    buys = [t for t in fresh if not t.get("warn")][:MAX_TICKETS]
    tickets = buys + [t for t in fresh if t.get("warn")]
    for t in tickets:
        state["sent"][f"{t['symbol']}:{t['setup']}"] = now_hhmm
        if not t.get("warn"):
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
        send(followup(state, now), a.dry)
        if not a.dry:
            save(state)
        return 0
    if now.strftime("%H:%M") < READY:  # the free plan would refuse the pre-market bars (HTTP 403)
        send(f"הסורק היומי זמין מ־{code(READY)} ניו־יורק ({code(il_time(day, READY))} ישראל): "
             f"נתוני הטרום־מסחר נפתחים 15 דקות אחרי 09:20.", a.dry)
        return 0

    if a.symbol:
        sym = a.symbol.upper()
        stats = daily_stats([sym], before[-1], before[-31])
        if sym not in stats:
            send(f"{code(sym)} מחוץ ליקום הסורק (מחיר 5–500$, נפח ממוצע מעל מיליון ו־20M$, ATR מעל 2%).", a.dry)
            return 0
        rows = [r for r in in_play(stats, day) if r["symbol"] == sym] or [{"symbol": sym, "gap": 0.0, "pre_rel": 0.0}]
        rows[0].update(atr=stats[sym]["atr"], prev_close=stats[sym]["close"], prev_high=stats[sym]["high"],
                       prev_low=stats[sym]["low"], adv=stats[sym]["adv"])
        risk, max_pos, E = sizing()
        got = scan(rows, day, now.strftime("%H:%M"), risk, max_pos, E)
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
