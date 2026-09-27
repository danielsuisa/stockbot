"""Short-interest inputs for the squeeze screener (free, no key): FINRA consolidated short interest and SEC shares
outstanding, with the point-in-time rules the live screener and its backtest share
(docs/superpowers/specs/2026-09-27-squeeze-screener-design.md, section 5)."""
import bisect
import datetime as dt
import json

from bot import common

FINRA = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
FRAME = "https://data.sec.gov/api/xbrl/frames/dei/EntityCommonStockSharesOutstanding/shares/CY{y}Q{q}I.json"
FULL_INDEX = "https://www.sec.gov/Archives/edgar/full-index/{y}/QTR{q}/xbrl.idx"
CLASSES = ("NNM", "SC", "NYSE", "AMEX")  # Nasdaq Global (Select), Nasdaq Capital, NYSE, NYSE American
PAGE = 5000  # FINRA's row limit per request
SI_LAG = 8  # sessions after settlement before a report may be used (FINRA publishes after business day 7)
SHARES_FALLBACK = 140  # days after a fact's date when its filing date is unknown (20-F filers run ~136 days late)
SHARES_MAX_AGE = 400  # days: an older figure (the filer went quiet) is not used
MIN_SHARES = 100_000  # a smaller SEC figure is a unit/scale error (e.g. 100 shares), not a listed company


def norm(symbol):
    """FINRA symbol trimmed and upper-cased ('.' or '/' -> '-'; FINRA itself writes class shares as BRKB)."""
    return symbol.strip().upper().replace(".", "-").replace("/", "-")


def to_sec(symbol, known):
    """FINRA symbol -> its ticker in `known` (SEC / Yahoo spelling), or None. FINRA writes class shares without a
    separator, so BRKB -> BRK-B when that is a known ticker."""
    if symbol in known:
        return symbol
    alt = f"{symbol[:-1]}-{symbol[-1]}" if len(symbol) > 1 else None
    return alt if alt in known else None


def remap(rep, known):
    """A report {FINRA symbol: row} -> {known ticker: row}; a symbol without a known ticker keeps its FINRA name
    (it matches nothing downstream but still counts in coverage)."""
    return {to_sec(sym, known) or sym: row for sym, row in rep.items()}


def _post(body):
    """One FINRA API query -> rows ([] when FINRA answers 204 No Content)."""
    raw = common.fetch(FINRA, data=json.dumps(body).encode(),
                       headers={"Content-Type": "application/json", "Accept": "application/json"})
    return json.loads(raw) if raw else []


def slim(r):
    """A FINRA row -> what the screener uses: short shares, days to cover (0.0 when FINRA has none), change since the
    prior report as a fraction (None when missing), FINRA's average daily volume, market class, settlement date."""
    chg = r.get("changePercent")
    return {"si": r.get("currentShortPositionQuantity") or 0, "dtc": r.get("daysToCoverQuantity") or 0.0,
            "chg": None if chg is None else chg / 100, "adv": r.get("averageDailyVolumeQuantity"),
            "cls": r.get("marketClassCode"), "date": r.get("settlementDate")}


def settlement_dates(symbol="AAPL"):
    """Every settlement date (ISO, ascending), read from one always-listed symbol's history."""
    rows = _post({"limit": PAGE, "fields": ["settlementDate"],
                  "compareFilters": [{"compareType": "EQUAL", "fieldName": "symbolCode", "fieldValue": symbol}]})
    return sorted({r["settlementDate"] for r in rows})


def report(date):
    """{symbol (SEC spelling): slim row} for one settlement date, exchange-listed classes only (paged)."""
    out, offset = {}, 0
    while True:
        rows = _post({"limit": PAGE, "offset": offset,
                      "compareFilters": [{"compareType": "EQUAL", "fieldName": "settlementDate", "fieldValue": date}],
                      "domainFilters": [{"fieldName": "marketClassCode", "values": list(CLASSES)}]})
        for r in rows:
            out[norm(r["symbolCode"])] = slim(r)
        if len(rows) < PAGE:
            return out
        offset += PAGE


def usable_from(settle, cal):
    """First session (ISO) on which a report settled on `settle` may be used: the SI_LAG-th session after it in the
    ascending session list `cal`. A settlement date that is not a session counts from the next session (later, never
    earlier). None when `cal` does not reach that far yet."""
    j = bisect.bisect_left(cal, settle) + SI_LAG
    return cal[j] if j < len(cal) else None


def frame(y, q):
    """{cik: (fact date ISO, shares, accession)} from one SEC frame ({} when it is not published)."""
    d = common.get_json(FRAME.format(y=y, q=q)) or {}
    return {int(r["cik"]): (r["end"], int(r["val"]), r.get("accn")) for r in d.get("data", []) if r.get("val")}


def filings(y, q):
    """{accession: date filed} for every XBRL filing of one quarter, from EDGAR's full index ({} if unpublished)."""
    out = {}
    for line in (common.fetch(FULL_INDEX.format(y=y, q=q)) or b"").decode("latin-1").splitlines():
        parts = line.split("|")
        if len(parts) == 5 and parts[4].endswith(".txt"):
            out[parts[4].rsplit("/", 1)[-1][:-4]] = parts[3]
    return out


def shares_index(frames, filed=None):
    """Frames -> {cik: [(usable from ISO, fact date ISO, shares), ...] ascending}; a fact in several frames counts
    once. With `filed` ({accession: date filed}; the backtest) a fact is usable the day after its filing, or
    SHARES_FALLBACK days after its date when the filing is unknown; without it (live: whatever the API serves is
    already public) a fact is usable from its date."""
    idx = {}
    for fr in frames:
        for cik, (end, val, accn) in fr.items():
            if filed is None:
                use = end
            elif accn in filed:
                use = (dt.date.fromisoformat(filed[accn]) + dt.timedelta(1)).isoformat()
            else:
                use = (dt.date.fromisoformat(end) + dt.timedelta(SHARES_FALLBACK)).isoformat()
            idx.setdefault(cik, set()).add((use, end, val))
    return {cik: sorted(s) for cik, s in idx.items()}


def shares_at(idx, cik, day):
    """Shares outstanding usable on session `day` (ISO): the latest fact usable by then, if it is at most
    SHARES_MAX_AGE days old and not below MIN_SHARES (a scale error); else None."""
    facts = idx.get(cik)
    if not facts:
        return None
    i = bisect.bisect_right(facts, (day, "~", float("inf"))) - 1
    if i < 0:
        return None
    _, end, val = facts[i]
    age = (dt.date.fromisoformat(day) - dt.date.fromisoformat(end)).days
    return val if val >= MIN_SHARES and age <= SHARES_MAX_AGE else None
