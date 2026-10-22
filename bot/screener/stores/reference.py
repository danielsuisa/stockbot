"""Reference data: the screening universe, the trading calendar and session clock, and per-company reference facts
(SIC, country, earnings 8-Ks) from SEC submissions.

Universe = Nasdaq Trader symbol directory (nasdaqlisted + otherlisted; test issues, preferreds, warrants, units,
rights and notes dropped) joined with Alpaca's asset list (shortable, has_options; also the fallback universe when the
directory cannot be read), OCC's equity option underlyings and SEC's ticker map (CIK). Symbols use Alpaca's spelling
(BRK.B); SEC's map spells it BRK-B."""
import datetime as dt
import re

from bot import alpaca, common
from bot.screener import geo, sic, stores

NY = alpaca.NY
NT = "https://www.nasdaqtrader.com/dynamic/SymDir/{}.txt"
OCC = "https://marketdata.theocc.com/delo-download?prodType=EU&downloadFields=OS;US;SN;EXCH&format=txt"
EXCH = {"A": "amex", "N": "nyse", "P": "arca", "Z": "cboe", "V": "iex"}  # otherlisted "Exchange" (OBSERVED layout)
ALPACA_EXCH = {"NASDAQ": "nasd", "NYSE": "nyse", "AMEX": "amex", "ARCA": "arca", "BATS": "cboe", "IEX": "iex"}
PLAIN = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")  # common symbols; class shares as BRK.B
NOT_COMMON = re.compile(r"(?i)\b(warrants?|units?|rights?|preferred|preference|notes? due|debentures?|subordinated|"
                        r"depositary shares?,? each representing|perpetual|cumulative|% )")
ADS = re.compile(r"(?i)american depositary|depositary (shares|receipts?)$|\bADS\b|\bADR\b")
REF_PER_RUN = 400  # submissions refreshed per run (rolling, oldest first), plus every company filing an 8-K
REF_MAX_DAYS = 30
EARNINGS_KEEP = 12  # Item 2.02 8-Ks kept per company


# ---------- universe ----------
def _directory(name):
    body = common.fetch(NT.format(name), tries=3, timeout=60)
    stores.count("nt")
    text = (body or b"").decode("latin-1")
    if "|" not in text.split("\n", 1)[0]:  # an HTML challenge page or empty: unusable
        raise RuntimeError(f"nasdaqtrader {name}: no directory")
    rows = [line.split("|") for line in text.splitlines()]
    head = rows[0]
    return [dict(zip(head, r)) for r in rows[1:] if len(r) == len(head) and not r[0].startswith("File Creation")]


def common_stock(symbol, name):
    """Is this listing a common stock / ADR / ETF (what finviz lists), not a preferred, warrant, unit, right or
    note? (INFERRED from the directories' symbol and name conventions.)"""
    if not PLAIN.match(symbol):
        return False
    if NOT_COMMON.search(name) and not ADS.search(name.split(" - ")[0]):
        return False
    return not (len(symbol) == 5 and symbol[-1] in "WUR" and re.search(r"(?i)warrant|unit|right", name))


def parse_directories(nasdaq, other):
    """Directory rows -> {symbol: {"name", "exch", "etf"}} (test issues and non-common listings dropped)."""
    out = {}
    for r in nasdaq:
        s, name = r.get("Symbol", "").strip(), r.get("Security Name", "")
        if r.get("Test Issue") == "Y" or not common_stock(s, name):
            continue
        out[s] = {"name": name, "exch": "nasd", "etf": r.get("ETF") == "Y"}
    for r in other:
        s, name = r.get("ACT Symbol", "").strip(), r.get("Security Name", "")
        if r.get("Test Issue") == "Y" or not common_stock(s, name):
            continue
        code = r.get("Exchange", "")
        out.setdefault(s, {"name": name, "exch": EXCH.get(code, code.lower()), "etf": r.get("ETF") == "Y"})
    return out


def parse_occ(text):
    """OCC listed-products text -> set of underlying symbols (Alpaca spelling)."""
    out = set()
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) >= 2 and parts[1] and parts[1][0].isalpha():
            out.add(parts[1].replace("/", ".").upper())
    return out


def _alpaca_assets():
    rows = alpaca._get(alpaca.ASSETS, {"status": "active", "asset_class": "us_equity"})
    stores.count("alpaca")
    return rows


