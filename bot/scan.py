"""Daily insider-cluster scan: EDGAR daily index -> Form 4 open-market purchases -> Hebrew Telegram alerts."""
import argparse
import datetime as dt
import functools
import json
import math
import os
import re
import statistics
import sys
import time
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bot import common, form4, fundamentals, journal, market
from bot.common import code, esc, money, price

MIN_INSIDERS = common.env("MIN_INSIDERS", 3)
MIN_CLUSTER_USD = common.env("MIN_CLUSTER_USD", 100000.0)
MIN_SINGLE_USD = common.env("MIN_SINGLE_USD", 500000.0)
WINDOW_DAYS = common.env("WINDOW_DAYS", 30)
MAX_CATCHUP = common.env("MAX_CATCHUP", 5)
MAX_LINES = 6
MAX_PAY = 25  # DEF 14A downloads per run (best-effort pay ratio; the holdings ratio is always there)
SCHEMA = 2
DEFAULTS = {"days": {}, "buys": [], "alerted": {}, "info": [], "regime": None, "prices": {},
            "seen": {},  # day -> accessions already processed (rescans only fetch what is new)
            "sent": {},  # cik -> snapshot of the last alert (id, totals, accessions) for corrections
            "removed": {},  # original accession -> the 4/A that withdrew its purchases
            "heartbeat": None}
RESCAN = 3  # previous trading days re-read every run for late filings and 4/A amendments
LEGACY = 5  # days per run re-read to upgrade rows saved before schema 2 (no trade signature -> joint filings unmerged)
INDICES = ("SPY", "IWM", "%5EVIX")


def migrate_row(r):
    """A purchase row from schema 1 (before kinds/weights existed) -> schema 2, in place."""
    if "kind" not in r:
        role = r.get("role", "")
        r.update(kind="open", plan_value=0.0, pct=None, post=None,
                 weight=0.0 if not r.get("od") else 1.5 if form4.TOP.search(role) else 0.7 if role == "דירקטור" else 1.0)
    return r


def load(path):
    """State file -> dict with every schema-2 key (older files are migrated in memory; saved on the next write)."""
    st = {**json.loads(json.dumps(DEFAULTS)), **(json.loads(path.read_text("utf-8")) if path.exists() else {})}
    st["buys"] = [migrate_row(r) for r in st["buys"]]
    st["v"] = SCHEMA
    return st


def save(path, state):
    """Atomic write, so a crash never leaves a half-written state file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, path)


def prune(state, today):
    """Drop days, purchases (by last transaction date), alert marks and informational rows older than the window."""
    for k, v in DEFAULTS.items():  # tolerate partial/older state dicts
        state.setdefault(k, json.loads(json.dumps(v)))
    since = today - dt.timedelta(WINDOW_DAYS)
    state["days"] = {k: v for k, v in sorted(state["days"].items()) if k >= since.strftime("%Y%m%d")}
    state["buys"] = sorted((b for b in state["buys"] if b["last"] >= since.isoformat()), key=lambda b: (b["last"], b["acc"]))
    accs, ciks = {b["acc"] for b in state["buys"]}, {b["cik"] for b in state["buys"]}
    alerted = {c: [a for a in v if a in accs] for c, v in state["alerted"].items()}
    state["alerted"] = {c: v for c, v in alerted.items() if v}
    state["info"] = [i for i in state["info"] if i["last"] >= since.isoformat() and i["cik"] in ciks]  # context only
    state["sent"] = {c: s for c, s in state["sent"].items() if s["date"] >= since.isoformat()}
    state["removed"] = {a: r for a, r in state["removed"].items() if r["date"] >= since.isoformat()}
    keep = sorted(state["days"])[-(RESCAN + 2):]
    state["seen"] = {d: v for d, v in state["seen"].items() if d in keep}
    tickers = {b["ticker"] for b in state["buys"]} | set(INDICES)
    state["prices"] = {t: v for t, v in state["prices"].items() if t in tickers}
    return state


def weekdays(today):
    """The window's weekdays, oldest first (today is excluded: its index is only published tonight)."""
    return [d.strftime("%Y%m%d") for d in (today - dt.timedelta(i) for i in range(WINDOW_DAYS, 0, -1)) if d.weekday() < 5]


def pick(state, today):
    """Days to scan: every window weekday on the first run, else the most recent MAX_CATCHUP unprocessed ones."""
    todo = [d for d in weekdays(today) if d not in state["days"]]
    return todo[-MAX_CATCHUP:] if state["days"] else todo


def _iso(day):
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


