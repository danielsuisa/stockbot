"""On-demand forensic one-page report for a US ticker -> one Telegram message (Hebrew)."""
import datetime as dt
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor

from bot import common, form4, fundamentals, tenk
from bot.common import code, esc, money, price

INSIDER_DAYS = common.env("INSIDER_DAYS", 180)
MAX_FORM4 = 150  # bounds run time for mega-caps with heavy Form 4 traffic
MAX_LINES = 6


def insiders(cik, rows):
    """Open-market purchases (P/A) of this issuer's stock in the last INSIDER_DAYS, from raw Form 4 XML."""
    since = (dt.date.today() - dt.timedelta(days=INSIDER_DAYS)).isoformat()
    f4 = [f for f in rows if f["form"] == "4" and f["filingDate"] >= since]
    urls = [common.doc_url(cik, f["accessionNumber"], f["primaryDocument"].rsplit("/", 1)[-1]) for f in f4[:MAX_FORM4]]
    with ThreadPoolExecutor(4) as pool:
        docs = list(pool.map(form4.fetch, urls))
    buys = []
    for doc in docs:
        if not doc or doc["issuer_cik"] != cik:  # the CIK may itself be a reporting owner elsewhere
            continue
        own = [b for b in doc["buys"] if b["date"] >= since]
        if own:
            buys.append({**form4.summarize({"buys": own}), **form4.insider(doc)})
    out = [f"<b>רכישות בעלי עניין בשוק הפתוח ({INSIDER_DAYS} יום)</b>"]
    if len(f4) > MAX_FORM4:
        out.append(f"נבדקו {code(MAX_FORM4)} מתוך {code(len(f4))} דיווחי Form 4 (האחרונים).")
    if not buys:
        return out + [f"לא נמצאו רכישות (קוד {code('P')}) ב־{code(len(urls))} דיווחי Form 4."]
    buys.sort(key=lambda b: b["last"], reverse=True)
    out.append(f"סה״כ {code(len(buys))} דיווחי רכישה · {code(len({b['cik'] for b in buys}))} רוכשים"
               f" · {code(money(sum(b['value'] for b in buys)))}")
    for b in buys[:MAX_LINES]:
        out.append(f"• רכש {esc(b['name'])} ({esc(b['role'])}) · {code(b['last'])} · {code(money(b['value']))}"
                   f" @ {code(price(b['price']))}")
    if len(buys) > MAX_LINES:
        out.append(f"ועוד {code(len(buys) - MAX_LINES)} דיווחים.")
    return out


def report(ticker):
    """Full report as Telegram HTML (common.send appends the disclaimer)."""
    t = ticker.strip().upper().lstrip("$").replace(".", "-")
    hit = common.tickers().get(t)
    if not hit:
        return f"לא מצאתי את הטיקר {code(t)} ברשימת החברות של SEC."
    cik, name = hit
    sub = common.submissions(cik) or {}
    rows = common.filings(sub) if sub else []
    sic = int(sub.get("sic") or 0) or None
    lines = [f"📊 <b>דוח פורנזי</b> · {code(t)} · {esc(name)}",
             f"ענף: {code(f'SIC {sic}') if sic else 'חסר'} {esc(sub.get('sicDescription') or '')}"]
    parts = (("דוחות כספיים", lambda: fundamentals.format_he(fundamentals.analyze(cik, t, sic))),
             ("בעלי עניין", lambda: insiders(cik, rows)),
             ("גורמי סיכון", lambda: tenk.format_he(tenk.analyze(cik, rows))))
    for title, build in parts:
        lines.append("")
        try:
            lines += build()
        except Exception as e:  # one broken section must not kill the report
            traceback.print_exc()
            lines.append(f"⚠️ החלק \"{title}\" נכשל ({code(type(e).__name__)}) ולכן הושמט.")
    return "\n".join(lines)


def main():
    t = sys.argv[1] if len(sys.argv) > 1 else common.env("TICKER")
    if not t.strip():
        sys.exit("usage: python -m bot.check TICKER   (or env TICKER)")
    try:
        msg = report(t)
    except (Exception, SystemExit) as e:  # SEC down / SEC_UA missing: say so in Telegram, keep the run red
        common.send(f"⚠️ הדוח עבור {code(t.strip().upper())} נכשל ({code(type(e).__name__)}): {common.failure(e)}")
        raise
    common.send(msg)


if __name__ == "__main__":
    main()
