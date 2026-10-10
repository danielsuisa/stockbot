"""Phase A readers for the recommendation algorithm (docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md
section 7 A; plan docs/superpowers/plans/2026-10-10-recommendation-algorithm-phase-a.md). Measurement only: nothing
here computes a return of a ticket or a stock.

- SEC Insider Transactions Data Sets: the symbols on Form 3/4/5 filings (the universe's insider test, CIK -> symbol)
  and S1's open-market purchases.
- EDGAR submissions.zip: 8-K item 2.02 acceptance times and the reaction session.
- Alpaca daily bars, raw and split-adjusted, cut into segments by the spec's bad-price rule."""
import bisect
import collections
import datetime as dt
import hashlib
import json
import os
import re
import urllib.request
import zipfile

from bot import alpaca, common, form4, probe_insider as pi

FORMS = {"3", "4", "5", "3/A", "4/A", "5/A"}
_SYMBOL = re.compile(r"[A-Z]{1,5}(\.[A-Z])?")


# ---------- insider data sets ----------
def normalise_symbol(raw):
    """A trading symbol as filed -> Alpaca's spelling ("brk-b" -> "BRK.B"; the first of a list), or None."""
    s = (raw or "").strip().upper()
    s = re.split(r"[,;\s]+", s)[0] if s else ""
    s = s.replace("-", ".").replace("/", ".")
    return s if _SYMBOL.fullmatch(s) and s not in ("NONE", "NA", "N.A") else None


def symbol_filings(z):
    """Every Form 3/4/5 (and amendment) of a quarter's zip -> [(filing date ISO, issuer CIK, symbol)]; filings whose
    symbol is unusable are left out (counted by the caller from the difference)."""
    _, rows = pi.table(z, "SUBMISSION")
    out = []
    for r in rows:
        if (r.get("DOCUMENT_TYPE") or "").strip() not in FORMS:
            continue
        sym, filed = normalise_symbol(r.get("ISSUERTRADINGSYMBOL")), pi.day(r.get("FILING_DATE"))
        if sym and filed:
            out.append((filed, (r.get("ISSUERCIK") or "").strip(), sym))
    return out


def index(rows):
    """symbol_filings rows -> ({symbol: sorted filing dates}, {symbol: {issuer CIKs}})."""
    dates, ciks = collections.defaultdict(list), collections.defaultdict(set)
    for filed, cik, sym in rows:
        dates[sym].append(filed)
        ciks[sym].add(cik)
    for v in dates.values():
        v.sort()
    return dict(dates), dict(ciks)


def _cik(s):
    return str(int(s)) if str(s).strip().isdigit() else str(s).strip()


