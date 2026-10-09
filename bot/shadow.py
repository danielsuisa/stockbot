"""Forward shadow tracker of ZBA K10s (docs/superpowers/specs/2026-10-09-shadow-tracker-design.md): every trading day,
the trades K10s would have made, computed with the backtest's own code on SIP data, posted to Telegram with a running
tally. No order is sent and no money is used; the method has not passed a pre-registered test.

    python -m bot.shadow           # process the completed sessions not yet in data/shadow_journal.json
    python -m bot.shadow --dry     # print the messages, write nothing"""
import argparse
import collections
import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from bot import alpaca, common, ticket, ticket_backtest as tb
from bot.common import code

VARIANT = "K10s"
STOP_ATR, SPREAD_D = tb.ZBA_GRID[VARIANT]  # 0.10 x ATR14, spread <= 0.25 x the stop distance
JOURNAL = "shadow_journal.json"
CATCH_UP = 5  # sessions processed in one run at most
LOOK_DAYS = 60  # calendar days of daily bars before a session: 15 bars for ATR14 and the 14-day average volume
VERDICT_TRADES, VERDICT_SESSIONS = 100, 60  # spec section 6
SHOW_T = 30  # trades before the tally shows the average and t
IL = ZoneInfo("Asia/Jerusalem")
TITLE = "👻 <b>מעקב צל: פריצת הפתיחה</b>"
SOURCE = ("⚠️ מעקב בלבד, לא כרטיס פקודה ולא ייעוץ השקעות.\n"
          f"מקור: נתוני {code('Alpaca')} (מושהים ב־{code(15)} דקות)")  # replaces common.send's SEC footer
DAYS = ["ב׳", "ג׳", "ד׳", "ה׳", "ו׳", "ש׳", "א׳"]  # date.weekday(): Monday = 0
# why a stock in the opening top did not trade: "{n}" -> the count; "ב־" phrases take "וב־" as the last item
SKIPS = {"spread": "{n} נפסלו בגלל מרווח רחב", "trigger": "ב־{n} לא הייתה פריצה",
         "doji": "ב־{n} נר הפתיחה היה ניטרלי", "no atr": "ב־{n} חסרה היסטוריה",
         "size below minimum": "ב־{n} הפוזיציה יצאה קטנה מדי", "no signal": "ב־{n} חסרו נתונים",
         "no fill": "ב־{n} הפקודה לא התמלאה",
         "Rule 201": "ב־{n} הייתה חסימת מכירה בחסר אחרי ירידה של " + code("10%"),
         "no trigger": "ב־{n} לא הייתה עסקה ברמת הכניסה", "gaps": "ב־{n} חסרו נתוני דקה"}
EXITS = {"stop": "נעצרה בסטופ", "eod": "נסגרה בסוף היום", "target": "נסגרה ביעד"}


# ---------- files ----------
def path():
    return Path(common.env("SHADOW_DIR", str(common.DATA))) / JOURNAL


def load():
    p = path()
    return json.loads(p.read_text("utf-8")) if p.exists() else {"v": 1, "since": None, "days": {}}


def save(j):
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(j, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, p)


# ---------- which sessions ----------
def pending(sessions, j, today):
    """Completed sessions (before `today`, ISO) still to process: after the last processed one, at most CATCH_UP; on
    the first run only the latest completed session (the ones before it belong to the already-seen backtest)."""
    done = [s for s in sessions if s < today]
    if not done:
        return []
    if not j["days"]:
        return done[-1:]
    last = max(j["days"])
    return [s for s in done if s > last][:CATCH_UP]


