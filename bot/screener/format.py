"""Screen results -> Hebrew Telegram text (the bot's message rules: Hebrew first on every line, numbers in <code>,
no sign glued to a number, Israeli dates, Israel time - tests/msgrules.py). Approximated values carry "≈" (G9)."""
import hashlib

from bot import common
from bot.common import code
from bot.screener import catalog

SOURCE = ("⚠️ מידע לצורכי מחקר בלבד ואינו ייעוץ השקעות.\n"
          "מקור: Alpaca (מחירים, באיחור של כ־15 דקות), SEC, FINRA, Nasdaq Trader, OCC. ≈ = ערך מקורב.")
SESSION = {"PRE": "לפני הפתיחה", "RTH": "במסחר", "POST": "אחרי הסגירה", "CLOSED": "השוק סגור"}


def _pct(v, digits=1):
    return common.arrow(v, digits)


def _abs_pct(v, digits=1):
    return code(f"{abs(v) * 100:.{digits}f}%")


def _x(v, digits=1):
    return code(f"{v:.{digits}f}") if v >= 0 else f"{code(f'{abs(v):.{digits}f}')} שלילי"


def _shares(v):
    return common.count(v)


def _usd(v):
    return code(common.money(v))


def _bool(v):
    return "כן" if v else "לא"


# metric -> (Hebrew label, formatter); every other metric shows its id
LABELS = {
    "price": ("מחיר", lambda v: code(common.price(v))), "change": ("שינוי", _pct), "change_open": ("מהפתיחה", _pct),
    "gap": ("פער פתיחה", _pct), "volume": ("מחזור", _shares), "avg_volume": ("מחזור ממוצע", _shares),
    "rel_volume": ("מחזור יחסי", lambda v: f"פי {code(f'{v:.1f}')}"), "trades": ("עסקאות", _shares),
    "market_cap": ("שווי שוק", _usd), "shares_outstanding": ("מניות", _shares), "float": ("מניות צפות", _shares),
    "float_pct": ("צפות מהמניות", _abs_pct), "short_float": ("שורט מהצפות", _abs_pct),
    "short_ratio": ("ימים לכיסוי", _x), "insider_own": ("בעלי עניין", _abs_pct), "insider_trans": ("שינוי בעלי עניין", _pct),
    "pe": ("מכפיל רווח", _x), "ps": ("מכפיל מכירות", _x), "pb": ("מכפיל הון", _x), "pc": ("מחיר למזומן", _x),
    "pfcf": ("מחיר לתזרים חופשי", _x), "ev_ebitda": ("EV ל־EBITDA", _x), "ev_sales": ("EV למכירות", _x),
    "dividend_yield": ("תשואת דיבידנד", _abs_pct), "payout": ("יחס חלוקה", _abs_pct), "roa": ("תשואה על נכסים", _pct),
    "roe": ("תשואה על ההון", _pct), "roi": ("תשואה על ההשקעה", _pct), "current_ratio": ("יחס שוטף", _x),
    "quick_ratio": ("יחס מהיר", _x), "lt_debt_eq": ("חוב ארוך להון", _x), "debt_eq": ("חוב להון", _x),
    "gross_margin": ("רווח גולמי", _pct), "oper_margin": ("רווח תפעולי", _pct), "net_margin": ("רווח נקי", _pct),
    "eps_qoq": ("רווח למניה, רבעון", _pct), "eps_yoy_ttm": ("רווח למניה, שנה", _pct), "sales_qoq": ("מכירות, רבעון", _pct),
    "sales_yoy_ttm": ("מכירות, שנה", _pct), "eps_5y": ("רווח למניה, 5 שנים", _pct), "sales_5y": ("מכירות, 5 שנים", _pct),
    "rsi14": ("RSI", lambda v: code(f"{v:.1f}")), "atr14": ("ATR", lambda v: code(common.price(v))),
    "beta": ("בטא", _x), "volatility_w": ("תנודתיות שבוע", _abs_pct), "volatility_m": ("תנודתיות חודש", _abs_pct),
    "perf_w": ("שבוע", _pct), "perf_m": ("חודש", _pct), "perf_q": ("רבעון", _pct), "perf_h": ("חצי שנה", _pct),
    "perf_y": ("שנה", _pct), "perf_ytd": ("מתחילת השנה", _pct), "perf_3y": ("3 שנים", _pct), "perf_5y": ("5 שנים", _pct),
    "perf_10y": ("10 שנים", _pct), "price_vs_sma20": ("מול ממוצע 20", _pct), "price_vs_sma50": ("מול ממוצע 50", _pct),
    "price_vs_sma200": ("מול ממוצע 200", _pct), "from_high_52w": ("מהשיא השנתי", _pct),
    "from_low_52w": ("מהשפל השנתי", _pct), "from_high_20d": ("משיא 20 יום", _pct), "from_low_20d": ("משפל 20 יום", _pct),
    "from_high_50d": ("משיא 50 יום", _pct), "from_low_50d": ("משפל 50 יום", _pct), "day_range": ("טווח יומי", _abs_pct),
    "optionable": ("אופציות", _bool), "shortable": ("שורט אפשרי", _bool), "ah_close": ("אחרי הסגירה", lambda v: code(common.price(v))),
    "ah_change": ("שינוי אחרי הסגירה", _pct), "news_count": ("ידיעות", lambda v: code(int(v))),
}
DEFAULT_EXTRA = ("volume", "rel_volume", "market_cap")
SKIP_EXTRA = {"price", "change", "is_etf", "is_shell", "exchange", "sector", "industry", "country", "earnings_date",
              "ipo_date", "candlestick", "ssr"} | {m for m in catalog.UNITS if m.startswith(("cross_", "new_"))}