def status(today=None):
    """Hebrew /status: the last scanned trading day and what the rolling state holds."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    path = Path(common.env("STATE_FILE", str(common.DATA / "state.json")))
    if not path.exists():
        return f"📋 הסריקה היומית עוד לא רצה (אין קובץ {code('data/state.json')})."
    st = prune(load(path), today)
    ok = sorted(d for d, v in st["days"].items() if v == "ok")
    wait = [d for d in weekdays(today) if d not in st["days"]]
    return "\n".join((
        "📋 <b>מצב הסריקה היומית</b>",
        f"יום המסחר האחרון שנסרק: {code(_iso(ok[-1])) if ok else 'עדיין אין'}",
        f"ב־{code(WINDOW_DAYS)} הימים האחרונים: {code(len(ok))} ימי מסחר נסרקו · {code(len(st['days']) - len(ok))} חגים"
        + (f" · {code(len(wait))} ממתינים לסריקה (האחרון {code(_iso(wait[-1]))})" if wait else ""),
        f"דיווחי רכישה בזיכרון: {code(len(st['buys']))} · חברות שכבר קיבלו התראה: {code(len(st['alerted']))}",
        f"כללי התראה: לפחות {code(MIN_INSIDERS)} נושאי משרה שונים שרכשו בשוק הפתוח יחד {code(money(MIN_CLUSTER_USD))},"
        f" או רכישה בודדת של {code(money(MIN_SINGLE_USD))} (בלי תוכניות {code('10b5-1')})",
        heartbeat_line(st.get("heartbeat")), watchdog_line(st, today),
        f"סריקה אוטומטית בימים ג׳–ש׳ ב־{code('05:30 UTC')}; הפקודה {code('/scan')} מריצה אותה עכשיו."))


def heartbeat_line(hb):
    """/status and /health: what the last scan run reported (state["heartbeat"])."""
    if not hb:
        return "💓 דופק: עוד אין נתונים מריצה של הגרסה הנוכחית."
    failed = f" · ימים שנכשלו: {', '.join(code(_iso(d)) for d in hb['failed'])}" if hb.get("failed") else ""
    return (f"💓 ריצה אחרונה: {code(hb.get('at', '—'))} · {'תקינה ✅' if hb.get('ok') else 'נכשלה ❌'} · "
            f"{code(hb.get('filings', 0))} הגשות · {code(hb.get('errors', 0))} שגיאות · "
            f"{code(hb.get('alerts', 0))} התראות{failed}")


def missed(st, today):
    """The watchdog's test -> (previous trading day, last scanned day, trading days after it never scanned)."""
    prev = previous_trading_day(today)
    last = max((d for d, v in st["days"].items() if v == "ok"), default="")
    return prev, last, [d for d in weekdays(today) if last < d <= (prev or "") and d not in st["days"]]


def watchdog_line(st, today):
    """/status and /health: the missed-day watchdog's view now, plus its last scheduled run when GitHub answers."""
    prev, _, gap = missed(st, today)
    if gap:
        head = (f"🐕 שומר ימים חסרים: ⚠ עוד לא נסרקו {', '.join(code(_iso(d)) for d in gap)}"
                f" (הסריקה ב־{code('05:30 UTC')} והשומר ב־{code('14:00 UTC')} משלימים ימים חסרים)")
    else:
        head = f"🐕 שומר ימים חסרים: תקין, יום המסחר הקודם {code(_iso(prev)) if prev else 'לא ידוע'} נסרק"
    r = common.runs("watchdog.yml")
    if r:
        head += f" · ריצה אחרונה {code(r['created_at'][:16].replace('T', ' '))} {code(r['conclusion'] or r['status'])}"
    return head


def _get(url):
    """form4.fetch, but one broken filing is reported (False) instead of killing the whole day."""
    try:
        return form4.fetch(url)
    except Exception as e:
        print(f"  ! {url}: {type(e).__name__}: {e}")
        return False


@functools.lru_cache(None)
def published(year, q):
    """File names SEC lists for a daily-index quarter, or None if the listing is unavailable. The listing is
    trusted, not error codes: a missing index has come back as 404, S3 403 XML and an HTML 503 page."""
    try:
        base = f"https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{q}"
        return {i["name"] for i in common.get_json(f"{base}/index.json")["directory"]["item"]}
    except Exception as e:
        print(f"daily-index listing {year} QTR{q} unavailable ({type(e).__name__}) - fetching files directly")
        return None


def make_row(acc, doc, own, ticker, filed):
    """One filing's in-window purchase lines -> a state row (open-market part, or kind "plan" if all 10b5-1)."""
    s, who = form4.summarize({"buys": own}), form4.insider(doc)
    return {"acc": acc, "cik": doc["issuer_cik"], "ticker": ticker, "company": common.tickers()[ticker][1],
            "insider": who["name"], "role": who["role"], "insider_cik": who["cik"], "od": who["od"],
            "weight": who.get("weight", 0.0), "first": s["first"], "last": s["last"], "shares": s["shares"],
            "value": round(s["value"], 2) if s["price"] else None,
            "price": round(s["price"], 4) if s["price"] else None, "filed": filed, "form": doc["form"] or "4",
            "sig": s["sig"], "indirect": s["indirect"], "kind": s["kind"], "plan_value": round(s["plan_value"], 2),
            "post": s["post"], "pct": round(s["pct"], 4) if s["pct"] is not None else None}


def amend(buys, acc, doc, row, removed=None):
    """Apply a Form 4/A: replace the original filing's row (same issuer and insider, filed on the amendment's
    'date of original submission', else the same trades) under the ORIGINAL accession, so alert marks and the
    journal keep pointing at it; an amendment without in-window purchases removes that row (noted in `removed`
    for corrections). -> original acc or None."""
    who = form4.insider(doc)
    same = [b for b in buys.values() if b["cik"] == doc["issuer_cik"] and b["insider_cik"] == who["cik"]
            and b["acc"] != acc]
    orig = next((b for b in same if doc.get("original") and b["filed"] == doc["original"]), None) \
        or next((b for b in same if row and b.get("sig") == row["sig"]), None)
    if not orig:
        return None
    if row:
        buys[orig["acc"]] = {**row, "acc": orig["acc"], "filed": orig["filed"], "amended_by": acc}
    else:
        del buys[orig["acc"]]
        if removed is not None:
            removed[orig["acc"]] = {"by": acc, "cik": orig["cik"], "date": orig["last"]}
    return orig["acc"]


def efts_urls(day):
    """Fallback when the daily index is unavailable: SEC full-text search for the day's Form 4 and 4/A
    -> {accession: full-submission .txt URL} (100 hits per page)."""
    out, iso = {}, _iso(day)
    for form in ("4", "4/A"):
        start = 0
        while True:
            d = common.get_json(f"https://efts.sec.gov/LATEST/search-index?forms={form}&dateRange=custom"
                                f"&startdt={iso}&enddt={iso}&from={start}") or {}
            hits = d.get("hits", {}).get("hits", [])
            for h in hits:
                acc, cik = h["_id"].split(":")[0], int((h["_source"].get("ciks") or ["0"])[0])
                out[acc] = common.doc_url(cik, acc, acc + ".txt")
            start += len(hits)
            if not hits or start >= d["hits"]["total"]["value"] or start >= 5000:
                break
    return out


