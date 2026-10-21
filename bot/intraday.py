"""Intraday screener: the user's presentation "נרות יפניים ופקודות Bracket" as a live screener (rules and their slides:
docs/deck-screener-spec.md; the user's decision: docs/research/deck-screener-decision-2026-10-21.md). Long only;
nothing is sent to a broker: every ticket is a recommendation the user enters by hand in IBKR.

Stage 1, from 08:00 New York: "stocks in play" (universe: Alpaca assets on NYSE/Nasdaq/Arca, $5-$500, 20-session
average volume >= 1M shares and $20M, ATR14 >= 2% of the close, gap >= 2% either way or pre-market volume >= 10% of
the average day); the top WATCH by pre-market relative volume are the watchlist. Before the open it is rebuilt every
5 minutes from the SIP pre-market bars that are at least 15 minutes old (the free plan); from 09:55 it is re-ranked
by the 09:30-09:35 SIP bar's relative volume (Zarattini & Aziz 2024, the top 20 by this measure).

Stage 2, every 5 minutes 08:00-10:30: the completed 5-minute candles of the watchlist (IEX feed, no lag; a pre-market
candle counts only with trades in 3 of its 5 minutes) -> the deck's setups (bot/deck.py): Hammer and bullish
Engulfing on support, Inside bar, NR7, breakout above a level and its Retest, VWAP pullback (from 09:30), Bull flag.
Each must pass the deck's 8-item checklist, including a round-trip cost of at most 0.3 of its risk. The ticket is a
bracket: buy stop-limit a tick above the trigger, stop loss a tick under the pattern, target 2 x risk, valid to 10:30,
time exit 5 minutes before the close. Size = $25 / risk per share, at most what $2,500 of cash buys.

    python -m bot.intraday --mode morning     # loop over the window, send new tickets
    python -m bot.intraday --mode once        # one scan now (the /intraday now command)
    python -m bot.intraday --mode exit        # the time-exit reminder (5 minutes before the close)
    python -m bot.intraday --mode followup    # outcomes of today's tickets, stats
    python -m bot.intraday --symbol AAPL      # one symbol against the setups now
    --dry: print instead of sending, write nothing"""
import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

from bot import alpaca, common, deck, ticket
from bot.common import code, il_date, price
# The retired v1 setups (bot/intraday_v1.py) keep these names for bot/intraday_count.py's frozen 2024 count.
from bot.intraday_v1 import LATE_FROM, candles, setups  # noqa: F401

NY = alpaca.NY
STATE = "intraday_state.json"
SOURCE = ("⚠️ כרטיס מחושב בלבד, לא ייעוץ השקעות: הבוט לא שולח פקודות. ניירת בלבד.\n"
          f"מקור: נרות של {code('Alpaca')} — {code('IEX')} בזמן אמת לנרות ולציטוט, {code('SIP')} (באיחור 15 דקות) לנפח "
          "· סורק התבניות מהמצגת")