REASON = {"unknown code": "קוד לא מוכר", "unknown sort key": "מיון לא מוכר", "unknown parameter": "פרמטר לא מוכר",
          "only one signal per screen": "רק סיגנל אחד בסריקה"}


def extra_metrics(query):
    """The (up to) 3 metrics a row shows besides price and change: those the filters, sort and signal use."""
    out = []
    for p in query.filters:
        out += [m for m in p.metrics() if m in LABELS and m not in SKIP_EXTRA]
    if query.sort and query.sort[0] in LABELS and query.sort[0] not in SKIP_EXTRA:
        out.append(query.sort[0])
    if query.signal:
        from bot.screener import signals
        rule = signals.RULES.get(query.signal["code"])
        if rule and rule[0] in LABELS and rule[0] not in SKIP_EXTRA:
            out.insert(0, rule[0])
    out = list(dict.fromkeys(out))
    for m in DEFAULT_EXTRA:
        if len(out) >= 3:
            break
        if m not in out:
            out.append(m)
    return out[:3]


def cell(ctx, metric):
    mv = ctx.value(metric)
    if mv.value is None:
        return f"{LABELS.get(metric, (metric,))[0]} חסר"
    label, fmt = LABELS.get(metric, (metric, lambda v: code(v)))
    approx = "≈" if mv.approx or (metric in ("float", "short_float", "volume_pct_float")
                                   and ctx.get("float_method") == "outstanding_proxy") else ""
    return f"{label} {approx}{fmt(mv.value)}"


def row_text(k, r, extras):
    c = r["ctx"]
    head = f"מקום {code(k)}: {code(r['t'])} · {cell(c, 'price').split(' ', 1)[1]} {cell(c, 'change').split(' ', 1)[1]}"
    return head + "\nנתונים: " + " · ".join(cell(c, m) for m in extras)


def when_line(res, now):
    """'נכון ל־16:45 ניו־יורק · 23:45 בישראל (באיחור של כ־15 דקות) · אחרי הסגירה'."""
    il = now.astimezone(common.IL)
    return (f"נכון ל־{code(now.strftime('%H:%M'))} ניו־יורק · {code(il.strftime('%H:%M'))} בישראל, יום "
            f"{common.weekday(il.date())} {code(common.il_date(il.date(), year=False))}"
            f" (באיחור של כ־15 דקות) · {SESSION.get(res.session, res.session)}")


