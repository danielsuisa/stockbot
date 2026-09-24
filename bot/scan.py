"""Daily insider-cluster scan: EDGAR daily index -> Form 4 open-market purchases -> Hebrew Telegram alerts."""
import argparse
import datetime as dt
import json
import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bot import common, form4
from bot.common import code, esc, money, price

MIN_INSIDERS = common.env("MIN_INSIDERS", 2)
MIN_CLUSTER_USD = common.env("MIN_CLUSTER_USD", 100000.0)
MIN_SINGLE_USD = common.env("MIN_SINGLE_USD", 500000.0)
WINDOW_DAYS = common.env("WINDOW_DAYS", 30)
MAX_CATCHUP = common.env("MAX_CATCHUP", 5)
MAX_LINES = 6


def load(path):
    return {"days": {}, "buys": [], "alerted": {}, **(json.loads(path.read_text("utf-8")) if path.exists() else {})}


def save(path, state):
    """Atomic write, so a crash never leaves a half-written state file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, path)


def prune(state, today):
    """Drop days, purchases (by last transaction date) and alert marks older than the window."""
    since = today - dt.timedelta(WINDOW_DAYS)
    state["days"] = {k: v for k, v in sorted(state["days"].items()) if k >= since.strftime("%Y%m%d")}
    state["buys"] = sorted((b for b in state["buys"] if b["last"] >= since.isoformat()), key=lambda b: (b["last"], b["acc"]))
    accs = {b["acc"] for b in state["buys"]}
    alerted = {c: [a for a in v if a in accs] for c, v in state["alerted"].items()}
    state["alerted"] = {c: v for c, v in alerted.items() if v}
    return state


def pick(state, today):
    """Days to scan: every window weekday on the first run, else the most recent MAX_CATCHUP unprocessed ones."""
    days = [d.strftime("%Y%m%d") for d in (today - dt.timedelta(i) for i in range(WINDOW_DAYS, 0, -1)) if d.weekday() < 5]
    todo = [d for d in days if d not in state["days"]]
    return todo[-MAX_CATCHUP:] if state["days"] else todo


def _get(url):
    """form4.fetch, but one broken filing is reported (False) instead of killing the whole day."""
    try:
        return form4.fetch(url)
    except Exception as e:
        print(f"  ! {url}: {type(e).__name__}: {e}")
        return False


def scan_day(day, state, today):
    """Merge one day's listed-issuer purchase filings into state['buys'] -> 'ok' | 'holiday' | None (retry later)."""
    t0, d = time.time(), dt.datetime.strptime(day, "%Y%m%d").date()
    url = f"https://www.sec.gov/Archives/edgar/daily-index/{d.year}/QTR{(d.month + 2) // 3}/form.{day}.idx"
    idx = common.fetch(url)
    if idx is None:  # holiday, or not published yet
        old = (today - d).days >= 3
        print(f"{day}: no daily index -> {'holiday' if old else 'not published yet, will retry'}")
        return "holiday" if old else None
    rows = [r.split() for r in idx.decode("latin-1").splitlines()]
    rows = [r for r in rows if r[:1] == ["4"]]  # exactly "4": no 4/A
    urls = {r[-1].rsplit("/", 1)[1][:-4]: "https://www.sec.gov/Archives/" + r[-1] for r in rows}  # listed once per filer
    with ThreadPoolExecutor(6) as pool:
        docs = list(pool.map(_get, urls.values()))
    since, until = (today - dt.timedelta(WINDOW_DAYS)).isoformat(), today.isoformat()
    buys, listed, found, kept = {b["acc"]: b for b in state["buys"]}, common.cik_tickers(), 0, 0
    for acc, doc in zip(urls, docs):
        own = [b for b in doc["buys"] if since <= b["date"] <= until] if doc else []
        found += bool(own)
        t = own and listed.get(doc["issuer_cik"])
        if not t:
            continue
        kept += 1
        s, who = form4.summarize({"buys": own}), form4.insider(doc)
        buys[acc] = {"acc": acc, "cik": doc["issuer_cik"], "ticker": t, "company": common.tickers()[t][1],
                     "insider": who["name"], "role": who["role"], "insider_cik": who["cik"], "od": who["od"],
                     "first": s["first"], "last": s["last"], "shares": s["shares"],
                     "value": round(s["value"], 2) if s["price"] else None,
                     "price": round(s["price"], 4) if s["price"] else None, "filed": d.isoformat()}
    state["buys"], errors = list(buys.values()), sum(x is False for x in docs)
    print(f"{day}: {len(rows)} Form-4 lines, {len(urls)} filings, {sum(bool(x) for x in docs)} parsed, {errors} errors, "
          f"{found} with purchases, {kept} of them listed issuers, {time.time() - t0:.0f}s")
    return None if errors else "ok"  # failed filings -> the day is retried next run


def foreign(cik):
    """Foreign private issuer (latest annual report is a 20-F/40-F): Form 4 prices may be in local currency."""
    rows = common.filings(common.submissions(cik) or {"filings": {"recent": {}}})
    return next((f["form"] for f in rows if f["form"] in ("10-K", "20-F", "40-F")), "") in ("20-F", "40-F")