# ---------- stage 1 parameters (docs/research/intraday-strategies-2026-10-17.md, section "screener") ----------
PRICE = (5.0, 500.0)  # liquid names with real spreads; below $5 the costs ate the ZBA edge (docs/backtest)
ADV_SHARES, ADV_DOLLARS, ADV_SESSIONS = 1_000_000, 20_000_000.0, 20
ATR_PCT, ATR_ABS = 0.02, 0.50  # ATR14: >= 2% of the close (a 2:1 target fits a session) and >= $0.50 (Z&A 2024)
GAP_PCT = 0.02  # |price - previous close| / previous close: a catalyst that was priced over night
PRE_VOL_SHARE = 0.10  # pre-market volume >= 10% of the average day: in play without a gap
OPEN_RELVOL, STRONG_RELVOL = 1.0, 5.0  # 09:30-09:35 volume / its mean over RELVOL_SESSIONS: in play (Z&A), strong (Cameron)
RELVOL_SESSIONS = 14
WATCH = 20  # the watchlist (Zarattini & Aziz: the top 20 stocks in play)
SIP_DELAY = 15  # minutes: the free plan's SIP data ends this long before now
PRE_REFRESH_UNTIL = "09:35"  # the watchlist is rebuilt every scan up to here (the 09:35 build uses the IEX open)
RERANK_AT = "09:55"
TICK = deck.TICK
TIME_EXIT = "15:55"  # a full session's time exit; early closes use the calendar's close - 5 minutes
MAX_TICKETS = 5  # a scan sends at most this many, by score
EXCHANGES = {"NYSE", "NASDAQ", "ARCA"}
FUND_WORDS = ("ETF", "ETN", "FUND", "TRUST", "INDEX", "PORTFOLIO", "SHARES")
WINDOWS = {"morning": (deck.FIRST, deck.LAST)}
STEP = 5  # minutes between scans
# One ticket per symbol per scan (the bot's rule): the more specific setup first when one candle fits several.
PREFER = ("vwap_hammer", "vwap_engulf", "retest", "breakout", "flag", "hammer", "engulf", "inside", "nr7")
JOURNAL_KEYS = ("ok", "symbol", "setup", "note", "score", "entry_stop", "limit", "stop_loss", "target", "R",
                "r_floored", "shares", "risk", "time_exit", "atr", "gap", "pre_rel", "open_relvol", "at", "last", "vwap")


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
    return {"day": day, "watch": [], "sent": {}, "journal": [], "stats": None, "cards": {}}


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
    """{symbol: {"close", "high", "low", "atr", "adv", "advd"}} for symbols with the liquidity and range the screen
    asks for (high, low and close: the previous session's, levels of slide c4-levels)."""
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


def sip_end(now_hhmm):
    """The latest New York minute the free plan serves SIP data up to at `now_hhmm`."""
    return ticket.hhmm(ticket.mins(now_hhmm) - SIP_DELAY - 1)


def in_play(stats, day, now_hhmm=None):
    """Gap and pre-market volume -> [{symbol, gap, pre_vol, pre_rel, open}] sorted by pre_rel. Before PRE_REFRESH_UNTIL
    (a pre-market build): the SIP pre-market bars up to 15 minutes ago, the gap from their last price. Otherwise: the
    pre-market to 09:20 and the gap from the IEX open print."""
    syms = sorted(stats)
    early = now_hhmm is not None and now_hhmm < PRE_REFRESH_UNTIL
    pre = alpaca.volume_since(syms, day, "04:00", sip_end(now_hhmm) if early else "09:20")
    opens = {} if early else alpaca.live_bars(syms, day, "09:30", "09:30", "5Min")  # IEX: the open print, live
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


def stage1_stats(day, cal):
    before = [d for d in sorted(cal) if d < day]
    return daily_stats(universe(), before[-1], before[-max(ADV_SESSIONS, 30) - 1]), before


def watchlist(day, cal, now_hhmm=None, stats=None):
    """Stage 1 -> ([{symbol, gap, pre_rel, open, atr, prev_close, prev_high, prev_low, adv}] (the top WATCH), the
    sessions before `day`)."""
    if stats is None:
        stats, before = stage1_stats(day, cal)
    else:
        before = [d for d in sorted(cal) if d < day]
    rows = in_play(stats, day, now_hhmm)[:WATCH]
    for r in rows:
        st = stats[r["symbol"]]
        r.update(atr=st["atr"], prev_close=st["close"], prev_high=st["high"], prev_low=st["low"], adv=st["adv"])
    return rows, before


def rerank(watch, day, before):
    """From 09:55: re-rank the watchlist by the opening 5-minute bar's relative volume (stocks in play, Z&A 2024)."""
    rel = open_relvol([w["symbol"] for w in watch], day, before[-RELVOL_SESSIONS:])
    for w in watch:
        w["open_relvol"] = rel.get(w["symbol"])
    kept = [w for w in watch if w["open_relvol"] is None or w["open_relvol"] >= OPEN_RELVOL]
    return sorted(kept, key=lambda w: -(w.get("open_relvol") or 0))


# ---------- stage 2: the scan ----------
def time_exit(day, cal):
    """5 minutes before the session's close (15:55; 12:55 on a 13:00 early close)."""
    close = (cal or {}).get(day, (None, "16:00"))[1]
    return ticket.hhmm(ticket.mins(close) - 5) if close else TIME_EXIT