def index_urls(day):
    """One day's Form 4 / 4/A filings -> ({accession: .txt URL} or None for holiday/not-yet-published, source).
    The quarter listing decides whether the index exists; if it exists but cannot be read after retries, the
    full-text search API is the fallback."""
    d = dt.datetime.strptime(day, "%Y%m%d").date()
    names = published(d.year, (d.month + 2) // 3)
    if names is not None and f"form.{day}.idx" not in names:
        return None, "index"
    try:
        idx = common.fetch(f"https://www.sec.gov/Archives/edgar/daily-index/{d.year}/QTR{(d.month + 2) // 3}/form.{day}.idx")
    except Exception as e:
        common.log("index_unavailable", day=day, error=f"{type(e).__name__}: {e}", fallback="efts")
        urls = efts_urls(day)
        if not urls:
            raise
        return urls, "efts"
    if idx is None:
        return None, "index"
    rows = [r.split() for r in idx.decode("latin-1").splitlines()]
    rows = [r for r in rows if r[:1] in (["4"], ["4/A"])]
    return {r[-1].rsplit("/", 1)[1][:-4]: "https://www.sec.gov/Archives/" + r[-1] for r in rows}, "index"  # once per filer


def scan_day(day, state, today, stats=None, rescan=False):
    """Merge one day's listed-issuer purchase filings (and Form 4/A amendments) into state['buys'], keep other
    acquisitions as informational rows -> 'ok' | 'holiday' | None (retry later). A rescan only fetches filings
    that were not in the day's index before (late filings / amendments)."""
    t0, d = time.time(), dt.datetime.strptime(day, "%Y%m%d").date()
    stats = stats if stats is not None else {}
    urls, source = index_urls(day)
    if urls is None:  # holiday, or not published yet
        old = (today - d).days >= 3
        print(f"{day}: no daily index -> {'holiday' if old else 'not published yet, will retry'}")
        return "holiday" if old else None
    state.setdefault("seen", {})
    if rescan:
        urls = {a: u for a, u in urls.items() if a not in set(state["seen"].get(day, []))}
    with ThreadPoolExecutor(6) as pool:
        docs = list(pool.map(_get, urls.values()))
    since, until = (today - dt.timedelta(WINDOW_DAYS)).isoformat(), today.isoformat()
    buys, listed, found, kept, amended = {b["acc"]: b for b in state["buys"]}, common.cik_tickers(), 0, 0, 0
    info = {i["acc"]: i for i in state.get("info", [])}
    removed = state.setdefault("removed", {})
    for acc, doc in zip(urls, docs):
        t = doc and listed.get(doc["issuer_cik"])
        if not t:
            continue
        own = [b for b in doc["buys"] if since <= b["date"] <= until]
        row = make_row(acc, doc, own, t, d.isoformat()) if own else None
        if doc["form"] == "4/A" and amend(buys, acc, doc, row, removed):
            amended += 1
            continue
        other = [a for a in doc["acq"] if since <= a["date"] <= until]
        if other:  # exercises, grants, ...: never signals, kept (as line counts) as context for issuers with purchases
            kinds = defaultdict(int)
            for a in other:
                kinds[a["kind"]] += 1
            info[acc] = {"acc": acc, "cik": doc["issuer_cik"], "last": max(a["date"] for a in other), "kinds": dict(kinds)}
        if row:
            found += 1
            kept += 1
            if not buys.get(acc, {}).get("amended_by"):  # re-reading an original never undoes its 4/A
                buys[acc] = row
    state["buys"], state["info"], errors = list(buys.values()), list(info.values()), sum(x is False for x in docs)
    ok = [a for a, x in zip(urls, docs) if x is not False]
    state["seen"][day] = sorted(set(state["seen"].get(day, [])) | set(ok))
    parsed = sum(bool(x) for x in docs)
    rec = {"day": day, "rescan": rescan, "filings": len(urls), "parsed": parsed, "errors": errors, "purchases": kept,
           "amendments": amended, "source": source, "seconds": round(time.time() - t0)}
    stats.setdefault("days", []).append(rec)
    common.log("day", **rec)
    print(f"{day}: {len(urls)} {'new ' if rescan else ''}filings, {parsed} parsed, {errors} errors, "
          f"{kept} listed-issuer purchase filings, {amended} amendments applied, source {source}, {rec['seconds']}s")
    return None if errors else "ok"  # failed filings -> the day is retried next run


def foreign(cik):
    """Foreign private issuer (latest annual report is a 20-F/40-F): Form 4 prices may be in local currency."""
    rows = common.filings(common.submissions(cik) or {"filings": {"recent": {}}})
    return next((f["form"] for f in rows if f["form"] in ("10-K", "20-F", "40-F")), "") in ("20-F", "40-F")


_pay_budget = [MAX_PAY]


@functools.lru_cache(None)
def pay(cik):
    """Best effort: the latest DEF 14A's pay-versus-performance XBRL -> {"peo": CEO total pay, "neo": average of the
    other named officers, "end": year end}; {} when there is no tagged proxy (or the per-run download budget is spent)."""
    if _pay_budget[0] <= 0:
        return {}
    rows = common.filings(common.submissions(cik) or {"filings": {"recent": {}}})
    f = next((r for r in rows if r["form"] == "DEF 14A" and r["primaryDocument"]), None)
    if not f:
        return {}
    _pay_budget[0] -= 1
    x = (common.fetch(common.doc_url(cik, f["accessionNumber"], f["primaryDocument"])) or b"").decode("utf-8", "replace")
    ends = {}
    for m in re.finditer(r'<(?:\w+:)?context\b[^>]*\bid="([^"]+)"[^>]*>(.*?)</(?:\w+:)?context>', x, re.S):
        e = re.search(r"<(?:\w+:)?endDate>([\d-]+)<", m.group(2))
        if e and "segment" not in m.group(2):  # company-wide figures only (no per-person/axis breakdown)
            ends[m.group(1)] = e.group(1)
    out = {}
    for key, tag in (("peo", "PeoTotalCompAmt"), ("neo", "NonPeoNeoAvgTotalCompAmt")):
        best = None
        for m in re.finditer(r'<ix:nonFraction\b([^>]*\bname="ecd:' + tag + r'"[^>]*)>([\d,.]+)</ix:nonFraction>', x):
            ctx = re.search(r'contextRef="([^"]+)"', m.group(1))
            scale = re.search(r'scale="(-?\d+)"', m.group(1))
            end = ends.get(ctx.group(1)) if ctx else None
            if end and (best is None or end > best[0]):
                best = (end, float(m.group(2).replace(",", "")) * 10 ** int(scale.group(1) if scale else 0))
        if best:
            out[key], out["end"] = best[1], max(out.get("end", ""), best[0])
    return out


def pay_ratio(b):
    """Purchase $ / annual pay for a CEO (PEO) or another officer (named-officer average), else None."""
    if not b["value"] or b["weight"] < 1.0:
        return None
    p = pay(int(b["cik"]))
    comp = p.get("peo") if re.search(r"(?i)\b(?:ceo|chief\s+executive|principal\s+executive)\b", b["role"]) else p.get("neo")
    return b["value"] / comp if comp else None


def quality(rows, regime):
    """0-100 cluster quality from open-market rows -> (score, parts). Breadth: distinct officer/director buyers
    (0-30); seniority: role weights CEO/CFO/Chair 1.5, officers 1.0, directors 0.7, token buys x0.5 (0-25); size:
    log of total $ (0-20); conviction: median % of holdings bought (0-15); market: panic +10, euphoria -10."""
    od = [r for r in rows if r["od"]]
    people = {}
    for r in od:  # one weight per person (their largest role weight), halved when all their buys are token buys
        k = r["insider_cik"] or r["insider"]
        w = r["weight"] * (0.5 if form4.symbolic(r) else 1.0)
        people[k] = max(people.get(k, 0.0), w)
    total = sum(r["value"] or 0 for r in rows)
    pcts = [r["pct"] for r in od if r.get("pct") is not None and not form4.symbolic(r)]
    parts = {"breadth": min(30, 10 * max(0, len(people) - 1)),
             "seniority": min(25, round(25 * sum(people.values()) / 4.5)),
             "size": round(20 * min(1.0, max(0.0, math.log10(total / 1e5) / 2))) if total > 0 else 0,
             "conviction": round(15 * min(1.0, statistics.median(pcts) / 0.25)) if pcts else 0,
             "market": {"panic": 10, "euphoria": -10}.get((regime or {}).get("tag"), 0)}
    return max(0, min(100, sum(parts.values()))), parts


PARTS = {"breadth": "רוחב", "seniority": "בכירות", "size": "סכום", "conviction": "שכנוע", "market": "שוק"}


def evaluate(bs, regime):
    """One issuer's rows -> dict: unique open rows, plan rows, cluster (n, $) or False, big single $ or False,
    quality score and parts. Only open-market purchases outside 10b5-1 plans count."""
    uniq = form4.joint(bs, "insider")  # a fund + its board member reporting one purchase count once
    op = [b for b in uniq if b.get("kind", "open") == "open"]
    od = [b for b in op if b["od"]]
    people, od_total = {b["insider_cik"] or b["insider"] for b in od}, sum(b["value"] or 0 for b in od)
    cluster = len(people) >= MIN_INSIDERS and od_total >= MIN_CLUSTER_USD and (len(people), od_total)
    big = max((b["value"] or 0 for b in op), default=0)
    score, parts = quality(op, regime)
    return {"uniq": uniq, "open": op, "plan": [b for b in uniq if b.get("kind") == "plan"], "cluster": cluster,
            "big": big >= MIN_SINGLE_USD and big, "score": score, "parts": parts}


def links(cik):
    """{accession: URL of the human-readable Form 4} from the issuer's submissions (one tap to verify)."""
    rows = common.filings(common.submissions(int(cik)) or {"filings": {"recent": {}}})
    return {f["accessionNumber"]: common.doc_url(cik, f["accessionNumber"], f["primaryDocument"])
            for f in rows if f["form"] in ("4", "4/A") and f.get("primaryDocument")}


def row_line(b, seen, urls=None):
    """One purchase line of an alert, with direct EDGAR links (the Form 4 itself and its filing index)."""
    extra, pct, r = [], b.get("pct"), pay_ratio(b)
    if pct is not None:
        extra.append(f"{code(f'{pct * 100:.1f}%')} מהאחזקה")
    if r is not None:
        extra.append(f"{code(f'{r * 100:.0f}%')} מהשכר השנתי")
    if form4.symbolic(b):
        extra.append("סמלית")
    doc = (urls or {}).get(b["acc"])
    name = f'<a href="{esc(doc)}">{esc(b["insider"])}</a>' if doc else esc(b["insider"])
    index = f' · <a href="{esc(common.doc_url(b["cik"], b["acc"]))}">אינדקס</a>'
    return (f"• רכש {name} ({esc(b['role'])}) · {code(b['last'])} · {code(money(b['value']))}"
            f" @ {code(price(b['price']))}" + (f" · {' · '.join(extra)}" if extra else "") + index
            + (" 🆕" if b["acc"] not in seen else ""))


def enrich(cik, ticker, j, today):
    """Alert context (block 5): SIC, price at alert (live, else cached and marked), 30-day dollar liquidity, forensic
    scores and sector concentration -> (Hebrew lines, journal fields). A failing source leaves its field "חסר"."""
    sub = common.submissions(int(cik)) or {}
    sic = int(sub.get("sic") or 0) or None
    q = market.quote(ticker)
    try:
        scores = journal.scores_of(fundamentals.analyze(int(cik), ticker, sic))
    except Exception as e:  # a forensic-score failure must not block the alert
        print(f"scores {ticker}: {type(e).__name__} {e}")
        scores = journal.scores_of(None)
    f = {"sic": sic, "quote": q, "liquidity": market.liquidity(ticker), "scores": scores, "company": sub.get("name")}
    e = {"price": {k: q.get(k) for k in ("price", "asof", "source")}, "liquidity": f["liquidity"], "scores": scores,
         "sic": sic}
    return journal.context_lines(e, journal.concentration(j, sic, today)), f


def block(ev, seen, regime, info=(), urls=None, context=()):
    """One issuer's Hebrew alert lines."""
    bs, cluster, big = ev["open"], ev["cluster"], ev["big"]
    b0, total = bs[0], sum(b["value"] or 0 for b in bs)
    kind = " + ".join(x for x, on in (("אשכול רכישות", cluster), ("רכישה גדולה", big)) if on)
    why = ([f"{code(cluster[0])} נושאי משרה/דירקטורים שונים רכשו יחד {code(money(cluster[1]))}"] if cluster else []) + \
          ([f"רכישה בודדת בהיקף {code(money(big))}"] if big else [])
    parts = " · ".join(f"{PARTS[k]} {code(f'{v:+d}' if k == 'market' else v)}" for k, v in ev["parts"].items())
    score = f"{ev['score']}/100"
    lines = [f"🟢 <b>{kind}</b> · {code(b0['ticker'])} · {esc(b0['company'])}", "סיבה: " + " · ".join(why),
             f"איכות: {code(score)} ({parts})",
             f"סה״כ בשוק הפתוח ב־{code(WINDOW_DAYS)} יום: {code(len(bs))} דיווחי רכישה · "
             f"{code(len({b['insider_cik'] or b['insider'] for b in bs}))} רוכשים · {code(money(total))}"]
    for b in sorted(bs, key=lambda b: (b["acc"] not in seen, b["value"] or 0), reverse=True)[:MAX_LINES]:
        lines.append(row_line(b, seen, urls))
    if len(bs) > MAX_LINES:
        lines.append(f"ועוד {code(len(bs) - MAX_LINES)} דיווחים.")
    if ev["plan"]:
        lines.append(f"ℹ️ לא נספרו: {code(len(ev['plan']))} רכישות בתוכנית {code('10b5-1')} בסך"
                     f" {code(money(sum(b['plan_value'] or b['value'] or 0 for b in ev['plan'])))}")
    kinds = defaultdict(int)
    for i in info:
        for k, v in i["kinds"].items():
            kinds[k] += v
    if kinds:
        names = {"exercise": "מימוש אופציות", "grant": "הענקות", "other": "אחר"}
        lines.append("ℹ️ פעולות נוספות ב־Form 4 (לא נספרו): "
                     + " · ".join(f"{names[k]} {code(f'×{v}')}" for k, v in sorted(kinds.items())))
    lines += list(context)
    lines.append(market.regime_line(regime or {"tag": "unknown"}))
    url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={b0['cik']}&type=4&owner=include"
    lines.append(f'🔗 <a href="{esc(url)}">כל דיווחי Form 4 של החברה ב־EDGAR</a>')
    return "\n".join(lines)


def _by_cik(state):
    by = defaultdict(list)
    for b in state["buys"]:
        by[str(b["cik"])].append(migrate_row(b))
    return by


def snapshot(ev, today):
    """What an alert said, kept to detect later corrections."""
    b0 = ev["open"][0]
    return {"id": f"{today.isoformat()}-{b0['ticker']}", "date": today.isoformat(), "ticker": b0["ticker"],
            "total": round(sum(b["value"] or 0 for b in ev["open"]), 2), "cluster": bool(ev["cluster"]),
            "big": bool(ev["big"]), "accs": sorted({a for b in ev["uniq"] for a in [b["acc"], *b.get("joint_accs", [])]}),
            "acks": []}


def alerts(state, today, j=None):
    """Qualifying issuers with not-yet-alerted filings -> [[Hebrew message, {cik: accessions}, {cik: snapshot},
    {cik: journal entry}]] (entries are recorded only once their message is delivered)."""
    j = j if j is not None else {"alerts": []}
    by, info = _by_cik(state), defaultdict(list)
    for i in state.get("info", []):
        info[str(i["cik"])].append(i)
    blocks, hits, fpi, qualifying = [], {}, [], 0  # blocks: (text, {cik: accs}, {cik: snapshot}, {cik: entry})
    pending = []  # this run's entries: sector concentration also counts alerts earlier in the same message batch
    for cik, bs in sorted(by.items(), key=lambda kv: -sum(b["value"] or 0 for b in kv[1])):
        ev = evaluate(bs, state.get("regime"))
        seen = set(state["alerted"].get(cik, []))
        qualifying += bool(ev["cluster"] or ev["big"])
        if not (ev["cluster"] or ev["big"]) or all(b["acc"] in seen for b in bs):
            continue
        hits[cik] = [b["acc"] for b in bs]
        if foreign(int(cik)):  # "$" and USD thresholds may be wrong (e.g. Bradesco reports BRL prices): name only
            fpi.append((code(bs[0]["ticker"]), cik))
        else:
            snap = snapshot(ev, today)
            context, f = enrich(cik, snap["ticker"], {"alerts": j["alerts"] + pending}, today)
            entry = journal.entry_for(ev, snap, state.get("regime"), f["quote"], f["liquidity"], f["scores"], f["sic"],
                                      f["company"] or bs[0]["company"])
            pending.append(entry)
            blocks.append((block(ev, seen, state.get("regime"), info[cik], links(cik), context), {cik: hits[cik]},
                           {cik: snap}, {cik: entry}))
    if fpi:
        blocks.append((f"ℹ️ רכישות חדשות גם אצל מנפיקים זרים: {', '.join(t for t, _ in fpi)} — לא הוצגו, כי המחיר"
                       " בדיווח שלהם עשוי להיות במטבע מקומי ולא בדולר (אפשר לשלוח לי את הטיקר לדוח מלא).",
                       {c: hits[c] for _, c in fpi}, {}, {}))
    common.log("alerts", qualifying=qualifying, new=len(hits), foreign=len(fpi))
    print(f"alerts: {qualifying} qualifying issuer(s), {len(hits)} with new filings ({len(fpi)} foreign, names only)")
    msgs = []
    head = (f"🔔 <b>רכישות בעלי עניין בשוק הפתוח</b> · {code(today.isoformat())}", {}, {}, {})
    for blk, *extra in [head] + blocks:
        if msgs and len(msgs[-1][0]) + len(blk) < 3500:  # never split one issuer across two messages
            msgs[-1][0] += "\n\n" + blk
            for mine, new in zip(msgs[-1][1:], extra):
                mine.update(new)
        else:
            msgs.append([blk, *(dict(d) for d in extra)])
    return msgs if blocks else []


def corrections(state, today):
    """A Form 4/A that changed or withdrew purchases behind an alert already sent -> a correction message when the
    alert's total moves by more than 10% or it no longer meets the rules. -> [Hebrew messages]; snapshots updated."""
    by, out = _by_cik(state), []
    for cik, snap in state.get("sent", {}).items():
        bs = by.get(cik, [])
        news = sorted({b["amended_by"] for b in bs if b["acc"] in snap["accs"] and b.get("amended_by")}
                      | {r["by"] for a, r in state.get("removed", {}).items() if a in snap["accs"]})
        news = [a for a in news if a not in snap["acks"]]
        if not news:
            continue
        ev = evaluate(bs, state.get("regime")) if bs else {"open": [], "cluster": False, "big": False}
        total = round(sum(b["value"] or 0 for b in ev["open"]), 2)
        still = bool(ev["cluster"] or ev["big"])
        snap["acks"] += news
        if abs(total - snap["total"]) <= 0.10 * max(snap["total"], 1) and still == (snap["cluster"] or snap["big"]):
            continue
        out.append(f"✏️ <b>תיקון להתראה</b> {code(snap['id'])} על {code(snap['ticker'])} (נשלחה {code(snap['date'])})\n"
                   f"דיווח מתוקן ({code('Form 4/A')}) שינה את סך הרכישות בשוק הפתוח מ־{code(money(snap['total']))}"
                   f" ל־{code(money(total))}" + ("" if still else " · ההתראה כבר לא עומדת בכללי ההתראה")
                   + "\nהדיווחים המתוקנים: "
                   + " · ".join(f'<a href="{esc(common.doc_url(cik, a))}">{code(a)}</a>' for a in news))
        snap.update(total=total, cluster=bool(ev["cluster"]), big=bool(ev["big"]))
    return out


_reported = [False]  # a heartbeat (or failure alarm) already went out in this process


def _mark_reported():
    _reported[0] = True
    if common.env("HEARTBEAT_FILE"):  # lets the workflow's failure step know the scan already reported
        Path(common.env("HEARTBEAT_FILE")).write_text("sent", "utf-8")


def alarm(reason, state=None, path=None):
    """'❌ הסריקה נכשלה: <reason>' - silence is a failure too, so every failed run says so (best effort)."""
    try:
        common.send(f"❌ הסריקה נכשלה: {reason}")
        _mark_reported()
    finally:
        if state is not None and path is not None:
            state["heartbeat"] = {"at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "ok": False,
                                  "reason": re.sub(r"<[^>]+>", "", reason)[:200]}
            save(path, state)


def alarm_step(reason):
    """The workflow's `if: failure()` step: alarm unless the scan itself already reported (the commit/push step
    failing after a sent heartbeat still alarms)."""
    marker = common.env("HEARTBEAT_FILE")
    if marker and Path(marker).exists() and common.env("COMMIT_OUTCOME") != "failure":
        return print("failure already reported by the scan")
    link = common.env("RUN_URL")
    alarm(esc(reason) + (f' (<a href="{esc(link)}">הריצה ב־GitHub</a>)' if link else ""))


MAX_VERIFY = 80  # Form 4s re-downloaded by /verify (newest first)


def verify(ticker, today=None):
    """/verify TICKER: re-download the issuer's Form 4 / 4/A filings of the window straight from EDGAR, rebuild the
    purchase rows independently of the scan, score them, and compare with what the scan's state holds."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    t = ticker.strip().upper().lstrip("$").replace(".", "-")
    hit = common.tickers().get(t)
    if not hit:
        return f"לא מצאתי את הטיקר {code(t)} ברשימת החברות של SEC."
    cik, name = hit
    since, until = (today - dt.timedelta(WINDOW_DAYS)).isoformat(), today.isoformat()
    f4 = [f for f in common.filings(common.submissions(cik) or {"filings": {"recent": {}}})
          if f["form"] in ("4", "4/A") and f["filingDate"] >= since][:MAX_VERIFY]
    urls = [common.doc_url(cik, f["accessionNumber"], f["primaryDocument"].rsplit("/", 1)[-1]) for f in f4]
    with ThreadPoolExecutor(4) as pool:
        docs = list(pool.map(_get, urls))
    buys = {}
    for f, doc in sorted(zip(f4, docs), key=lambda x: (x[0]["filingDate"], x[0]["accessionNumber"])):
        if not doc or doc["issuer_cik"] != cik:  # the issuer's CIK can also appear as a reporting owner
            continue
        own = [b for b in doc["buys"] if since <= b["date"] <= until]
        row = make_row(f["accessionNumber"], doc, own, t, f["filingDate"]) if own else None
        if doc["form"] == "4/A" and amend(buys, f["accessionNumber"], doc, row):
            continue
        if row:
            buys[row["acc"]] = row
    st = load(Path(common.env("STATE_FILE", str(common.DATA / "state.json"))))
    errors = sum(d is False for d in docs)
    out = [f"🔎 <b>אימות מחדש מול EDGAR</b> · {code(t)} · {esc(name)}",
           f"הורדו עכשיו {code(len(f4))} דיווחי {code('Form 4')} מ־{code(WINDOW_DAYS)} הימים האחרונים"
           + (f" ({code(errors)} נכשלו)" if errors else "") + "."]
    if not buys:
        return "\n".join(out + ["לא נמצאו רכישות של בעלי עניין בחלון הזה."])
    ev = evaluate(list(buys.values()), st.get("regime"))
    kind = " + ".join(x for x, on in (("אשכול רכישות", ev["cluster"]), ("רכישה גדולה", ev["big"])) if on)
    op = ev["open"]
    out += [f"תוצאה: {'<b>' + kind + '</b>' if kind else 'לא עומד בכללי ההתראה'} · איכות {code(str(ev['score']) + '/100')}",
            f"בשוק הפתוח: {code(len(op))} דיווחי רכישה · {code(len({b['insider_cik'] or b['insider'] for b in op}))}"
            f" רוכשים · {code(money(sum(b['value'] or 0 for b in op)))}"
            + (f" · {code(len(ev['plan']))} בתוכנית {code('10b5-1')} (לא נספרו)" if ev["plan"] else "")]
    seen, urls_ = set(st["alerted"].get(str(cik), [])), links(cik)
    for b in sorted(op + ev["plan"], key=lambda b: b["value"] or 0, reverse=True)[:MAX_LINES]:
        out.append(row_line(b, seen, urls_))
    # both sides deduplicated the same way (joint filings = one purchase), then compared by accession and total
    mine = [b for b in st["buys"] if str(b["cik"]) == str(cik)]
    old = evaluate(mine, st.get("regime"))["open"] if mine else []
    accs = lambda rows: {a for b in rows for a in [b["acc"], *b.get("joint_accs", [])]}
    total = lambda rows: sum(b["value"] or 0 for b in rows)
    only_new, only_old = accs(op) - accs(old), accs(old) - accs(op)
    if not old:
        out.append("השוואה לסריקה: אין לה רכישות בשוק הפתוח של החברה בזיכרון"
                   + (" (הדיווחים חדשים מדי, או שהסריקה עוד לא הגיעה ליום שלהם)." if op else "."))
    elif not (only_new or only_old) and abs(total(op) - total(old)) <= 1:
        out.append(f"השוואה לסריקה: ✅ תואם ({code(len(old))} דיווחים, {code(money(total(old)))})")
    else:
        out.append("השוואה לסריקה: ⚠ יש הבדלים"
                   + (f" · חדשים שהסריקה עוד לא ראתה: {code(len(only_new))}" if only_new else "")
                   + (f" · רק בזיכרון הסריקה: {code(len(only_old))}" if only_old else "")
                   + f" · סכום עכשיו {code(money(total(op)))} מול {code(money(total(old)))} בסריקה")
    out.append(f"התראה נשלחה על החברה: {'כן' if seen else 'לא'}")
    return "\n".join(out)


def previous_trading_day(today):
    """The latest weekday before today whose daily index SEC lists (holidays are skipped); None if unknown."""
    for i in range(1, 8):
        d = today - dt.timedelta(i)
        if d.weekday() < 5:
            names = published(d.year, (d.month + 2) // 3)
            if names is None or f"form.{d:%Y%m%d}.idx" in names:
                return d.strftime("%Y%m%d")
    return None


def watchdog(today=None):
    """Missed-day watchdog (its own daily schedule): if the last scanned trading day is older than the previous
    trading day, say so and retry the scan (dispatch the daily-scan workflow on Actions, run it here locally)."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    st = load(Path(common.env("STATE_FILE", str(common.DATA / "state.json"))))
    prev, last, gap = missed(st, today)
    common.log("watchdog", previous_trading_day=prev, last_scanned=last, missed=gap)
    common.summary(f"### Watchdog {today}\n- previous trading day: {prev}\n- last scanned: {last or '-'}\n"
                   f"- missed: {', '.join(gap) or 'none'}")
    if not gap:
        return print(f"watchdog: ok (last scanned {last}, previous trading day {prev})")
    common.send(f"⚠️ לא סרקתי את {', '.join(code(_iso(d)) for d in gap)} — מריץ את הסריקה שוב.")
    if common.env("GITHUB_ACTIONS"):
        err = common.dispatch("daily-scan.yml", {"notify": "true"})
        if err:
            common.send(f"⚠️ לא הצלחתי להפעיל את הסריקה מחדש ({code(err)}). בדקו את ההרשאה {code('actions: write')}"
                        f" בקובץ {code('watchdog.yml')}.")
            sys.exit(f"watchdog could not dispatch the scan: {err}")
    else:
        main([])


def run(a):
    """One scan: new days + a rescan of the last RESCAN scanned days, corrections, alerts, heartbeat."""
    t0, today = time.time(), dt.datetime.now(dt.timezone.utc).date()
    path = Path(common.env("STATE_FILE", str(common.DATA / "state.json")))
    state = prune(load(path), today)
    market.CACHE.update(state["prices"])
    days = a.days.split(",") if a.days else pick(state, today)
    ok = sorted(d for d, v in state["days"].items() if v == "ok")
    rescans = [] if a.days else [d for d in ok[-RESCAN:] if d not in days]
    legacy = sorted({b["filed"].replace("-", "") for b in state["buys"] if "sig" not in b} & set(ok) - set(days + rescans))
    rescans += [] if a.days else legacy[-LEGACY:]  # one-time upgrade, newest first, a few days per run
    print(f"scan {today}: {len(days)} day(s) {' '.join(days)}; rescan {' '.join(rescans) or '-'}; state {path}")
    stats, failed = {"days": []}, []
    for day in days + rescans:
        try:
            res = scan_day(day, state, today, stats, rescan=day in rescans)
        except Exception as e:  # e.g. SEC outage: this day is retried next run and must not block the later days
            traceback.print_exc()
            common.log("day_failed", day=day, error=f"{type(e).__name__}: {e}")
            failed.append(day)
            continue
        if res and day not in rescans:
            state["days"][day] = res
        if res == "ok" and day in legacy:  # re-read: rows it did not rebuild are marked, never re-read forever
            for b in state["buys"]:
                if "sig" not in b and b["filed"].replace("-", "") == day:
                    b["sig"], b["indirect"] = None, False
        if not a.dry:
            save(path, prune(state, today))  # after every day: crash-safe
    state["regime"] = market.regime()
    j = journal.load()
    fixes = corrections(state, today)
    for text in fixes:
        common.send(text, signal=True)
    n = 0
    for text, marks, snaps, entries in alerts(state, today, j):
        common.send(text, signal=True)
        state["alerted"].update(marks)  # per delivered message: a later failure never re-sends this one
        state["sent"].update(snaps)
        for cik, e in entries.items():  # 5.1: the journal gets every delivered alert (unique id)
            state["sent"][cik]["id"] = journal.record(j, e)["id"]
        n += len(marks)
        if not a.dry:
            save(path, state)
            journal.save(j)
    state["prices"] = {t: v for t, v in market.CACHE.items()}
    filings = sum(r["parsed"] for r in stats["days"])
    errors = sum(r["errors"] for r in stats["days"]) + len(failed)
    sources = sorted({r["source"] for r in stats["days"]})
    state["heartbeat"] = {"at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "ok": not failed,
                          "filings": filings, "errors": errors, "alerts": n, "corrections": len(fixes),
                          "days": days, "rescans": rescans, "failed": failed, "sources": sources,
                          "seconds": round(time.time() - t0)}
    if not a.dry:
        save(path, prune(state, today))
    common.summary("\n".join([f"### Insider scan {today}", "", "| day | filings | parsed | errors | purchases | "
                              "amendments | source | s |", "|---|---|---|---|---|---|---|---|"]
                             + [f"| {r['day']}{' (rescan)' if r['rescan'] else ''} | {r['filings']} | {r['parsed']} | "
                                f"{r['errors']} | {r['purchases']} | {r['amendments']} | {r['source']} | {r['seconds']} |"
                                for r in stats["days"]]
                             + ["", f"alerts: {n} · corrections: {len(fixes)} · failed days: {', '.join(failed) or 'none'}"
                                f" · regime: {state['regime']['tag']} · sources: {', '.join(sources) or '-'}"
                                f" · {state['heartbeat']['seconds']}s"]))
    common.log("done", alerts=n, corrections=len(fixes), filings=filings, errors=errors, failed=failed,
               seconds=state["heartbeat"]["seconds"])
    print(f"done: {n} issuer alert(s), {len(state['buys'])} purchase filings in state, {time.time() - t0:.0f}s")
    if failed:  # keep the run red so a persistent problem is visible - and say so
        alarm(f"לא הצלחתי לסרוק את {', '.join(code(_iso(d)) for d in failed)} ({code(errors)} שגיאות) — אנסה שוב"
              " בריצה הבאה.")
        sys.exit(f"days that failed and will be retried: {' '.join(failed)}")
    via = " · מקור: חיפוש טקסט מלא (האינדקס היומי לא היה זמין)" if "efts" in sources else ""
    common.send(f"✅ סריקה {code(today.isoformat())}: {code(filings)} הגשות, {code(errors)} שגיאות, "
                + (f"{code(n)} התראות חדשות" if n else "אין התראות חדשות")
                + (f" · {code(len(fixes))} תיקונים" if fixes else "") + via)
    _mark_reported()


def followup(today=None, dry=False):
    """Weekly (5.2): the 30/90/180-day return of every alert against SPY into the journal, then the summary
    (hit rate and excess return by horizon, market regime and quality bucket) to Telegram."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    j = journal.load()
    n = journal.followup(j, today)
    if not dry:
        journal.save(j)
    common.log("followup", measured=n, alerts=len(j["alerts"]))
    common.summary(f"### Journal follow-up {today}\n- alerts: {len(j['alerts'])}\n- new return measurements: {n}")
    common.send(journal.stats_text(j, "📊 <b>סיכום שבועי של יומן ההתראות</b>")
                + f"\nמדידות תשואה חדשות השבוע: {code(n)}")
    _mark_reported()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Daily EDGAR Form 4 insider-cluster scan")
    ap.add_argument("--days", help="force specific days: YYYYMMDD[,YYYYMMDD...]")
    ap.add_argument("--dry", action="store_true", help="do not write the state file")
    ap.add_argument("--notify", action="store_true", help="kept for compatibility: every run now sends a heartbeat")
    ap.add_argument("--watchdog", action="store_true", help="missed-day check (retries the scan if a day was missed)")
    ap.add_argument("--alarm", metavar="REASON", help="workflow failure step: report unless already reported")
    ap.add_argument("--followup", action="store_true", help="weekly: journal returns vs SPY + summary (also "
                                                               "env SCAN_MODE=followup)")
    a = ap.parse_args(argv)
    if a.alarm is not None:
        return alarm_step(a.alarm)
    if a.watchdog:
        return watchdog()
    try:
        followup(dry=a.dry) if a.followup or common.env("SCAN_MODE") == "followup" else run(a)
    except BaseException as e:  # heartbeat: a failed run is never silent
        if isinstance(e, KeyboardInterrupt) or (isinstance(e, SystemExit) and (not e.code or _reported[0])):
            raise
        reason = common.failure(e) if isinstance(e, SystemExit) else f"{code(type(e).__name__)}: {esc(str(e)[:150])}"
        try:
            alarm(reason)
        except BaseException:
            traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
