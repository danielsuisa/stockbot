"""Short-interest inputs for the squeeze screener (free, no key): FINRA consolidated short interest and SEC shares
outstanding, with the point-in-time rules the live screener and its backtest share
(docs/superpowers/specs/2026-09-27-squeeze-screener-design.md, section 5)."""
import bisect
import datetime as dt
import json

from bot import common

FINRA = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
FRAME = "https://data.sec.gov/api/xbrl/frames/dei/EntityCommonStockSharesOutstanding/shares/CY{y}Q{q}I.json"
CLASSES = ("NNM", "SC", "NYSE", "AMEX")  # Nasdaq Global (Select), Nasdaq Capital, NYSE, NYSE American
PAGE = 5000  # FINRA's row limit per request
SI_LAG = 8  # sessions after settlement before a report may be used (FINRA publishes after business day 7)
SHARES_LAG = 15  # days after a dei fact's date (the filing's cover date) before it may be used
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
    """{cik: (fact date ISO, shares)} from one SEC frame ({} when it is not published)."""
    d = common.get_json(FRAME.format(y=y, q=q)) or {}
    return {int(r["cik"]): (r["end"], int(r["val"])) for r in d.get("data", []) if r.get("val")}


def shares_index(frames):
    """Frames -> {cik: [(fact date ISO, shares), ...] ascending}; the same fact in several frames counts once."""
    idx = {}
    for fr in frames:
        for cik, fact in fr.items():
            idx.setdefault(cik, set()).add(tuple(fact))
    return {cik: sorted(s) for cik, s in idx.items()}


def shares_at(idx, cik, day):
    """Shares outstanding usable on session `day` (ISO): the latest fact dated at least SHARES_LAG days earlier and
    at most SHARES_MAX_AGE days old; None when there is none or it is below MIN_SHARES (a scale error)."""
    facts = idx.get(cik)
    if not facts:
        return None
    d = dt.date.fromisoformat(day)
    i = bisect.bisect_right(facts, ((d - dt.timedelta(SHARES_LAG)).isoformat(), float("inf"))) - 1
    if i < 0:
        return None
    end, val = facts[i]
    return val if val >= MIN_SHARES and (d - dt.date.fromisoformat(end)).days <= SHARES_MAX_AGE else None