def context(watch, day, before):
    """The per-symbol inputs the rules read besides today's candles: yesterday's 5-minute candles (SIP, complete) and
    the daily bars for the Wyckoff tag -> {symbol: {"yday", "daily"}}."""
    syms = [w["symbol"] for w in watch]
    if not syms or not before:
        return {}
    yday = alpaca.minute_bars(syms, before[-1])
    span = deck.WY_RANGE_N + deck.WY_DECLINE_N
    daily = alpaca.daily(syms, before[-span - 1] if len(before) > span else before[0], before[-1])
    return {s: {"yday": deck.candles(yday.get(s, []), "16:00", start=deck.OPEN), "daily": daily.get(s, [])}
            for s in syms}


def spread_of(symbol, day, now_hhmm, live=True):
    """The full spread of the latest IEX quote (live, or as of `now_hhmm` for a replay); None when unusable."""
    q = alpaca.iex_quote(symbol, None if live else alpaca.utc(day, now_hhmm))
    if not q:
        return None
    bid, ask = q
    return max(round(ask - bid, 4), TICK) if bid > 0 and ask > 0 and ask >= bid else None


def scan(watch, day, now_hhmm, cal=None, extra=None, bars=None, live=True, blocked=None):
    """One pass over the watchlist -> [tickets], best first. `extra`: context(); `bars`: today's 1-minute IEX bars
    (fetched when None); `blocked`: a list that collects (symbol, setup, failed item, detail)."""
    syms = [w["symbol"] for w in watch]
    bars = bars if bars is not None else alpaca.live_bars(syms, day, "04:00", now_hhmm)
    extra = extra if extra is not None else context(watch, day, [d for d in sorted(cal or {}) if d < day])
    te = time_exit(day, cal)
    out = []
    for w in watch:
        sym = w["symbol"]
        cs = deck.candles(bars.get(sym, []), now_hhmm)
        if not cs:
            continue
        x = extra.get(sym, {})
        ctx = {"atr": w["atr"], "time_exit": te, "yday": x.get("yday", []), "daily": x.get("daily", []),
               "prev": {"h": w.get("prev_high"), "l": w.get("prev_low"), "c": w["prev_close"]}
               if w.get("prev_high") else None, "spread": None}
        res = deck.evaluate(sym, cs, ctx, now_hhmm)
        if any(b[1] == "cost" and b[2] == "אין ציטוט" for b in res["blocked"]):  # a quote only for a near-ticket
            ctx["spread"] = spread_of(sym, day, now_hhmm, live)
            res = deck.evaluate(sym, cs, ctx, now_hhmm)
        if blocked is not None:
            blocked += [(sym, *b) for b in res["blocked"]] + ([(sym, "*", "filter", res["filter"])] if res["filter"] else [])
        got = sorted(res["tickets"], key=lambda t: PREFER.index(t["setup"]))
        if got:
            t = got[0]
            rv = w.get("open_relvol") or w["pre_rel"] * 10  # relative volume is the measured edge; the gap is not
            t.update(ok=True, gap=w["gap"], pre_rel=w["pre_rel"], open_relvol=w.get("open_relvol"),
                     score=round(min(rv, STRONG_RELVOL) / STRONG_RELVOL, 3))
            out.append(t)
    return sorted(out, key=lambda t: -t["score"])


# ---------- messages ----------
# The ticket's label by setup: what the engine measured (T2: the VWAP pullback and the bull flag), or untested.
UNTESTED = "מחוץ למסלול שנבדק"
LABELS = {"vwap_hammer": "נבדק: תוחלת שלילית (T2)", "vwap_engulf": "נבדק: תוחלת שלילית (T2)",
          "flag": "נבדק: תוחלת שלילית (T2)"}


def label(setup):
    return f"תווית: {LABELS.get(setup, UNTESTED)} · כרטיס למחקר בלבד"


