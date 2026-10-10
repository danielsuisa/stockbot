"""Phase A readers for the recommendation algorithm (docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md
section 7 A; plan docs/superpowers/plans/2026-10-10-recommendation-algorithm-phase-a.md). Measurement only: nothing
here computes a return of a ticket or a stock.

- SEC Insider Transactions Data Sets: the symbols on Form 3/4/5 filings (the universe's insider test, CIK -> symbol)
  and S1's open-market purchases.
- EDGAR submissions.zip: 8-K item 2.02 acceptance times and the reaction session.
- Alpaca daily bars, raw and split-adjusted, cut into segments by the spec's bad-price rule."""
import collections
import re

from bot import form4, probe_insider as pi

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