def ignored_lines(ignored):
    if not ignored:
        return []
    parts = []
    for c, why in ignored:
        if why.startswith("unavailable: "):
            he = "אין מקור חינמי"
        elif why.startswith("replaced by "):
            he = f"הוחלף ב־{code(why.split(' ', 2)[2])}"
        else:
            he = REASON.get(why, why)
        parts.append(f"{code(c)} ({he})")
    return ["לא נבדקו: " + ", ".join(parts)]


def notes_lines(res):
    out = []
    if res.truncated:
        out.append(f"⚠️ נבדקו היום רק {code(res.stage2)} מניות מתוך {code(res.stage2 + res.truncated)} (מגבלת עומס).")
    for n in res.notes:
        if n.startswith("stale"):
            out.append(f"⚠️ נתונים לא עדכניים: {code(n.split(':', 1)[1].strip())}")
        elif n.startswith("no live data"):
            out.append("⚠️ לא התקבלו נתוני היום: לפי הסגירה האחרונה.")
    return out


def screen_text(title, query, res, now, max_rows=15):
    """The full result message."""
    extras = extra_metrics(query)
    lines = [f"🔎 <b>{common.esc(title)}</b>", f"קודים: {code(query.key() or '-')}", when_line(res, now), ""]
    if res.rows:
        shown = res.rows[:max_rows]
        lines.append(f"נמצאו {code(res.total)} מניות" + (f", מוצגות {code(len(shown))}" if res.total > len(shown) else "") + ":")
        lines += [row_text(k, r, extras) for k, r in enumerate(shown, 1)]
    else:
        lines.append("🤷 אף מניה לא עומדת כרגע בכל התנאים.")
    tail = ignored_lines(res.ignored) + notes_lines(res)
    if any(c.value(m).approx for r in res.rows[:max_rows] for c in [r["ctx"]] for m in extras):
        tail.append("≈ ערך מקורב: ראו את ההגדרות במסמך המדדים.")
    return "\n".join(lines + ([""] + tail if tail else []))


def change_text(title, res, old, now, max_rows=15):
    """Scheduled preset alert: who entered (with their lines) and who left since the last alert."""
    was, now_set = set(old or []), [r["t"] for r in res.rows]
    lines = [f"🔄 <b>{common.esc(title)}: שינוי בתוצאות</b>", when_line(res, now)]
    extras = DEFAULT_EXTRA
    new = [row_text(k, r, extras) for k, r in enumerate(res.rows[:max_rows], 1) if r["t"] not in was]
    if new:
        lines += ["", "נכנסו:"] + new
    left = sorted(was - set(now_set))
    if left:
        lines += ["", "יצאו: " + ", ".join(map(code, left)) + "."]
    return "\n".join(lines)


def result_hash(res, sort_metric=None):
    """Hash of the sorted ticker set plus the rounded sort-key values (alerts only on new or changed results)."""
    parts = []
    for r in sorted(res.rows, key=lambda r: r["t"]):
        v = r["ctx"].get(sort_metric) if sort_metric else None
        parts.append(f"{r['t']}:{round(v) if isinstance(v, (int, float)) else ''}")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def presets_text(presets, state):
    """/screens: every preset with its schedule and last run."""
    lines = ["📋 <b>סריקות שמורות</b>"]
    for p in presets:
        st = (state.get("presets") or {}).get(p["name"]) or {}
        sched = (f"רצה לבד: {', '.join(SESSION_HE.get(s, s) for s in p.get('sessions') or [])}"
                 if p.get("enabled") else "לא רצה לבד")
        last = f"ריצה אחרונה {code(common.il_when(st['at']))}, {code(len(st.get('tickers') or []))} מניות" if st.get("at") else "עוד לא רצה"
        lines += ["", f"• סריקה {code(p['name'])}{' (ברירת מחדל)' if p.get('builtin') else ''}",
                  f"קודים: {code(p['filters'])}", f"{sched} · {last}"]
    lines += ["", f"להרצה: {code('/screen run NAME')} · לשמירה: {code('/screen save NAME CODES')} · למחיקה: {code('/screen del NAME')}"]
    return "\n".join(lines)


SESSION_HE = {"pre": "לפני הפתיחה", "rth": "במסחר", "post": "אחרי הסגירה"}