def block(bs, seen, cluster, big):
    """One issuer's Hebrew alert lines."""
    b0, total = bs[0], sum(b["value"] or 0 for b in bs)
    kind = " + ".join(x for x, on in (("אשכול רכישות", cluster), ("רכישה גדולה", big)) if on)
    why = ([f"{code(cluster[0])} נושאי משרה/דירקטורים שונים רכשו יחד {code(money(cluster[1]))}"] if cluster else []) + \
          ([f"רכישה בודדת בהיקף {code(money(big))}"] if big else [])
    lines = [f"🟢 <b>{kind}</b> · {code(b0['ticker'])} · {esc(b0['company'])}", "סיבה: " + " · ".join(why),
             f"סה״כ ב־{code(WINDOW_DAYS)} יום: {code(len(bs))} דיווחי רכישה · "
             f"{code(len({b['insider_cik'] or b['insider'] for b in bs}))} רוכשים · {code(money(total))}"]
    bs = sorted(bs, key=lambda b: (b["acc"] not in seen, b["value"] or 0), reverse=True)  # new first, then largest
    for b in bs[:MAX_LINES]:
        lines.append(f"• רכש {esc(b['insider'])} ({esc(b['role'])}) · {code(b['last'])} · {code(money(b['value']))}"
                     f" @ {code(price(b['price']))}{' 🆕' if b['acc'] not in seen else ''}")
    if len(bs) > MAX_LINES:
        lines.append(f"ועוד {code(len(bs) - MAX_LINES)} דיווחים.")
    url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={b0['cik']}&type=4&owner=include"
    lines.append(f'🔗 <a href="{esc(url)}">כל דיווחי Form 4 של החברה ב־EDGAR</a>')
    return "\n".join(lines)


def alerts(state, today):
    """Qualifying issuers with not-yet-alerted filings -> [(Hebrew message, {cik: accessions it covers})]."""
    by = defaultdict(list)
    for b in state["buys"]:
        by[str(b["cik"])].append(b)
    blocks, hits, fpi, qualifying = [], {}, [], 0  # blocks: (text, {cik: accs})
    for cik, bs in sorted(by.items(), key=lambda kv: -sum(b["value"] or 0 for b in kv[1])):
        od = [b for b in bs if b["od"]]
        people, od_total = {b["insider_cik"] or b["insider"] for b in od}, sum(b["value"] or 0 for b in od)
        cluster = len(people) >= MIN_INSIDERS and od_total >= MIN_CLUSTER_USD and (len(people), od_total)
        big = max(b["value"] or 0 for b in bs)
        big = big >= MIN_SINGLE_USD and big
        seen = set(state["alerted"].get(cik, []))
        qualifying += bool(cluster or big)
        if not (cluster or big) or all(b["acc"] in seen for b in bs):
            continue
        hits[cik] = [b["acc"] for b in bs]
        if foreign(int(cik)):  # "$" and USD thresholds may be wrong (e.g. Bradesco reports BRL prices): name only
            fpi.append((code(bs[0]["ticker"]), cik))
        else:
            blocks.append((block(bs, seen, cluster, big), {cik: hits[cik]}))
    if fpi:
        blocks.append((f"ℹ️ רכישות חדשות גם אצל מנפיקים זרים: {', '.join(t for t, _ in fpi)} — לא הוצגו, כי המחיר"
                       " בדיווח שלהם עשוי להיות במטבע מקומי ולא בדולר (אפשר לשלוח לי את הטיקר לדוח מלא).",
                       {c: hits[c] for _, c in fpi}))
    print(f"alerts: {qualifying} qualifying issuer(s), {len(hits)} with new filings ({len(fpi)} foreign, names only)")
    msgs = []
    for blk, marks in [(f"🔔 <b>רכישות בעלי עניין בשוק הפתוח</b> · {code(today.isoformat())}", {})] + blocks:
        if msgs and len(msgs[-1][0]) + len(blk) < 3500:  # never split one issuer across two messages
            msgs[-1][0] += "\n\n" + blk
            msgs[-1][1].update(marks)
        else:
            msgs.append([blk, dict(marks)])
    return msgs if blocks else []


def main():
    ap = argparse.ArgumentParser(description="Daily EDGAR Form 4 insider-cluster scan")
    ap.add_argument("--days", help="force specific days: YYYYMMDD[,YYYYMMDD...]")
    ap.add_argument("--dry", action="store_true", help="do not write the state file")
    a = ap.parse_args()
    t0, today = time.time(), dt.datetime.now(dt.timezone.utc).date()
    path = Path(common.env("STATE_FILE", str(common.DATA / "state.json")))
    state = prune(load(path), today)
    days = a.days.split(",") if a.days else pick(state, today)
    print(f"scan {today}: {len(days)} day(s) {' '.join(days)}; state {path}")
    for day in days:
        status = scan_day(day, state, today)
        if status:
            state["days"][day] = status
        if not a.dry:
            save(path, prune(state, today))  # after every day: crash-safe
    n = 0
    for text, marks in alerts(state, today):
        common.send(text)
        state["alerted"].update(marks)  # per delivered message: a later failure never re-sends this one
        n += len(marks)
        if not a.dry:
            save(path, state)
    print(f"done: {n} issuer alert(s), {len(state['buys'])} purchase filings in state, {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
