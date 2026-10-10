"""Phase A readers for the recommendation algorithm (docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md
section 7 A; plan docs/superpowers/plans/2026-10-10-recommendation-algorithm-phase-a.md). Measurement only: nothing
here computes a return of a ticket or a stock.

- SEC Insider Transactions Data Sets: the symbols on Form 3/4/5 filings (the universe's insider test, CIK -> symbol)
  and S1's open-market purchases.
- EDGAR submissions.zip: 8-K item 2.02 acceptance times and the reaction session.
- Alpaca daily bars, raw and split-adjusted, cut into segments by the spec's bad-price rule."""
import array
import bisect
import collections
import datetime as dt
import hashlib
import json
import os
import re
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

from bot import alpaca, common, form4, probe_insider as pi

FORMS = {"3", "4", "5", "3/A", "4/A", "5/A"}
SUBMISSIONS = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
_SYMBOL = re.compile(r"[A-Z]{1,5}(\.[A-Z])?")


# ---------- insider data sets ----------
def normalise_symbol(raw):
    """A trading symbol as filed -> Alpaca's spelling ("brk-b" -> "BRK.B"; the first of a list), or None."""
    s = (raw or "").strip().upper()
    s = re.split(r"[,;\s]+", s)[0] if s else ""
    s = s.replace("-", ".").replace("/", ".")
    return s if _SYMBOL.fullmatch(s) and s not in ("NONE", "NA", "N.A") else None


def symbol_filings(z, stats=None):
    """Every Form 3/4/5 (and amendment) of a quarter's zip -> [(filing date ISO, issuer CIK, symbol)]; filings whose
    symbol is unusable are left out and counted in stats["unusable symbol"] when a dict is given."""
    _, rows = pi.table(z, "SUBMISSION")
    out = []
    for r in rows:
        if (r.get("DOCUMENT_TYPE") or "").strip() not in FORMS:
            continue
        sym, filed = normalise_symbol(r.get("ISSUERTRADINGSYMBOL")), pi.day(r.get("FILING_DATE"))
        if sym and filed:
            out.append((filed, _cik(r.get("ISSUERCIK") or ""), sym))  # "0000320193" -> "320193"
        elif stats is not None:
            stats["unusable symbol"] = stats.get("unusable symbol", 0) + 1
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
    for _ in range({"UTC": 1, "double": 2}.get(tz, 0)):  # "double": shifted UTC -> New York twice (2026 sample)
        t = t.replace(tzinfo=dt.timezone.utc).astimezone(alpaca.NY).replace(tzinfo=None)
    return t.strftime("%Y-%m-%dT%H:%M")


def earnings_releases(zip_path, ciks, start, end, forms=("8-K",)):
    """Filings of the given form types (8-K only by default: an 8-K/A is an amendment, not a release; spec revision 6)
    whose items include 2.02, for the given issuer CIKs, filed from `start` to `end` ->
    {CIK: sorted [(accession number, filing date)]}, from each issuer's recent filings and every older page in
    submissions.zip (read in place). The acceptance time comes from the EDGAR index page (release_times): the zip's
    acceptanceDateTime is not consistent across filers (phase A check, 2026-10-10)."""
    want = {_cik(c) for c in ciks}
    out = collections.defaultdict(set)
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            m = re.fullmatch(r"CIK(\d{10})(-submissions-\d+)?\.json", name.rsplit("/", 1)[-1])
            if not m or _cik(m.group(1)) not in want:
                continue
            d = json.loads(z.read(name))
            cols = d if m.group(2) else (d.get("filings") or {}).get("recent") or {}
            for form, items, acc, filed in zip(cols.get("form", []), cols.get("items", []),
                                               cols.get("accessionNumber", []), cols.get("filingDate", [])):
                if form in forms and "2.02" in re.split(r"[,\s]+", items or "") and start <= filed <= end:
                    out[_cik(m.group(1))].add((acc, filed))
    return {c: sorted(v) for c, v in out.items()}