SETUP_NAMES = {"hammer": "Hammer על תמיכה", "engulf": "Engulfing שורי על תמיכה", "inside": "Inside bar",
               "nr7": "NR7", "breakout": "פריצה מעל רמה", "retest": "Retest של רמה שנפרצה",
               "vwap_hammer": "VWAP pullback עם Hammer", "vwap_engulf": "VWAP pullback עם Engulfing",
               "flag": "Bull flag",
               # the v1 names, for a journal written before the change
               "orb5": "פריצת טווח פתיחה 5 דק׳", "orb15": "פריצת טווח פתיחה 15 דק׳", "red_to_green": "מאדום לירוק"}
LEVEL_NAMES = {"vwap": "VWAP", "yday_high": "שיא אתמול", "yday_low": "שפל אתמול", "yday_close": "סגירת אתמול",
               "pre_high": "שיא לפני הפתיחה", "pre_low": "שפל לפני הפתיחה", "round": "מספר עגול",
               "touches": "נגיעות חוזרות", "previous_low": "שפל קודם"}
CHECK_NAMES = {"context": "הקשר", "level": "רמה", "shape": "צורה", "levels": "טריגר וסטופ", "size": "כמות",
               "cost": "עלות", "time": "זמן", "rule": "כלל קבוע"}
WYCKOFF_NAMES = {"accumulation": "צבירה", "spring": "Spring"}


def _pct(x):
    return code(f"{abs(x) * 100:.1f}%")


def il_time(day, hhmm):
    t = dt.datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=NY).astimezone(common.IL)
    return f"{t:%H:%M}"


def _ny_il(day, hhmm):
    return f"{code(hhmm)} ניו־יורק ({code(il_time(day, hhmm))} ישראל)"


def setup_line(setup):
    ch = deck.CHAPTER.get(setup)
    return SETUP_NAMES.get(setup, setup) + (f" (פרק {ch} במצגת)" if ch else "")


def ticket_text(t, day, E=None):
    """One ticket's card (`E` is accepted for the older callers and not used: size is fixed at $25 of risk)."""
    P = t["entry_stop"]
    rel = f" · נפח פתיחה יחסי {code(format(t['open_relvol'], '.1f'))}" if t.get("open_relvol") else ""
    gap = "פער " + ("מעלה " if t.get("gap", 0) >= 0 else "מטה ") + _pct(t.get("gap", 0))
    lv = t.get("level")
    why = f"למה: {SETUP_NAMES.get(t['setup'], t['setup'])}" + (
        f" על {LEVEL_NAMES.get(lv[1], lv[1])} {code(price(lv[0]))}" if lv else "") + f" · {gap}"
    stop_note = (", הורחב למינימום: 10% מה־ATR או 0.3% מהמחיר" if t.get("r_floored") and not t.get("r_capped")
                 else ", קוצר למקסימום: ATR אחד" if t.get("r_capped") else "")
    lines = [
        f"🎫 כרטיס ל-{code(t['symbol'])} · {setup_line(t['setup'])} · נר {_ny_il(day, t['at'])}",
        why + f" · נפח לפני פתיחה {_pct(t.get('pre_rel', 0))} מיום ממוצע{rel}",
        f"מחיר אחרון {code(price(t['last']))}" + (f" · VWAP {code(price(t['vwap']))}" if t.get("vwap") else ""),
        f"כניסה: קניית פריצה — טריגר {code(price(P))} · לימיט {code(price(t['limit']))}",
        f"סטופ לוס: {code(price(t['stop_loss']))} (סיכון {code(price(t['R']))} למניה{stop_note})",
        f"יעד: {code(price(t['target']))} (פי {code(format(deck.TARGET_R, 'g'))} מהסיכון)",
        f"כמות: {code(t['shares'])} מניות · סיכון {code(price(t['risk']))} (אחוז אחד מחשבון של {code(price(deck.CASH))})"
        f" · שווי עד {code(price(t['shares'] * t['limit']))}",
    ]
    if t.get("cost_r") is not None:
        lines.append(f"עלות משוערת: {code(format(t['cost_r'], '.2f'))} מיחידת הסיכון (עמלות IBKR Pro Tiered, עמלות "
                     f"צד ג׳ ומרווח מלא; התקרה {code(format(deck.COST_MAX_R, 'g'))})")
    if t.get("checks"):
        lines.append("רשימת הבדיקה: " + " · ".join(f"✓ {CHECK_NAMES[k]}" for k in deck.CHECKS))
    if t.get("wyckoff"):
        lines.append("הקשר וייקוף (לא טריגר): " + ", ".join(WYCKOFF_NAMES.get(w, w) for w in t["wyckoff"]))
    lines.append(f"תוקף: עד {_ny_il(day, t.get('valid_until', deck.LAST))} ואז ביטול · יציאה בזמן: "
                 f"{_ny_il(day, t['time_exit'])} אם לא נסגר קודם")
    lines.append("ב־IBKR: פקודת bracket אחת — BUY STP LMT (טריגר ולימיט שלמעלה), ילדים: SELL STP סטופ לוס ו־SELL LMT "
                 "יעד, OCA, תוקף DAY")
    if t.get("premarket"):
        lines += [f"לפני {code(deck.OPEN)}: סמנו Fill outside RTH בכל אחת משלוש הפקודות, והסטופ לוס חייב להיות SELL "
                  f"STP LMT — סטופ {code(price(t['stop_loss']))} · לימיט {code(price(t['stop_limit']))} (סטופ רגיל "
                  "לא פועל לפני הפתיחה). אחרי הפתיחה אפשר להחליף אותו ל־SELL STP רגיל.",
                  "⚠️ לפני הפתיחה ההגנה לא מובטחת: אם המחיר יקפוץ מתחת ללימיט של הסטופ, הוא לא יתמלא."]
    lines.append(label(t["setup"]))
    return "\n".join(lines)


