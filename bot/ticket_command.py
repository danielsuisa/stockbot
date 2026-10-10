"""`/ticket SYMBOL` (docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md revision 7, phase B): the
levels engine's ticket for any symbol, live, without signals (`breakout`, H = 21) and labelled "ללא יתרון מוכח".

The data path: the exchange calendar picks D (the latest session closed at least 15 minutes ago: Alpaca's free-plan
lag); Alpaca SIP daily bars through D; the SIP quote 5 minutes before D's close (the guard). No outcome of any ticket
is computed here: the simulation in `levels` is never called from this module."""
import datetime as dt
import sys

from bot import alpaca, common, levels
from bot.common import code, il_date, price

LIVE_LAG_MIN = 15  # minutes: the free Alpaca plan gives SIP data only after this
HISTORY_DAYS, FUTURE_DAYS = 400, 60  # calendar days read before D (252 sessions for the universe mark) and after it
SOURCE = ("⚠️ כרטיס מחושב בלבד, לא ייעוץ השקעות: הבוט לא שולח פקודות.\n"
          f"מקור: נרות יומיים וציטוט SIP של {code('Alpaca')} (באיחור של {code(LIVE_LAG_MIN)} דקות) · "
          "מנוע הרמות של האלגוריתם")
LABEL = "ללא יתרון מוכח"
REASONS = {
    "fewer than 15 bars": "פחות מ-15 ימי מסחר בנתונים",
    "shares below 2": "בסיכון שנקבע יוצאות פחות מ-2 מניות",
    "size above 1% of dollar volume": "הפוזיציה גדולה מ-1% ממחזור הדולרים היומי הממוצע",
    "no usable quote": "אין ציטוט שמיש 5 דקות לפני סגירת יום האות",
    "cost above 10% of risk": "העלות המשוערת גבוהה מ-10% מהסיכון",
}
UNIVERSE = {
    "price below $5": "מחיר מתחת ל-5$",
    "dollar volume below $10M": "מחזור דולרי ממוצע מתחת ל-10M$",
    "fewer than 252 sessions": "פחות מ-252 ימי מסחר",
}


def _at(day, hhmm):
    return dt.datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=alpaca.NY)


def signal_day(now, cal):
    """The latest session in `cal` ({date: (open, close)}) whose close was at least 15 minutes before `now` (NY)."""
    for d in sorted(cal, reverse=True):
        if _at(d, cal[d][1]) + dt.timedelta(minutes=LIVE_LAG_MIN) <= now:
            return d
    return None


def sessions_after(day, cal, n):
    return [d for d in sorted(cal) if d > day][:n]


def build(symbol, now=None):
    """The guarded ticket for `symbol` on the latest closed session, with its valid sessions, the time-exit date of a
    fill on the first of them and the partial universe mark -> dict ("ok" False with "reason" on a refusal)."""
    now = now or dt.datetime.now(alpaca.NY)
    today = now.date()
    cal = alpaca.calendar((today - dt.timedelta(HISTORY_DAYS)).isoformat(),
                          (today + dt.timedelta(FUTURE_DAYS)).isoformat())
    day = signal_day(now, cal)
    start = (dt.date.fromisoformat(day) - dt.timedelta(HISTORY_DAYS)).isoformat()
    bars = alpaca.daily([symbol], start, day).get(symbol) or []
    t = levels.plan(symbol, bars, day, risk=common.env("TICKET_RISK_USD", 100.0))
    if t["ok"]:
        t = levels.guard(t, alpaca.quote_full(symbol, levels.guard_time(day, cal)))
    t.update(symbol=symbol, day=day)
    if not t["ok"]:
        return t
    valid = sessions_after(day, cal, levels.BREAKOUT_DAYS)
    later = sessions_after(valid[0], cal, t["horizon"]) if valid else []
    t.update(valid_sessions=valid, time_exit_if_first=later[-1] if len(later) == t["horizon"] else None,
             universe=levels.universe_mark(bars, day),
             first_session_started=bool(valid) and valid[0] == today.isoformat() and now >= _at(valid[0], cal[valid[0]][0]))
    return t


def _p(x):
    return code(price(x))


def message(t):
    """Section 8's Hebrew message (every line Hebrew first, numbers in <code>)."""
    sym = code(t["symbol"])
    if not t["ok"]:
        return f"אין כרטיס ל-{sym} ליום {il_date(t['day'])}: {REASONS[t['reason']]}"
    a, b = t["legs"]
    valid = ", ".join(il_date(d) for d in t["valid_sessions"])
    first = t["valid_sessions"][0] if t["valid_sessions"] else None
    exit_line = f"יציאה בזמן: בסגירה של יום המסחר ה-{code(t['horizon'])} אחרי הכניסה"
    if first and t.get("time_exit_if_first"):
        exit_line += f" (כניסה ב-{il_date(first)} ← יציאה ב-{il_date(t['time_exit_if_first'])})"
    lines = [
        f"🎫 כרטיס ל-{sym} · יום האות {il_date(t['day'])}",
        "מקור: בקשה ידנית · אותות: אין אות תומך",
        f"כניסה: קניית פריצה — סטופ {_p(t['entry_stop'])} · לימיט {_p(t['limit'])} · בתוקף ב-{valid}",
        f"סטופ לוס (שתי המנות): {_p(t['stop_loss'])} · יעד מנה א': {_p(t['target'])} · "
        f"טריילינג מנה ב': {_p(t['trail'])}",
        f"המחירים מחושבים ממחיר הכניסה המתוכנן ויזוזו עם הכניסה בפועל (כניסה פחות {_p(t['trail'])}, "
        f"כניסה ועוד {_p(levels.px(levels.TARGET_R * t['R']))})",
        exit_line,
        f"מניות: מנה א' {code(a)} · מנה ב' {code(b)} · שווי פוזיציה {_p(t['shares'] * t['planned_entry'])} · "
        f"סיכון {_p(t['shares'] * t['R'])}",
        f"עלות משוערת: {code(format(t['cost_r'] * 100, '.1f') + '%')} "
        "מהסיכון (מרווח 5 דקות לפני סגירת יום האות, ועמלות)",
        "ב-IBKR: שתי פקודות bracket בחצי כמות — א': כניסה, יעד לימיט וסטופ · ב': כניסה ו-TRAIL — עד היציאה בזמן",
    ]
    if t.get("first_session_started"):
        lines.append("שים לב: יום הכניסה הראשון כבר במסחר")
    if t.get("universe"):
        lines.append("מחוץ ליקום שנבדק: " + ", ".join(UNIVERSE[u] for u in t["universe"])
                     + " (תנאי דיווחי בעלי העניין ייבדק בשלב הבא)")
    lines.append(f"תווית: {LABEL}")
    return "\n".join(lines)


def main(argv=None):
    """A dry run: the message for each symbol, through common.send (printed when TG_TOKEN is not set)."""
    for s in (argv if argv is not None else sys.argv[1:]):
        common.send(message(build(s.upper())), source=SOURCE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