def build_universe():
    """Fetch and join every universe source -> the "universe" store's data {symbol: row}; also returns notes
    (sources that failed). Row: name, exch, etf, cik, optionable, shortable, etb, source."""
    notes = []
    try:
        listed = parse_directories(_directory("nasdaqlisted"), _directory("otherlisted"))
        src = "nasdaqtrader"
    except Exception as e:  # the directory refuses a runner -> Alpaca's asset list alone (no ETF flag)
        common.trace()
        notes.append(f"nasdaqtrader: {type(e).__name__}")
        listed, src = {}, "alpaca"
    assets = {a["symbol"]: a for a in _alpaca_assets()}
    if src == "alpaca":
        for s, a in assets.items():
            if a.get("tradable") and a.get("exchange") in ALPACA_EXCH and common_stock(s, a.get("name") or ""):
                listed[s] = {"name": a.get("name") or "", "exch": ALPACA_EXCH[a["exchange"]],
                             "etf": bool(re.search(r"(?i)\bETF\b|\bfund\b", a.get("name") or ""))}
    try:
        occ = parse_occ((common.fetch(OCC, tries=3, timeout=90) or b"").decode("latin-1"))
        stores.count("occ")
    except Exception as e:
        common.trace()
        notes.append(f"occ: {type(e).__name__}")
        occ = set()
    ciks = common.tickers()
    stores.count("sec")
    out = {}
    for s, r in listed.items():
        a = assets.get(s) or {}
        attrs = a.get("attributes") or []
        cik = (ciks.get(s.replace(".", "-")) or (None,))[0]
        out[s] = {**r, "cik": cik, "optionable": bool("has_options" in attrs or s in occ),
                  "shortable": a.get("shortable") if a else None, "etb": a.get("easy_to_borrow") if a else None,
                  "alpaca": bool(a)}
    return out, src, notes


def universe(refresh=False):
    """The universe store {symbol: row}, rebuilt when missing, stale or asked (refresh) -> (data, meta)."""
    st = stores.load("universe")
    if refresh or st is None or stores.stale("universe", st["meta"]):
        data, src, notes = build_universe()
        meta = stores.save("universe", data, src, notes=notes)
        return data, meta
    return st["data"], st["meta"]


# ---------- calendar and clock ----------
def calendar(start, end):
    """Trading sessions over [start, end] -> sorted [(date ISO, open "HH:MM", close "HH:MM")] from Alpaca
    (weekdays 09:30-16:00 when Alpaca cannot be read; holidays then count as sessions - noted by the caller)."""
    try:
        rows = alpaca._get(alpaca.CALENDAR, {"start": start, "end": end})
        stores.count("alpaca")
        return [(r["date"], r["open"], r["close"]) for r in rows], "alpaca"
    except Exception:
        common.trace()
        d, out = dt.date.fromisoformat(start), []
        while d <= dt.date.fromisoformat(end):
            if d.weekday() < 5:
                out.append((d.isoformat(), "09:30", "16:00"))
            d += dt.timedelta(1)
        return out, "weekdays"


def sessions(today, back_days=4000, ahead_days=30, refresh=False):
    """The calendar store: every session from ~11 years back to 30 days ahead -> (rows, meta); refreshed daily."""
    st = stores.load("calendar")
    if not refresh and st and st["meta"].get("day") == today:
        return [tuple(r) for r in st["data"]], st["meta"]
    d = dt.date.fromisoformat(today)
    rows, src = calendar((d - dt.timedelta(back_days)).isoformat(), (d + dt.timedelta(ahead_days)).isoformat())
    meta = stores.save("calendar", rows, src, day=today)
    return rows, meta


def clock(now, cal):
    """New York time `now` (aware) + calendar rows -> {"now", "day", "session" (PRE|RTH|POST|CLOSED), "prev" (the
    last session completed before today's, or before now when CLOSED after the close), "open", "close", "trading"
    (today is a session)}. PRE 04:00-open, RTH open-close, POST close-20:00 (D-005)."""
    now = now.astimezone(NY)
    day = now.date().isoformat()
    by = {r[0]: r for r in cal}
    hhmm = now.strftime("%H:%M")
    past = [r[0] for r in cal if r[0] < day]
    out = {"now": now, "day": day, "trading": day in by, "open": None, "close": None,
           "prev": past[-1] if past else None}
    if day in by:
        _, o, c = by[day]
        out.update(open=o, close=c)
        if "04:00" <= hhmm < o:
            out["session"] = "PRE"
        elif o <= hhmm < c:
            out["session"] = "RTH"
        elif c <= hhmm < "20:00":
            out["session"] = "POST"
        else:
            out["session"] = "CLOSED"
            if hhmm >= "20:16":
                out["prev"] = day  # today's session is complete and its last trades are 15+ minutes old
    else:
        out["session"] = "CLOSED"
    return out