def release_times(found, workers=8):
    """Each filing's acceptance time from its EDGAR index page (Eastern time; common.fetch keeps SEC's 8 requests a
    second) -> ({CIK: sorted "YYYY-MM-DDTHH:MM"}, the number of pages that could not be read: counted, never
    guessed)."""
    jobs = [(c, a) for c, v in found.items() for a, _ in v]
    with ThreadPoolExecutor(workers) as ex:
        got = list(ex.map(lambda j: index_accepted(*j), jobs))
    out, missing = collections.defaultdict(list), 0
    for (c, _), t in zip(jobs, got):
        if t:
            out[c].append(t)
        else:
            missing += 1
    return {c: sorted(v) for c, v in out.items()}, missing


class Releases:
    """Item 2.02 releases by symbol, point in time (a CIK's release goes to the symbols it filed a Form 3/4/5 under in
    the 365 days before the filing date), with each acceptance time read from its EDGAR index page only when a rule
    needs it (cached; unreadable pages counted, never guessed). The reaction session of a release is its filing date or
    the next session, so a time matters only where those two give different answers."""

    def __init__(self, found, filings, days=365):
        by_cik = collections.defaultdict(list)
        for filed, cik, sym in filings:
            by_cik[_cik(cik)].append((filed, sym))
        for v in by_cik.values():
            v.sort()
        self.by_symbol = collections.defaultdict(list)
        for cik, items in found.items():
            rows = by_cik.get(_cik(cik), [])
            dates = [d for d, _ in rows]
            for acc, filed in items:
                lo = (dt.date.fromisoformat(filed) - dt.timedelta(days)).isoformat()
                for sym in {s for _, s in rows[bisect.bisect_left(dates, lo):bisect.bisect_right(dates, filed)]}:
                    self.by_symbol[sym].append((filed, _cik(cik), acc))
        for v in self.by_symbol.values():
            v.sort()
        self.times, self.missing = {}, 0

    def filing_dates(self, sym):
        return [f for f, _, _ in self.by_symbol.get(sym, ())]

    def time(self, cik, acc):
        if acc not in self.times:
            self.times[acc] = index_accepted(cik, acc)
            self.missing += self.times[acc] is None
        return self.times[acc]

    def reacts_on(self, sym, sessions, days):
        """Is any of the symbol's reaction sessions in `days` (a set of sessions)? Times are fetched only for
        releases whose filing date or next session is in `days`."""
        for filed, cik, acc in self.by_symbol.get(sym, ()):
            k = bisect.bisect_left(sessions, filed)
            cands = {sessions[j] for j in (k, k + 1) if j < len(sessions)}
            if not cands & days:
                continue
            t = self.time(cik, acc)
            if t and reaction_session(t, sessions) in days:
                return True
        return False


def release_symbols(releases, filings, days=365):
    """Point-in-time CIK -> symbol: each item 2.02 release (New York time) of a CIK goes to the symbols that CIK filed a
    Form 3/4/5 under in the `days` before it (a symbol can pass from one issuer to another) -> {symbol: [times]}.
    filings: symbol_filings rows."""
    by_cik = collections.defaultdict(list)
    for filed, cik, sym in filings:
        by_cik[_cik(cik)].append((filed, sym))
    for v in by_cik.values():
        v.sort()
    out = collections.defaultdict(list)
    for cik, times in releases.items():
        rows = by_cik.get(_cik(cik), [])
        dates = [d for d, _ in rows]
        for t in times:
            lo = (dt.date.fromisoformat(t[:10]) - dt.timedelta(days)).isoformat()
            for _, sym in rows[bisect.bisect_left(dates, lo):bisect.bisect_right(dates, t[:10])]:
                if t not in out[sym]:
                    out[sym].append(t)
    return {s: sorted(v) for s, v in out.items()}


def accepted_pairs(zip_path, cik, years):
    """The time-zone check's input: for each given year, the CIK's first item 2.02 8-K of that year in
    submissions.zip (recent filings and older pages) -> [(accession number, the raw acceptanceDateTime field)]."""
    rows = []
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if re.fullmatch(rf"CIK{int(cik):010d}(-submissions-\d+)?\.json",
                                                          n.rsplit("/", 1)[-1])]
        for name in names:
            d = json.loads(z.read(name))
            r = d if "-submissions-" in name else (d.get("filings") or {}).get("recent") or {}
            rows += [(t, a) for f, i, a, t in zip(r.get("form", []), r.get("items", []), r.get("accessionNumber", []),
                                                  r.get("acceptanceDateTime", []))
                     if f == "8-K" and "2.02" in re.split(r"[,\s]+", i or "") and t]
    rows.sort()
    out = []
    for y in years:
        hit = next(((a, t) for t, a in rows if t[:4] == str(y)), None)
        if hit:
            out.append(hit)
    return out