# ---------- EDGAR submissions.zip ----------
def download(url, path):
    """Stream a large SEC file to disk (SEC_UA, one request) unless it is already there -> path."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": common.env("SEC_UA")})
        tmp = path.with_suffix(".part")
        with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        os.replace(tmp, path)
    return path


def _to_ny(stamp, tz):
    """EDGAR's acceptanceDateTime ("2024-02-01T21:30:00.000Z") -> New York "YYYY-MM-DDTHH:MM"; tz says how to read
    the field (decided by the live check of the plan's Task 6)."""
    t = dt.datetime.fromisoformat(stamp[:19])
    if tz == "UTC":
        t = t.replace(tzinfo=dt.timezone.utc).astimezone(alpaca.NY)
    return t.strftime("%Y-%m-%dT%H:%M")


def earnings_releases(zip_path, ciks, tz):
    """8-K / 8-K/A filings whose items include 2.02, for the given issuer CIKs -> {CIK: sorted New York acceptance
    times}, from each issuer's recent filings and every older page in submissions.zip (read in place)."""
    want = {_cik(c) for c in ciks}
    out = collections.defaultdict(list)
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            m = re.fullmatch(r"CIK(\d{10})(-submissions-\d+)?\.json", name.rsplit("/", 1)[-1])
            if not m or _cik(m.group(1)) not in want:
                continue
            d = json.loads(z.read(name))
            cols = d if m.group(2) else (d.get("filings") or {}).get("recent") or {}
            for form, items, acc in zip(cols.get("form", []), cols.get("items", []), cols.get("acceptanceDateTime", [])):
                if form in ("8-K", "8-K/A") and "2.02" in re.split(r"[,\s]+", items or "") and acc:
                    out[_cik(m.group(1))].append(_to_ny(acc, tz))
    return {c: sorted(v) for c, v in out.items()}


def reaction_session(accepted, sessions):
    """New York acceptance time -> the reaction session: the acceptance day when it is a session and the time is
    before 16:00, else the next session; None past the last session."""
    d, hhmm = accepted[:10], accepted[11:16]
    if hhmm < "16:00":
        i = bisect.bisect_left(sessions, d)
    else:
        i = bisect.bisect_right(sessions, d)
    return sessions[i] if i < len(sessions) else None


# ---------- daily bars ----------
BREAK_RATIO, BREAK_GAP = 4.0, 10  # spec section 2 (the ratio on the split-adjusted closes: owner, 2026-10-10)


def split_bars(symbols, start, end):
    """Split-adjusted daily bars -> {symbol: {date: (open, close, volume)}}, cached per chunk under a hash of the
    chunk's symbols (so a different symbol list never reads another chunk's file)."""
    out = {}
    for i in range(0, len(symbols), alpaca.CHUNK):
        part = symbols[i:i + alpaca.CHUNK]
        key = hashlib.sha1(",".join(part).encode()).hexdigest()[:16]
        path = alpaca.CACHE / "1d_split" / f"{start}_{end}_{key}.json.gz"
        got = alpaca._read(path)
        if got is None:
            got, token = {}, None
            while True:
                q = {"symbols": ",".join(part), "timeframe": "1Day", "start": start, "end": end, "feed": "sip",
                     "adjustment": "split", "limit": alpaca.LIMIT, **({"page_token": token} if token else {})}
                js = alpaca._get(f"{alpaca.DATA}/bars", q)
                for s, items in (js.get("bars") or {}).items():
                    got.setdefault(s, []).extend([b["t"][:10], b["o"], b["c"], b["v"]] for b in items or [])
                token = js.get("next_page_token")
                if not token:
                    break
            alpaca._write(path, got)
        for s, rows in got.items():
            out[s] = {d: (o, c, v) for d, o, c, v in rows}
    return out


def segments(dates, close, sessions):
    """The bad-price rule -> a segment id per bar: a new segment after a split-adjusted close ratio close / previous
    close >= 4 (a fall is kept), or after more than 10 sessions without a bar."""
    out, seg = [], 0
    for i, d in enumerate(dates):
        if i:
            gap = bisect.bisect_left(sessions, d) - bisect.bisect_left(sessions, dates[i - 1]) - 1
            if gap > BREAK_GAP or (close[i - 1] and close[i] / close[i - 1] >= BREAK_RATIO - 1e-9):
                seg += 1
        out.append(seg)
    return out


def earlier_in_segment(segment, i):
    """Bars before index i in the same segment as bar i."""
    return i - segment.index(segment[i])


def load_series(symbols, start, end, sessions):
    """Raw and split-adjusted daily bars joined by date (a date missing from either feed is dropped) -> {symbol:
    {"dates", "raw_close", "raw_high", "raw_low", "raw_volume", "close", "volume", "segment"}}."""
    raw, adj = {}, {}
    for i in range(0, len(symbols), alpaca.CHUNK):
        part = symbols[i:i + alpaca.CHUNK]
        raw.update(alpaca.daily(part, start, end))
    adj = split_bars(symbols, start, end)
    out = {}
    for s in symbols:
        a = adj.get(s) or {}
        rows = [(b, a[b[0]]) for b in raw.get(s) or [] if b[0] in a]
        if not rows:
            continue
        x = {"dates": [b[0] for b, _ in rows], "raw_high": [b[2] for b, _ in rows], "raw_low": [b[3] for b, _ in rows],
             "raw_close": [b[4] for b, _ in rows], "raw_volume": [b[5] for b, _ in rows],
             "close": [c for _, (o, c, v) in rows], "volume": [v for _, (o, c, v) in rows]}
        x["segment"] = segments(x["dates"], x["close"], sessions)
        out[s] = x
    return out


def purchases(z):
    """A quarter's open-market purchase lines (code P, acquired, not a 10b5-1 plan line by bot/form4.py's rule) on
    original Form 4s by an officer or director -> [{"filed", "issuer", "symbol", "owner", "trade_date", "shares",
    "price", "value"}]; the owner is the filing's first officer/director."""
    out = []
    for f in pi.read_quarter(z, {}).values():
        if (f["form"] or "").strip() != "4":
            continue
        who = next((o["cik"] for o in f["owners"] if o["od"]), None)
        sym = normalise_symbol(f["symbol"])
        if not who or not f["filed"]:
            continue
        plan_notes = {k for k, v in f["notes"].items() if form4.PLAN.search(v) and not form4.NOT_PLAN.search(v)}
        for x in f["lines"]:
            if bool(x["refs"] & plan_notes) or (f["box"] and not plan_notes):
                continue
            if x["date"] and x["value"]:
                out.append({"filed": f["filed"], "issuer": f["issuer"], "symbol": sym, "owner": who,
                            "trade_date": x["date"], "shares": x["shares"], "price": x["price"], "value": x["value"]})
    return out