def list_text(tickets, day, now_hhmm, E=None):
    head = f"🕯️ סורק התבניות מהמצגת · יום {common.weekday(day)} {code(il_date(day))} · סריקה ב־{code(il_time(day, now_hhmm))} ישראל"
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


def exit_text(state, day, te):
    lines = [f"⏰ יציאה בזמן: {_ny_il(day, te)} — לסגור כל פוזיציה פתוחה מכרטיסי היום ולבטל פקודות שלא מולאו:"]
    for t in state["journal"]:
        lines.append(f"מניה {code(t['symbol'])} · {SETUP_NAMES.get(t['setup'], t['setup'])} · טריגר "
                     f"{code(price(t['entry_stop']))} · {code(t['shares'])} מניות")
    return "\n".join(lines)


def sent_tickets(state):
    """Today's journal with each ticket's card fields (cost, checklist, Wyckoff, pre-market note) put back."""
    cards = state.get("cards") or {}
    return [{**t, **cards.get(f"{t['symbol']}:{t['setup']}", {})} for t in state["journal"]]


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
        lines.append(f"מניה {code(j['symbol'])} · {SETUP_NAMES.get(j['setup'], j['setup'])} · {word}{r}")
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


def record(state, t, now_hhmm):
    """Mark ticket `t` sent and journal it: the journal keeps its schema, the card's extra fields go to "cards"."""
    key = f"{t['symbol']}:{t['setup']}"
    state["sent"][key] = now_hhmm
    state["journal"].append({k: t.get(k) for k in JOURNAL_KEYS})
    state.setdefault("cards", {})[key] = {k: v for k, v in t.items() if k not in JOURNAL_KEYS}