def index_accepted(cik, accession):
    """EDGAR's filing index page -> its "Accepted" time as printed there ("YYYY-MM-DDTHH:MM", Eastern time), or None
    when the page cannot be read."""
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{accession}-index.htm"
    try:
        page = (common.fetch(url, tries=3) or b"").decode("utf-8", "replace")
    except Exception:  # a failed page is counted by release_times, never guessed
        return None
    m = re.search(r"Accepted</div>\s*<div class=\"info\">(\d{4}-\d\d-\d\d) (\d\d:\d\d)", page)
    return f"{m.group(1)}T{m.group(2)}" if m else None


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
        f = lambda vals: array.array("d", vals)  # noqa: E731  (compact: ~15k symbols x ~2,600 days)
        x = {"dates": [b[0] for b, _ in rows], "raw_high": f(b[2] for b, _ in rows), "raw_low": f(b[3] for b, _ in rows),
             "raw_close": f(b[4] for b, _ in rows), "raw_volume": f(b[5] for b, _ in rows),
             "close": f(c for _, (o, c, v) in rows), "volume": f(v for _, (o, c, v) in rows)}
        x["segment"] = array.array("i", segments(x["dates"], x["close"], sessions))
        out[s] = x
        raw.pop(s, None)
        adj.pop(s, None)
    return out


def purchases(z):
    """A quarter's open-market purchase lines (code P, acquired, not a 10b5-1 plan line by bot/form4.py's rule) on
    original Form 4s by an officer or director -> [{"filed", "issuer", "symbol", "owner", "trade_date", "shares",
    "price", "value"}]; the owner is the filing's first officer/director."""
    out = []
    for f in pi.read_quarter(z, {}).values():
        if (f["form"] or "").strip() != "4":
            continue
        who = next((_cik(o["cik"]) for o in f["owners"] if o["od"]), None)  # CIKs as filed may be zero-padded
        sym = normalise_symbol(f["symbol"])
        if not who or not f["filed"]:
            continue
        plan_notes = {k for k, v in f["notes"].items() if form4.PLAN.search(v) and not form4.NOT_PLAN.search(v)}
        for x in f["lines"]:
            if bool(x["refs"] & plan_notes) or (f["box"] and not plan_notes):
                continue
            if x["date"] and x["value"]:
                out.append({"filed": f["filed"], "issuer": _cik(f["issuer"]), "symbol": sym, "owner": who,
                            "trade_date": x["date"], "shares": x["shares"], "price": x["price"], "value": x["value"]})
    return out


def transaction_prices(z):
    """A quarter's Form 4 / Form 5 non-derivative transaction prices (phase A.1: the price P of a universe-like
    stock-month) -> [(filed, symbol, trade date, accession, price)]: any transaction code, price > 0 only (grants and
    gifts carry $0, not a price), original forms only (amendments are ignored, as for S1), a usable symbol."""
    lines = collections.defaultdict(list)
    for r in pi.table(z, "NONDERIV_TRANS")[1]:
        px = pi.num(r.get("TRANS_PRICEPERSHARE"))
        if px and px > 0:
            lines[r["ACCESSION_NUMBER"]].append((pi.day(r.get("TRANS_DATE")), px))
    out = []
    for r in pi.table(z, "SUBMISSION")[1]:
        acc = r["ACCESSION_NUMBER"]
        sym = normalise_symbol(r.get("ISSUERTRADINGSYMBOL"))
        filed = pi.day(r.get("FILING_DATE"))
        if acc in lines and (r.get("DOCUMENT_TYPE") or "").strip() in ("4", "5") and sym and filed:
            out += [(filed, sym, d, acc, px) for d, px in lines[acc]]
    return out