# ---------- one session ----------
def run_day(day, hours, sessions):
    """K10s on session `day` with the backtest's functions -> {"top", "reasons", "dropped", "trades" (realistic rows),
    "optimistic" (bar-model rows), "gaps"}."""
    k = sessions.index(day)
    if k < tb.Z_DAYS + 1:  # never a short window or sessions[-1]: the caller loads enough calendar
        raise ValueError(f"shadow: {day} has {k} sessions of calendar before it, {tb.Z_DAYS + 1} needed")
    prev = sessions[k - 1]
    look = (dt.date.fromisoformat(day) - dt.timedelta(LOOK_DAYS)).isoformat()
    syms = tb.asset_universe(alpaca.assets())  # active and inactive, like the backtest: D's last-day names too
    daily = {}
    for i in range(0, len(syms), alpaca.CHUNK):
        daily.update(alpaca.daily(syms[i:i + alpaca.CHUNK], look, day))  # D is complete; only bars before D are used
    pre = tb.prefilter(day, daily, prev)
    window = sessions[max(0, k - tb.Z_DAYS):k]
    names = sorted(pre)
    prior = [alpaca.opening_bars(names, s) for s in window] if names else []
    today = alpaca.opening_bars(names, day) if names else {}
    top = tb.stocks_in_play({t: today.get(t) for t in names}, prior)
    mb = alpaca.minute_bars([t for t, _, _ in top], day) if top else {}
    rows, reasons, gaps = [], collections.Counter(), 0
    for rank, (t, rv, _) in enumerate(top, 1):
        bars = mb.get(t) or []
        if not bars:
            gaps += 1
            continue
        ctx = {"day": day, "open": hours[day][0], "close": hours[day][1], "atr": pre[t]["atr"], "open_rel_vol": rv,
               "from": hours[day][0], "stop_atr": STOP_ATR, "spread_d": SPREAD_D}
        reason, trade = tb._play("ZBA", "REGULAR", bars, ctx, tb._spread(t, day), day, t, "C")
        if trade:
            rows.append({**trade, "rank": rank, "rel_vol": rv, "variant": VARIANT})
        else:
            reasons[reason] += 1
    reasons["no fill"] += sum(1 for x in rows if not x["filled"])
    real, dropped = tb._realism(rows, VARIANT, {day: hours[day]}, look, day)
    dropped.pop("ok", None)
    keep = ("t", "rank", "side", "at", "level", "R", "shares", "spread", "fill", "kinds", "gross_r", "cost_r", "net_r")
    trim = lambda x: {c: round(x[c], 4) if isinstance(x[c], float) else x[c] for c in keep}  # noqa: E731
    return {"top": [t for t, _, _ in top], "reasons": {r: n for r, n in reasons.items() if n},
            "dropped": dict(dropped), "trades": [trim(x) for x in real],
            "optimistic": [trim(x) for x in rows if x["filled"]], "gaps": gaps}


# ---------- message ----------
def _px(x):
    return code(f"{x:.2f}$")


def il_date(day, year=True):
    """ISO date -> Israeli "8.10.2026" (or "8.10")."""
    d = dt.date.fromisoformat(day)
    return f"{d.day}.{d.month}" + (f".{d.year}" if year else "")


def weekday(day):
    return DAYS[dt.date.fromisoformat(day).weekday()]


def il_time(day, hhmm):
    """New York "HH:MM" on `day` -> Israel "HH:MM" (both daylight-saving calendars)."""
    ny = dt.datetime.combine(dt.date.fromisoformat(day), dt.time.fromisoformat(hhmm), tzinfo=alpaca.NY)
    return ny.astimezone(IL).strftime("%H:%M")


def money(r, word=""):
    """R -> dollars at the tracker's risk per trade, the sign as a word: "רווח של X$" / "הפסד של X$"."""
    usd = round(abs(r) * ticket.RISK)
    if not usd:
        return "ללא רווח והפסד"
    return f"{'רווח' if r > 0 else 'הפסד'}{word} של {code(f'{usd}$')}"


def trade_block(day, x):
    """One shadow trade: side and ticker, entry price and Israel time, stop and shares, how it ended in dollars."""
    side = x["side"]
    stop = x["fill"] - side * x["R"]
    exit_ = x["fill"] + side * x["gross_r"] * x["R"]
    head = f"🟢 <b>קנייה: {code(x['t'])}</b>" if side == 1 else f"🔴 <b>מכירה בחסר: {code(x['t'])}</b>"
    return "\n".join([head, f"{'כניסה בפריצה' if side == 1 else 'כניסה בשבירה'}: {_px(x['fill'])} בשעה "
                            f"{code(il_time(day, x['at']))}",
                      f"סטופ: {_px(stop)} · כמות: {code(x['shares'])} מניות",
                      f"תוצאה: {EXITS.get(x['kinds'], 'נסגרה')} ב־{_px(exit_)}, {money(x['net_r'])}"])


def skips_line(res):
    """📋 One sentence: how many stocks were in the opening top and why the others did not trade."""
    if not res["top"]:
        return "📋 אף מניה לא נכנסה היום לרשימת המניות הפעילות בפתיחה."
    counts = collections.Counter({**res["reasons"], **res["dropped"]})
    counts["gaps"] = res.get("gaps", 0)
    items = [SKIPS[r].format(n=code(n)) if r in SKIPS else f"ב־{code(n)}: {code(r)}"
             for r, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if n > 0]
    if not items:
        return f"📋 מתוך {code(len(res['top']))} המניות הפעילות בפתיחה, אף אחת לא נפסלה."
    if len(items) > 1:
        items[-1] = "ו" + items[-1] if items[-1].startswith("ב־") else "ו־" + items[-1]
    return f"📋 מתוך {code(len(res['top']))} המניות הפעילות בפתיחה, " + ", ".join(items) + "."


