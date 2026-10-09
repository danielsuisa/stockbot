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

from bot import alpaca, common, ticket_backtest as tb
from bot.common import code

VARIANT = "K10s"
STOP_ATR, SPREAD_D = tb.ZBA_GRID[VARIANT]  # 0.10 x ATR14, spread <= 0.25 x the stop distance
JOURNAL = "shadow_journal.json"
CATCH_UP = 5  # sessions processed in one run at most
LOOK_DAYS = 40  # calendar days of daily bars before a session: 15 bars for ATR14 and the 14-day average volume
BACKTEST_R = 0.277  # the realistic K10s mean on 2024-02 onward (docs/backtest/ticket-zba-2026-10-09.md)
VERDICT_TRADES, VERDICT_SESSIONS = 100, 60  # spec section 6
REASONS = {"trigger": "בלי פריצה", "doji": "נר פתיחה בלי כיוון", "spread": "מרווח רחב",
           "size below minimum": "פחות מ־10 מניות", "no atr": "בלי ATR", "no signal": "בלי נתונים",
           "no fill": "הפקודה לא התמלאה"}
DROPPED = {"Rule 201": "שורט חסום בכלל 201", "no trigger": "בלי עסקה ברמה בדקת ההפעלה"}


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
    prev = sessions[k - 1]
    look = (dt.date.fromisoformat(day) - dt.timedelta(LOOK_DAYS)).isoformat()
    syms = tb.asset_universe([a for a in alpaca.assets() if a.get("status") == "active"])
    daily = {}
    for i in range(0, len(syms), alpaca.CHUNK):
        daily.update(alpaca.daily(syms[i:i + alpaca.CHUNK], look, prev))
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
    real, dropped = tb._realism(rows, VARIANT, {day: hours[day]}, look, prev)
    dropped.pop("ok", None)
    keep = ("t", "rank", "side", "at", "level", "R", "shares", "spread", "fill", "kinds", "gross_r", "cost_r", "net_r")
    trim = lambda x: {c: round(x[c], 4) if isinstance(x[c], float) else x[c] for c in keep}  # noqa: E731
    return {"top": [t for t, _, _ in top], "reasons": {r: n for r, n in reasons.items() if n},
            "dropped": dict(dropped), "trades": [trim(x) for x in real],
            "optimistic": [trim(x) for x in rows if x["filled"]], "gaps": gaps}


# ---------- message ----------
def _px(x):
    return code(f"{x:.2f}")


def _r(x):
    return code(f"{x:+.2f}R")


def trade_line(x):
    """One shadow trade (Hebrew first): side, ticker, stop-order level and trigger time, fill, stop, shares, exit."""
    side = x["side"]
    stop = x["fill"] - side * x["R"]
    exit_ = x["fill"] + side * x["gross_r"] * x["R"]
    how = "סטופ" if x["kinds"] == "stop" else "סגירה"
    return (f"{'קנייה' if side == 1 else 'מכירה בחסר'}: {code(x['t'])} · פקודת עצירה ב־{_px(x['level'])}, הופעלה ב־"
            f"{code(x['at'])} · מילוי {_px(x['fill'])} · סטופ {_px(stop)} · {code(x['shares'])} מניות · יציאה "
            f"{_px(exit_)} ({how}) · {_r(x['net_r'])} נטו")


def tally(j):
    """Every realistic shadow trade so far -> tb.stats + total R, sessions, since."""
    xs = [{"day": d, "net_r": x["net_r"], "fill": x["fill"], "kinds": x["kinds"]}
          for d, v in sorted(j["days"].items()) for x in v["trades"]]
    st = tb.stats(xs)
    return {**st, "total": sum(x["net_r"] for x in xs), "sessions": len(j["days"]), "since": j.get("since")}


def message(day, res, j):
    """The Hebrew shadow message for one session (the journal already holds it)."""
    lines = [f"👻 <b>מעקב צל {code(VARIANT)} · {code(day)}</b>",
             "אלה העסקאות שהשיטה הייתה עושה במסחר הרגיל. לא נשלחה שום פקודה ולא הושקע כסף."]
    lines += [trade_line(x) for x in res["trades"]] or ["🤷 היום לא הייתה עסקה."]
    skipped = [f"{REASONS.get(r, r)} {code(n)}" for r, n in sorted(res["reasons"].items())]
    skipped += [f"{DROPPED.get(s, s)} {code(n)}" for s, n in sorted(res["dropped"].items())]
    if res.get("gaps"):
        skipped.append(f"בלי נתוני דקה {code(res['gaps'])}")
    lines.append(f"ℹ️ מתוך {code(len(res['top']))} המניות עם נפח הפתיחה החריג ביותר, לא נסחרו: "
                 + (" · ".join(skipped) if skipped else "אף אחת") + ".")
    t = tally(j)
    if t["n"]:
        small = f" (מדגם קטן, פחות מ־{code(VERDICT_TRADES)})" if t["n"] < VERDICT_TRADES else ""
        lines.append(f"📒 מצטבר מאז {code(t['since'])}: {code(t['n'])} עסקאות ב־{code(t['sessions'])} ימי מסחר{small} · "
                     f"ממוצע {_r(t['mean'])} · סה״כ {_r(t['total'])} · הצלחה {code(format(t['win'], '.0%'))}"
                     + (f" · סטטיסטי t {code(format(t['t'], '.2f'))}" if t["t"] is not None else ""))
    else:
        lines.append(f"📒 מצטבר מאז {code(t['since'])}: עוד אין עסקאות ({code(t['sessions'])} ימי מסחר).")
    lines.append(f"📊 בבדיקה ההיסטורית: {_r(BACKTEST_R)} לעסקה מפברואר 2024, אבל 2024 עצמה הייתה שלילית. ההכרעה"
                 f" תהיה אחרי {code(VERDICT_TRADES)} עסקאות ו־{code(VERDICT_SESSIONS)} ימי מסחר לפחות.")
    lines.append("⚠️ השיטה לא עברה בדיקה שנקבעה מראש, ולכן זה מעקב בלבד ולא כרטיס פקודה.")
    return "\n".join(lines)


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
    start = (dt.date.fromisoformat(today) - dt.timedelta(LOOK_DAYS + 30)).isoformat()
    hours = alpaca.calendar(start, today)
    sessions = sorted(hours)
    j = load()
    days = pending(sessions, j, today)
    if not days:
        print(f"shadow: no new session before {today}")
        if manual and not a.dry:
            common.send("👻 אין יום מסחר חדש למעקב הצל. היום האחרון שעובד: "
                        f"{code(max(j['days']) if j['days'] else '—')}.")
        return 0
    for day in days:
        res = run_day(day, hours, sessions)
        j["since"] = j.get("since") or day
        j["days"][day] = res
        text = message(day, res, j)
        if a.dry:
            print(text, end="\n\n")
            continue
        common.send(text)
        save(j)  # right after it went out: a later failure in this run never resends the day
        common.log("shadow", day=day, top=len(res["top"]), trades=len(res["trades"]), dropped=res["dropped"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
