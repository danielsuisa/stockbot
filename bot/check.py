"""On-demand forensic one-page report for a US ticker -> one Telegram message (Hebrew)."""
import datetime as dt
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor

from bot import common, form4, fundamentals, market, tenk
from bot.common import code, esc, price

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
    out = [f"👥 <b>רכישות של בעלי עניין ב־{code(INSIDER_DAYS)} הימים האחרונים</b>"]
    if len(f4) > MAX_FORM4:
        out.append(f"נבדקו רק {code(MAX_FORM4)} הדיווחים האחרונים מתוך {code(len(f4))}.")
    if not buys:
        return out + ["לא היו רכישות."]
    buys = sorted(form4.joint(buys), key=lambda b: b["last"], reverse=True)  # joint fund/director reports = 1
    op = [b for b in buys if b["kind"] == "open"]
    if op:
        out.append(f"{code(len({b['cik'] for b in op}))} בעלי עניין קנו ב־{common.amount(sum(b['value'] for b in op))}"
                   " בסך הכול.")
    for b in buys[:MAX_LINES]:
        tag = " · בתוכנית קבועה מראש, לא נספרה" if b["kind"] == "plan" else " · רכישה סמלית" if form4.symbolic(b) else ""
        pct = f" · {code(format(b['pct'], '.0%'))} מהאחזקה" if b.get("pct") is not None else ""
        out.append(f"• רכש {esc(b['name'])} ({esc(b['role'])}): {common.amount(b['value'])}, {code(price(b['price']))}"
                   f" למניה, ב־{code(common.il_date(b['last'], year=False))}{pct}{tag}")
    if len(buys) > MAX_LINES:
        out.append(f"ועוד {code(len(buys) - MAX_LINES)} רכישות.")
    return out


def sizing(t):
    """5.4: 30-day average dollar volume, flagged under $2M a day (informational only; nothing here suggests a trade)."""
    liq = market.liquidity(t)
    if liq is None:
        return []
    return [f"💧 מחזור יומי ממוצע: {common.amount(liq)}" + (" ⚠️ נמוך" if liq < market.LOW_LIQUIDITY else "")]


def report(ticker):
    """Full report as Telegram HTML (common.send appends the disclaimer)."""
    t = ticker.strip().upper().lstrip("$").replace(".", "-")
    hit = common.tickers().get(t)
    if not hit:
        return f"לא מצאתי את {code(t)} ברשימת החברות של SEC."
    cik, name = hit
    sub = common.submissions(cik) or {}
    rows = common.filings(sub) if sub else []
    sic = int(sub.get("sic") or 0) or None
    lines = [f"📊 <b>דוח על {code(t)} · {esc(name)}</b>"]
    if sub.get("sicDescription"):
        lines.append(f"ענף: {esc(sub['sicDescription'])}")
    lines += sizing(t)
    parts = (("דוחות כספיים", lambda: fundamentals.format_he(fundamentals.analyze(cik, t, sic))),
             ("בעלי עניין", lambda: insiders(cik, rows)),
             ("גורמי סיכון", lambda: tenk.format_he(tenk.analyze(cik, rows))))
    for title, build in parts:
        lines.append("")
        try:
            lines += build()
        except Exception as e:  # one broken section must not kill the report
            traceback.print_exc()
            lines.append(f"⚠️ לא הצלחתי להביא את החלק \"{title}\".")
    return "\n".join(lines)


def main():
    t = sys.argv[1] if len(sys.argv) > 1 else common.env("TICKER")
    if not t.strip():
        sys.exit("usage: python -m bot.check TICKER   (or env TICKER)")
    try:
        msg = report(t)
    except (Exception, SystemExit) as e:  # SEC down / SEC_UA missing: say so in Telegram, keep the run red
        common.send(f"⚠️ הדוח על {code(t.strip().upper())} נכשל. {common.failure(e)}")
        raise
    common.send(msg, signal=True)


if __name__ == "__main__":
    main()
