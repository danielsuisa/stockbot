"""Phase A.1 of the recommendation algorithm (docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md
revision 6, section 7 A.1; plan docs/superpowers/plans/2026-10-10-recommendation-algorithm-phase-a1-claude.md):
item 2.02 coverage with 8-K only, universe-like stock-months defined without bars (FINRA average daily share volume x
the latest Form 4/5 transaction price), how many the rename rule recovers, f and b = f x 1 R.

Counts and coverage only. No return of a ticket or a stock is computed; the only files written are caches of raw
data (FINRA reports, Alpaca bars, SEC zips)."""
import bisect
import calendar
import collections
import datetime as dt

from bot import alpaca, common, shorts

CACHE = common.ROOT / ".cache" / "finra"
MONTHS = ("2018-01", "2026-03")
MIN_PRICE, MIN_DOLLAR_VOLUME, WINDOW_DAYS = 5.0, 10_000_000.0, 365
RENAME_SHARE, B_MAX = 1 / 3, 0.03  # spec section 2 (the rename rule) and section 6.4 (b)


# ---------- FINRA ----------
def finra_report(date):
    """FINRA's consolidated short-interest report settled on `date` (exchange-listed classes) -> {FINRA symbol: slim
    row}, cached as gzip JSON."""
    path = CACHE / f"{date}.json.gz"
    got = alpaca._read(path)
    if got is None:
        got = shorts.report(date)
        alpaca._write(path, got)
    return got


def finra_spellings(symbol):
    """An insider-data symbol (BRK.B) -> its two FINRA spellings: shorts.norm's (BRK-B) and FINRA's own class-share
    form without a separator (BRKB)."""
    return symbol.replace(".", "-"), symbol.replace(".", "")


def finra_row(report, symbol):
    """The report's row for an insider-data symbol, or None."""
    return next((report[s] for s in finra_spellings(symbol) if s in report), None)


def volume_check(reports):
    """{date: report} -> how many rows carry FINRA's average daily share volume, and the reports where none does (the
    spec's stop condition: no volume field)."""
    rows = [r for rep in reports.values() for r in rep.values()]
    return {"reports": len(reports), "rows": len(rows), "rows with volume": sum(1 for r in rows if r.get("adv")),
            "reports without any volume": sorted(d for d, rep in reports.items()
                                                 if not any(r.get("adv") for r in rep.values()))}


# ---------- universe-like stock-months, the rename rule, f and b ----------
def month_ends(first, last):
    """["YYYY-MM" from `first` to `last`] -> [(month, its last calendar day ISO)]."""
    y, m = map(int, first.split("-"))
    out = []
    while f"{y:04d}-{m:02d}" <= last:
        out.append((f"{y:04d}-{m:02d}", dt.date(y, m, calendar.monthrange(y, m)[1]).isoformat()))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def latest_before(items, end):
    """The last of `items` (tuples sorted by their first element, an ISO date) dated on or before `end`, or None."""
    k = bisect.bisect_right(items, end, key=lambda t: t[0])
    return items[k - 1] if k else None


def _window(month_end):
    return (dt.date.fromisoformat(month_end) - dt.timedelta(WINDOW_DAYS)).isoformat()


def universe_like(month_end, symbols_filed, prices, finra, stats=None):
    """Spec section 7 A.1 item 2 for the month ending `month_end` -> {symbol: {"V", "P"}}: an issuer symbol on a Form
    3/4/5 filed in the 365 days through the month's end ({symbol: sorted filing dates}); in the month's FINRA report
    (`finra`, None when there is none) with average daily share volume V; the latest Form 4/5 transaction price P of
    those 365 days ({symbol: sorted [(filed, trade date, accession, price)]}: latest by filing date, then trade date,
    then accession) >= $5; V x P >= $10M. `stats` counts the FINRA gaps."""
    stats = {} if stats is None else stats
    lo, out = _window(month_end), {}
    if finra is None:
        return out
    for sym, dates in symbols_filed.items():
        k = bisect.bisect_left(dates, lo)
        if k == len(dates) or dates[k] > month_end:
            continue
        row = finra_row(finra, sym)
        if row is None:
            stats["not in the FINRA report"] = stats.get("not in the FINRA report", 0) + 1
            continue
        if not row.get("adv"):
            stats["FINRA row without volume"] = stats.get("FINRA row without volume", 0) + 1
            continue
        last = latest_before(prices.get(sym, []), month_end)
        if last is None or last[0] < lo or last[3] < MIN_PRICE:
            continue
        if row["adv"] * last[3] >= MIN_DOLLAR_VOLUME:
            out[sym] = {"V": row["adv"], "P": last[3]}
    return out


def cik_at(filings_by_sym, sym, month_end):
    """The CIK of the symbol's latest Form 3/4/5 filing in the 365 days through the month's end ({symbol: sorted
    [(filed, cik)]}), or None."""
    last = latest_before(filings_by_sym.get(sym, []), month_end)
    return last[1] if last and last[0] >= _window(month_end) else None


def recovered(sym, month, cik, symbols_of_cik, bar_months):
    """The rename rule (spec section 2) for a symbol without bars in `month`: its CIK has exactly one other filed
    symbol with bars ({cik: {symbols}}, {symbol: {months with a bar}}), and that symbol has a bar in the month -> that
    symbol, else None."""
    others = [s for s in symbols_of_cik.get(cik, ()) if s != sym and bar_months.get(s)]
    return others[0] if len(others) == 1 and month in bar_months[others[0]] else None


CELLS = ("universe-like", "with bars", "without bars", "recovered by the rename rule",
         "recovered, duplicating a stock-month with bars")


def survivorship_counts(per_month):
    """{month: {"like": set, "with bars": set, "recovered": {symbol: other}, "duplicates": int}} -> {"per_year": {year:
    cells}, "overall": cells}."""
    per = collections.defaultdict(lambda: dict.fromkeys(CELLS, 0))
    for month, m in per_month.items():
        for key in (month[:4], "overall"):
            c = per[key]
            c["universe-like"] += len(m["like"])
            c["with bars"] += len(m["with bars"])
            c["without bars"] += len(m["like"]) - len(m["with bars"])
            c["recovered by the rename rule"] += len(m["recovered"])
            c["recovered, duplicating a stock-month with bars"] += m["duplicates"]
    overall = per.pop("overall", dict.fromkeys(CELLS, 0))
    return {"per_year": {y: per[y] for y in sorted(per)}, "overall": overall}


def decide(overall):
    """The rename rule's one-third condition, f under both readings and b = f x 1 R against 0.03 R. b uses the rule as
    decided: when the rule is not used nothing counts as recovered (spec section 7 A.1 item 3)."""
    n, without, rec = overall["universe-like"], overall["without bars"], overall["recovered by the rename rule"]
    used = 3 * rec >= without
    f = (without - (rec if used else 0)) / n if n else None
    return {"rename rule used": used, "f (rule as decided)": f,
            "f (literal, recovered always subtracted)": (without - rec) / n if n else None,
            "b_R": f, "b within 0.03 R": f is not None and f <= B_MAX}
