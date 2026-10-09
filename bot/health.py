"""/health: live checks of every data source, the scheduled jobs' last runs and the scan's own state -> Hebrew."""
import datetime as dt
import json
from pathlib import Path

from bot import common, market, scan
from bot.common import code

WORKFLOWS = (("daily-scan.yml", "סריקת בעלי העניין"), ("watchdog.yml", "בדיקת ימים חסרים"),
             ("squeeze.yml", "רשימת הסקוויז"), ("shadow.yml", "מעקב הצל"), ("telegram-listen.yml", "הבוט בטלגרם"))


def probe(name, fn):
    """Run one live check -> a ✅/❌ line (a failure is shown with its error type, never raised)."""
    try:
        detail = fn()
        ok = True
    except (Exception, SystemExit) as e:  # SystemExit: SEC_UA missing
        detail, ok = code(type(e).__name__), False
    return f"{'✅' if ok else '❌'} {name}: {'תקין' if ok else 'נכשל'}" + (f" · {detail}" if detail else "")


def _json(url):
    """One quick attempt (a health check must not sit through the full retry backoff)."""
    body = common.fetch(url, tries=2, timeout=20)
    if body is None:
        raise RuntimeError("HTTP 404")
    return json.loads(body)


def _index():
    d = dt.datetime.now(dt.timezone.utc).date()
    last = d.replace(day=1) - dt.timedelta(days=1 + 31 * ((d.month - 1) % 3))  # a day in the previous quarter
    err = RuntimeError("no daily index this quarter or last")
    for y, q in ((d.year, (d.month + 2) // 3), (last.year, (last.month + 2) // 3)):  # new quarter: not listed yet
        try:  # (SEC answers a missing listing with 404 or even an HTML 503)
            names = _json(f"https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/index.json")
        except Exception as e:
            err = e
            continue
        days = sorted(i["name"][5:13] for i in names["directory"]["item"] if i["name"].startswith("form."))
        if days:
            return f"האחרון מ־{code(common.il_date(scan._iso(days[-1]), year=False))}"
    raise err


def _data():
    _json("https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/dei/EntityCommonStockSharesOutstanding.json")
    return ""


def _efts():
    d = scan.previous_trading_day(dt.datetime.now(dt.timezone.utc).date(), quick=True) or ""
    day = scan._iso(d) if d else dt.date.today().isoformat()
    _json(f"https://efts.sec.gov/LATEST/search-index?forms=4&dateRange=custom&startdt={day}&enddt={day}")
    return ""


def _yahoo():
    r = market.chart("SPY", range="5d", interval="1d")
    if not r:
        raise RuntimeError("no data from query1/query2")
    return ""


def _telegram():
    common.tg("getMe")
    return ""


def _run_line(wf, label):
    r = common.runs(wf, completed=True)  # the listener's own run is always in progress: show the last finished one
    if not r:
        return f"• {label}: אין מידע"
    mark = {"success": "✅ הצליחה", "failure": "❌ נכשלה", "cancelled": "⚪ בוטלה", None: "⏳ רצה עכשיו"}.get(
        r["conclusion"], "⚠️")
    return f"• {label}: ריצה אחרונה {code(common.il_when(r['created_at']))} {mark}"


def report(today=None):
    today = today or dt.datetime.now(dt.timezone.utc).date()
    lines = ["🩺 <b>בדיקה שהכול עובד</b>", "", "<b>מקורות הנתונים, עכשיו</b>",
             probe("האינדקס היומי של SEC", _index),
             probe("הנתונים של SEC", _data),
             probe("החיפוש של SEC (גיבוי לאינדקס)", _efts),
             probe("המחירים מ־Yahoo", _yahoo),
             probe("הבוט בטלגרם", _telegram),
             "", "<b>הריצות האוטומטיות</b>"] + [_run_line(wf, label) for wf, label in WORKFLOWS]
    path = Path(common.env("STATE_FILE", str(common.DATA / "state.json")))
    if path.exists():
        st = scan.prune(scan.load(path), today)
        wait = [d for d in scan.weekdays(today) if d not in st["days"]]
        lines += ["", "<b>הסריקה היומית</b>", scan.heartbeat_line(st.get("heartbeat")), scan.watchdog_line(st, today)]
        if wait:
            lines.append(f"ממתינים לסריקה: {code(len(wait))} ימים.")
    else:
        lines += ["", "<b>הסריקה היומית</b>: עוד לא רצה."]
    return "\n".join(lines)