def run_scan(state, day, before, now_hhmm, dry, cal=None, run=None):
    """One scan: refresh the pre-market watchlist, re-rank it at 09:55, send new tickets. `run` caches the stage-1
    stats and the per-symbol context for the length of a run."""
    run = run if run is not None else {}
    if now_hhmm <= PRE_REFRESH_UNTIL and "stats" in run:  # the 09:35 build takes the gap from the open print
        state["watch"], _ = watchlist(day, cal, now_hhmm, run["stats"])
        run.pop("extra", None)
    if now_hhmm >= RERANK_AT and state["watch"] and not state["watch"][0].get("open_relvol"):
        state["watch"] = rerank(state["watch"], day, before)
        send(watch_text(state["watch"], day), dry, symbols=[w["symbol"] for w in state["watch"]])
        run.pop("extra", None)
    have = {s for s in run.get("extra", {})}
    if {w["symbol"] for w in state["watch"]} - have:
        run["extra"] = {**run.get("extra", {}), **context([w for w in state["watch"] if w["symbol"] not in have], day, before)}
    tickets = [t for t in scan(state["watch"], day, now_hhmm, cal, run.get("extra", {}))
               if f"{t['symbol']}:{t['setup']}" not in state["sent"]][:MAX_TICKETS]
    for t in tickets:
        record(state, t, now_hhmm)
    if tickets:
        send(list_text(tickets, day, now_hhmm), dry, signal=True, symbols=[t["symbol"] for t in tickets])
    return tickets


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["morning", "once", "followup", "watch", "exit"], default="once")
    ap.add_argument("--symbol")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)
    now = _now()
    day = now.date().isoformat()
    cal = alpaca.calendar((now.date() - dt.timedelta(days=150)).isoformat(), day)
    if day not in cal:
        if a.mode != "exit":
            send("היום אין מסחר בניו־יורק, הסורק היומי לא רץ.", a.dry)
        return 0
    state = load(None)
    if not state or state.get("day") != day:
        state = fresh_state(day)
    before = [d for d in sorted(cal) if d < day]
    hhmm = now.strftime("%H:%M")

    if a.mode == "followup":
        send(followup(state), a.dry)
        if not a.dry:
            save(state)
        return 0

    if a.mode == "exit":  # dispatched twice (before an early close and a full one); only the matching one sends
        te = time_exit(day, cal)
        if abs(ticket.mins(hhmm) - ticket.mins(te)) > 10:
            return 0
        _sleep_until(dt.datetime.fromisoformat(f"{day}T{te}").replace(tzinfo=NY))
        if state["journal"]:
            send(exit_text(state, day, te), a.dry, signal=True, symbols=[t["symbol"] for t in state["journal"]])
        return 0

    if a.symbol:
        sym = a.symbol.upper()
        stats = daily_stats([sym], before[-1], before[-31])
        if sym not in stats:
            send(f"{code(sym)} מחוץ ליקום הסורק (מחיר 5–500$, נפח ממוצע מעל מיליון ו־20M$, ATR מעל 2%).", a.dry)
            return 0
        rows = [r for r in in_play(stats, day, hhmm) if r["symbol"] == sym] or [{"symbol": sym, "gap": 0.0, "pre_rel": 0.0}]
        st = stats[sym]
        rows[0].update(atr=st["atr"], prev_close=st["close"], prev_high=st["high"], prev_low=st["low"], adv=st["adv"])
        got = scan(rows, day, hhmm, cal)
        send(list_text(got, day, hhmm), a.dry, signal=bool(got), symbols=[t["symbol"] for t in got])
        return 0

    run = {}
    if not state["watch"]:
        run["stats"], before = stage1_stats(day, cal)
        state["watch"], _ = watchlist(day, cal, hhmm, run["stats"])
        send(watch_text(state["watch"], day), a.dry, symbols=[w["symbol"] for w in state["watch"]])
        if not a.dry:
            save(state)
    elif hhmm <= PRE_REFRESH_UNTIL and a.mode == "morning":
        run["stats"], _ = stage1_stats(day, cal)
    if a.mode == "watch":
        return 0
    if a.mode == "once":
        run_scan(state, day, before, hhmm, a.dry, cal, run)
        if not a.dry:
            save(state)
        return 0
    start, end = WINDOWS[a.mode]
    t = dt.datetime.fromisoformat(f"{day}T{start}").replace(tzinfo=NY)
    t = max(t, now.replace(second=0, microsecond=0) + dt.timedelta(minutes=(STEP - now.minute % STEP) % STEP))
    while t.strftime("%H:%M") <= end:
        _sleep_until(t + dt.timedelta(seconds=25))  # the candle's last minute bar is in
        try:
            run_scan(state, day, before, t.strftime("%H:%M"), a.dry, cal, run)
            if not a.dry:
                save(state)
        except Exception as e:  # one failed scan must not end the window
            common.trace(e)
            send(f"⚠️ סריקה ב־{code(t.strftime('%H:%M'))} ניו־יורק נכשלה: {code(str(e)[:120])}", a.dry)
        t += dt.timedelta(minutes=STEP)
    return 0


if __name__ == "__main__":
    sys.exit(main())