def completed(cal, ck):
    """Sessions whose daily bar is final at clock `ck` (ascending ISO list): up to ck["prev"]."""
    return [r[0] for r in cal if ck["prev"] and r[0] <= ck["prev"]]


# ---------- per-company reference (SEC submissions) ----------
def accepted_ny(stamp):
    """SEC submissions "acceptanceDateTime" -> naive New York datetime. The field is labelled UTC ("Z") but equals New
    York time plus twice New York's UTC offset (OBSERVED 2026-10-21 against EDGAR's filing index pages: AAPL's
    2024-10-31 8-K "2024-11-01T00:30:25Z" was accepted 16:30:25, its 2025-01-30 8-K "02:30:23Z" at 16:30:23; D-035).
    A plain date (no time) -> midnight. None for empty input."""
    if not stamp:
        return None
    if len(stamp) <= 10:
        return dt.datetime.fromisoformat(stamp[:10])
    t = dt.datetime.fromisoformat(stamp[:19])
    guess = (t - dt.timedelta(hours=9)).replace(tzinfo=NY)
    off = -guess.utcoffset()  # 4 h (EDT) or 5 h (EST)
    return t - 2 * off
def _ref_row(sub):
    """SEC submissions JSON -> compact reference row."""
    biz = (sub.get("addresses") or {})
    row = {"sic": sub.get("sic") or None, "name": sub.get("name") or "",
           "country": geo.country(biz, sub.get("stateOfIncorporation")),
           "inc": sub.get("stateOfIncorporation") or None, "fy_end": sub.get("fiscalYearEnd") or None}
    sec_, ind = sic.classify(row["sic"], row["name"])
    row.update(sector=sec_, industry=ind)
    earn = []
    for f in common.filings(sub):
        if f.get("form") in ("8-K", "8-K/A") and "2.02" in (f.get("items") or "").split(","):
            earn.append(f.get("acceptanceDateTime") or f.get("filingDate"))
    row["earnings"] = sorted(e for e in earn if e)[-EARNINGS_KEEP:]
    return row


def eightk_ciks(day):
    """CIKs with an 8-K in EDGAR's daily form index of `day` (ISO) -> set ([] when not published)."""
    d = dt.date.fromisoformat(day)
    body = common.fetch(f"https://www.sec.gov/Archives/edgar/daily-index/{d.year}/QTR{(d.month + 2) // 3}/"
                        f"form.{d:%Y%m%d}.idx")
    stores.count("sec")
    out = set()
    for line in (body or b"").decode("latin-1").splitlines():
        r = line.split()
        if len(r) >= 5 and r[0] in ("8-K", "8-K/A") and r[-3].isdigit():
            out.add(int(r[-3]))
    return out


def reference(ciks, today, per_run=REF_PER_RUN, priority=(), refresh=True):
    """The reference store {cik (str): row}: refresh `priority` CIKs (8-K filers) plus the per_run oldest/missing
    ones among `ciks` -> (data, meta, refreshed count)."""
    st = stores.load("reference")
    data = st["data"] if st else {}
    if not refresh:
        return data, st["meta"] if st else None, 0
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(REF_MAX_DAYS)).isoformat()
    want = [str(c) for c in dict.fromkeys(priority) if c]
    old = sorted((data.get(str(c), {}).get("t", ""), str(c)) for c in ciks if c)
    want += [c for t, c in old if t < cutoff and c not in want][:per_run]
    done = 0
    for c in want:
        try:
            sub = common.submissions(int(c))
            stores.count("sec")
        except Exception:
            common.trace()
            continue
        if sub:
            data[c] = {**_ref_row(sub), "t": today}
            done += 1
    common.submissions.cache_clear()  # big JSONs: do not keep them in memory
    meta = stores.save("reference", data, "sec-submissions", refreshed=done) if done or st is None else st["meta"]
    return data, meta, done
