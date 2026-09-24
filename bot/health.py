"""/health: live checks of every data source, the scheduled jobs' last runs and the scan's own state -> Hebrew."""
import datetime as dt
import json
import time
from pathlib import Path

from bot import common, market, scan
from bot.common import code, esc

WORKFLOWS = (("daily-scan.yml", "סריקה יומית"), ("watchdog.yml", "שומר ימים חסרים"),
             ("telegram-listen.yml", "מאזין טלגרם"))


def probe(name, fn):
    """Run one live check -> a ✅/❌ line with its latency (a failure is shown, never raised)."""
    t0 = time.time()
    try:
        detail = fn()
        ok = True
    except (Exception, SystemExit) as e:  # SystemExit: SEC_UA missing
        detail, ok = f"{code(type(e).__name__)} {esc(str(e)[:80])}", False
    took = code(f"{time.time() - t0:.1f}s")
    return f"{'✅' if ok else '❌'} {name}: {'תקין' if ok else 'נכשל'} ({took})" + (f" · {detail}" if detail else "")


def _json(url):
    """One quick attempt (a health check must not sit through the full retry backoff)."""
    body = common.fetch(url, tries=2, timeout=20)
    if body is None:
        raise RuntimeError("HTTP 404")
    return json.loads(body)


def _index():
    d = dt.datetime.now(dt.timezone.utc).date()
    names = _json(f"https://www.sec.gov/Archives/edgar/daily-index/{d.year}/QTR{(d.month + 2) // 3}/index.json")
    days = sorted(i["name"][5:13] for i in names["directory"]["item"] if i["name"].startswith("form."))
    return f"האינדקס האחרון {code(scan._iso(days[-1]))}" if days else "אין עדיין אינדקס ברבעון"


def _data():
    j = _json("https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/dei/EntityCommonStockSharesOutstanding.json")
    return f"{code(j.get('entityName', '?'))}"


def _efts():
    d = scan.previous_trading_day(dt.datetime.now(dt.timezone.utc).date()) or ""
    day = scan._iso(d) if d else dt.date.today().isoformat()
    j = _json(f"https://efts.sec.gov/LATEST/search-index?forms=4&dateRange=custom&startdt={day}&enddt={day}")
    return f"{code((j['hits']['total'] or {}).get('value', 0))} דיווחי {code('Form 4')} ב־{code(day)}"


def _yahoo():
    r = market.chart("SPY", range="5d", interval="1d")
    if not r:
        raise RuntimeError("no data from query1/query2")
    return f"{code('SPY')} {code(common.price(r['meta']['regularMarketPrice']))}"


def _telegram():
    return f"הבוט {code('@' + common.tg('getMe')['username'])}"


def _run_line(wf, label):
    r = common.runs(wf)
    if not r:
        return f"• {label}: אין מידע (אין גישה ל־{code('GitHub API')})"
    mark = {"success": "✅", "failure": "❌", "cancelled": "⚪", None: "⏳"}.get(r["conclusion"], "⚠")
    when = r["created_at"][:16].replace("T", " ")
    return f"• {label}: {mark} ריצה אחרונה {code(when)} · {code(r['conclusion'] or r['status'])} · {code(r['event'])}"


def report(today=None):
    today = today or dt.datetime.now(dt.timezone.utc).date()
    lines = ["🩺 <b>בדיקת בריאות</b>", "<b>מקורות נתונים (בדיקה חיה עכשיו)</b>",
             probe(f"האינדקס היומי של {code('SEC EDGAR')}", _index),
             probe(f"ממשק הנתונים {code('data.sec.gov')}", _data),
             probe(f"חיפוש טקסט מלא {code('EFTS')} (גיבוי לאינדקס)", _efts),
             probe(f"מחירי {code('Yahoo')}", _yahoo),
             probe("בוט טלגרם", _telegram),
             "", "<b>ריצות מתוזמנות</b>"] + [_run_line(wf, label) for wf, label in WORKFLOWS]
    path = Path(common.env("STATE_FILE", str(common.DATA / "state.json")))
    if path.exists():
        st = scan.prune(scan.load(path), today)
        wait = [d for d in scan.weekdays(today) if d not in st["days"]]
        lines += ["", "<b>מצב הסריקה</b>", scan.heartbeat_line(st.get("heartbeat")), scan.watchdog_line(st, today),
                  f"ימים ממתינים לסריקה: {', '.join(code(scan._iso(d)) for d in wait) if wait else 'אין'}"]
    else:
        lines += ["", f"<b>מצב הסריקה</b>: עוד אין קובץ {code('data/state.json')}."]
    return "\n".join(lines)