def tally(j):
    """Every realistic shadow trade so far -> tb.stats + total R, sessions, since."""
    xs = [{"day": d, "net_r": x["net_r"], "fill": x["fill"], "kinds": x["kinds"]}
          for d, v in sorted(j["days"].items()) for x in v["trades"]]
    st = tb.stats(xs)
    return {**st, "total": sum(x["net_r"] for x in xs), "wins": sum(x["net_r"] > 0 for x in xs),
            "sessions": len(j["days"]), "since": j.get("since")}


def tally_block(j):
    """📒 The running record in dollars; the average and t only from SHOW_T trades on."""
    t = tally(j)
    head = f"📒 <b>מצטבר מאז {code(il_date(t['since'], year=False))}:</b> "
    if not t["n"]:
        head += f"עוד אין עסקאות ({code(t['sessions'])} ימי מסחר)."
    else:
        head += f"{code(t['n'])} עסקאות · {money(t['total'], ' כולל')} · {code(t['wins'])} מוצלחות"
        if t["n"] >= SHOW_T:
            head += f" · ממוצע לעסקה: {money(t['mean'])}"
            head += f" · מובהקות t={code(format(t['t'], '.2f'))}" if t["t"] is not None else ""
    verdict = f"ההכרעה תהיה אחרי {code(VERDICT_TRADES)} עסקאות ו־{code(VERDICT_SESSIONS)} ימי מסחר."
    if t["n"] < SHOW_T:
        verdict = "מדגם קטן מדי למסקנה. " + verdict
    elif t["n"] >= VERDICT_TRADES and t["sessions"] >= VERDICT_SESSIONS:
        verdict = "יש מספיק עסקאות וימי מסחר להכרעה שנקבעה מראש."
    return head + "\n" + verdict


def message(day, res, j):
    """The Hebrew shadow message for one session (the journal already holds it); common.send adds SOURCE."""
    blocks = [f"{TITLE}\nיום {weekday(day)} {code(il_date(day))} · מעקב בלבד, לא נשלחה פקודה"]
    blocks += [trade_block(day, x) for x in res["trades"]] or ["אף עסקה לא נפתחה היום."]
    blocks += [skips_line(res), tally_block(j)]
    return "\n\n".join(blocks)


def no_new_day(j):
    last = max(j["days"]) if j["days"] else None
    return (f"{TITLE}\nאין יום מסחר חדש למעקב הצל. "
            + (f"היום האחרון שנבדק: יום {weekday(last)} {code(il_date(last))}." if last else "עוד לא נבדק אף יום."))


# ---------- run ----------
def today_ny():
    return dt.datetime.now(alpaca.NY).date().isoformat()


def main(argv=None):
    ap = argparse.ArgumentParser(description="ZBA K10s forward shadow tracker -> Telegram and data/shadow_journal.json")
    ap.add_argument("--dry", action="store_true", help="print the messages, write nothing")
    ap.add_argument("--manual", action="store_true", help="say so when there is no new session (also SHADOW_MODE)")
    a = ap.parse_args(argv)
    manual = a.manual or common.env("SHADOW_MODE") == "manual"
    today = today_ny()
    j = load()
    first = min(today, max(j["days"], default=today))  # after a long stop the catch-up days still have 15 sessions before them
    start = (dt.date.fromisoformat(first) - dt.timedelta(LOOK_DAYS + 30)).isoformat()
    hours = alpaca.calendar(start, today)
    sessions = sorted(hours)
    days = pending(sessions, j, today)
    if not days:
        print(f"shadow: no new session before {today}")
        if manual and not a.dry:
            common.send(no_new_day(j), source=SOURCE)
        return 0
    for day in days:
        res = run_day(day, hours, sessions)
        j["since"] = j.get("since") or day
        j["days"][day] = res
        text = message(day, res, j)
        if a.dry:
            print(text, end="\n\n")
            continue
        common.send(text, source=SOURCE)
        save(j)  # right after it went out: a later failure in this run never resends the day
        common.log("shadow", day=day, top=len(res["top"]), trades=len(res["trades"]), dropped=res["dropped"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
