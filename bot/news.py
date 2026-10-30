"""News / catalyst tag for a long ticket: one Hebrew line, information only (never a filter, never blocks a ticket).

Free sources, no keys: Yahoo Finance headline RSS (per symbol), the Nasdaq earnings calendar (one call per date for
the next EARNINGS_DAYS days, shared by every symbol) and, only when SEC_UA is set, SEC EDGAR 8-K / 6-K filings.
Every request has a short timeout and one try; any error -> "אין חדשות משמעותיות". Results are cached per symbol per
day in the process (a scan loop asks again for free) and the calendar once per day."""
import datetime as dt
import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from bot import common

TIMEOUT = float(common.env("NEWS_TIMEOUT", "3"))  # seconds per request
BUDGET = float(common.env("NEWS_BUDGET", "6"))  # seconds for one symbol's whole lookup
NEWS_DAYS, EARNINGS_DAYS = 7, 14
NONE = "אין חדשות משמעותיות"
MATERIAL = re.compile(r"\b(FDA|approv|clearance|certif|partner|agreement|deal|acqui|merger|contract|award|"
                      r"upgrade|downgrade|price target|raises|guidance|beats?|record|launch|8-K|6-K|offering)", re.I)
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
_cache, _cal = {}, {}


def _get(url, headers=UA):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=TIMEOUT) as r:
        return r.read()


def headlines(symbol, today):
    """Yahoo RSS -> [(date, title, source)] material and within NEWS_DAYS, newest first."""
    root = ET.fromstring(_get(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"))
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        try:
            d = parsedate_to_datetime(it.findtext("pubDate")).date()
        except (TypeError, ValueError):
            continue
        if title and (today - d).days <= NEWS_DAYS and MATERIAL.search(title):
            out.append((d, title, "Yahoo Finance"))
    return sorted(out, reverse=True)


def filings(symbol, today):
    """EDGAR 8-K / 6-K within NEWS_DAYS -> [(date, title, 'SEC EDGAR')]; skipped (no request) without SEC_UA."""
    ua = common.env("SEC_UA")
    if not ua:
        return []
    m = json.loads(_get("https://www.sec.gov/files/company_tickers.json", {"User-Agent": ua}))
    cik = next((v["cik_str"] for v in m.values() if v["ticker"] == symbol), None)
    if not cik:
        return []
    r = json.loads(_get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json", {"User-Agent": ua}))["filings"]["recent"]
    out = []
    for form, d, items in zip(r["form"], r["filingDate"], r.get("items", [""] * len(r["form"]))):
        d = dt.date.fromisoformat(d)
        if form in ("8-K", "6-K") and (today - d).days <= NEWS_DAYS:
            out.append((d, f"דיווח {form}" + (f" (סעיף {items})" if items else ""), "SEC EDGAR"))
    return out


def earnings_in(symbol, today, deadline=float("inf")):
    """Days until the next earnings date within EARNINGS_DAYS (Nasdaq calendar), else None."""
    for k in range(EARNINGS_DAYS + 1):
        d = today + dt.timedelta(days=k)
        if d.weekday() >= 5:
            continue
        if d not in _cal:
            if time.monotonic() > deadline:
                return None  # out of budget: no earnings tag rather than a late ticket
            try:
                rows = (json.loads(_get(f"https://api.nasdaq.com/api/calendar/earnings?date={d}"))
                        .get("data") or {}).get("rows") or []
                _cal[d] = {r.get("symbol") for r in rows}
            except Exception:  # one missing date must not cost the others
                _cal[d] = set()
        if symbol in _cal[d]:
            return k
    return None


def _fmt(d):
    return d.strftime("%d/%m")


def tag(symbol, today=None):
    """One Hebrew line for the ticket. Never raises."""
    if common.env("NEWS_TAG", "1") == "0":
        return NONE
    today = today or dt.date.today()
    deadline = time.monotonic() + BUDGET
    key = (symbol, today)
    if key in _cache:
        return _cache[key]
    parts = []
    try:
        items = sorted(filings(symbol, today) + headlines(symbol, today), reverse=True)
    except Exception as e:
        common.log("news_error", symbol=symbol, error=str(e)[:120])
        items = []
    if items:
        d, title, src = items[0]
        parts.append(f"חדשות: {title[:110]} ({src}, {_fmt(d)})")
    try:
        k = earnings_in(symbol, today, deadline)
    except Exception:
        k = None
    if k is not None:
        parts.append("קטליזטור: דוחות היום" if k == 0 else f"קטליזטור: דוחות בעוד {k} ימים")
    line = " · ".join(parts) or NONE
    _cache[key] = line
    return line
