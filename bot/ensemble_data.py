"""The ensemble's inputs (docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md sections 2 and 4.2):
insider filings and purchases, insider market prices keyed by CIK, daily bars (raw and split-adjusted), item 2.02
releases with their acceptance times, the calendar. Inputs only: nothing here holds or computes an outcome, and only
inputs are ever cached."""
import bisect
import collections

from bot import alpaca, common, probe_ensemble as pe, probe_ensemble_data as pd, probe_insider as pi

INPUT_FIELDS = ("sessions", "cal", "rows", "filed", "ciks", "of_cik", "by_sym", "purchases", "prices_by_cik", "series",
                "releases", "rename_pool")
SIGNAL_START, SIGNAL_END, BARS_FROM = pe.SIGNAL_START, pe.SIGNAL_END, pe.BARS_FROM


class Inputs:
    """The inputs of one run (fields: INPUT_FIELDS)."""

    def __init__(self, **fields):
        assert set(fields) == set(INPUT_FIELDS), sorted(set(fields) ^ set(INPUT_FIELDS))
        vars(self).update(fields)


def market_prices(z):
    """A quarter's insider transactions at a market price, by issuer CIK (spec 4.2, C12) -> [(filed, cik, trade
    date, accession, price)]: codes P, S, F, price > 0, original Forms 4 and 5; the symbol is not needed."""
    lines = collections.defaultdict(list)
    for r in pi.table(z, "NONDERIV_TRANS")[1]:
        px = pi.num(r.get("TRANS_PRICEPERSHARE"))
        if (r.get("TRANS_CODE") or "").strip() in pd.MARKET_CODES and px and px > 0:
            lines[r["ACCESSION_NUMBER"]].append((pi.day(r.get("TRANS_DATE")) or "", px))
    out = []
    for r in pi.table(z, "SUBMISSION")[1]:
        acc, filed = r["ACCESSION_NUMBER"], pi.day(r.get("FILING_DATE"))
        if acc in lines and (r.get("DOCUMENT_TYPE") or "").strip() in ("4", "5") and filed and r.get("ISSUERCIK"):
            out += [(filed, pd._cik(r["ISSUERCIK"]), d, acc, px) for d, px in lines[acc]]
    return out


def make_inputs(sessions, cal, rows, purchases, prices_by_cik, series, releases):
    """Index the raw inputs: filings by symbol, CIKs per symbol and symbols per CIK (CIKs unpadded), each symbol's
    filings with their CIK in date order, market prices per CIK in date order, and the symbols the rename rule can
    ever apply to (a CIK with another symbol that has bars)."""
    rows = [(f, pd._cik(c), s) for f, c, s in rows]
    filed, ciks = pd.index(rows)
    of_cik, by_sym = collections.defaultdict(set), collections.defaultdict(list)
    for f, c, s in rows:
        of_cik[c].add(s)
        by_sym[s].append((f, c))
    for v in by_sym.values():
        v.sort()
    prices = {pd._cik(c): sorted(v) for c, v in prices_by_cik.items()}
    pool = {s for s in filed if any(o != s and o in series for c in ciks.get(s, ()) for o in of_cik[c])}
    return Inputs(sessions=list(sessions), cal=dict(cal), rows=rows, filed=filed, ciks=ciks, of_cik=dict(of_cik),
                  by_sym=dict(by_sym), purchases=[{**x, "issuer": pd._cik(x["issuer"])} for x in purchases],
                  prices_by_cik=prices, series=series, releases=releases, rename_pool=pool)


def load(today, bars_end=SIGNAL_END, symbols=None):
    """Every input from the network or its cache. `bars_end` reaches past SIGNAL_END only for outcomes (spec 4.2,
    C17); the universe, the detectors and the signal days end on SIGNAL_END."""
    rows, buys, prices = [], [], collections.defaultdict(list)
    for y in range(2015, today.year + 1):
        for q in range(1, 5):
            z = pi.quarter_zip(y, q)
            if z is None:
                continue
            rows += pd.symbol_filings(z, {})
            if (y, q) >= (2015, 4):
                buys += pd.purchases(z)
            for filed, cik, trade, acc, px in market_prices(z):
                prices[cik].append((filed, trade, acc, px))
    cal_all = alpaca.calendar("2015-12-01", bars_end)
    sessions = sorted(d for d in cal_all if BARS_FROM <= d <= bars_end)
    filed, ciks = pd.index(rows)
    wanted = sorted(filed) if symbols is None else sorted(symbols)
    series = pd.load_series(wanted, BARS_FROM, bars_end, sessions)
    zip_path = pd.download(pd.SUBMISSIONS, common.ROOT / ".cache" / "sec" / "submissions.zip")
    found = pd.earnings_releases(zip_path, {pd._cik(c) for s in wanted for c in ciks.get(s, ())}, "2016-12-01",
                                 SIGNAL_END)
    return make_inputs(sessions, {d: cal_all[d] for d in sessions}, rows, buys, prices, series,
                       pd.Releases(found, rows))


def session_on_or_after(sessions, day):
    k = bisect.bisect_left(sessions, day)
    return sessions[k] if k < len(sessions) else None
